const express    = require('express');
const cors       = require('cors');
const session    = require('express-session');
const { Issuer, generators } = require('openid-client');
require('dotenv').config();
const { v4: uuidv4 } = require('uuid');
const nodemailer = require('nodemailer');
const crypto     = require('crypto');
const { createClient: createRedisClient } = require('redis');
const RedisStore = require('connect-redis').default;

const app = express();
app.set("trust proxy", 1);
app.use(express.json());

function inviteExpiresAt(days = 7) {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

// ── Supabase ─────────────────────────────────────────────────────────────────

const { createClient } = require('@supabase/supabase-js');
const supabaseAdmin = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

// ── Redis ─────────────────────────────────────────────────────────────────────

const redisClient = createRedisClient({ url: process.env.REDIS_URL });
redisClient.on('error', (err) => console.error('Redis error:', err));
redisClient.connect().catch(console.error);

// ── CORS ─────────────────────────────────────────────────────────────────────

app.use(cors({
  origin:      process.env.FRONTEND_URL,
  credentials: true,
}));

// ── Session ──────────────────────────────────────────────────────────────────

app.use(session({
  store: new RedisStore({ client: redisClient }),
  secret:            process.env.SESSION_SECRET,
  resave:            false,
  saveUninitialized: false,
  cookie: {
    secure:   true,
    sameSite: 'none',
    maxAge:   7 * 24 * 60 * 60 * 1000,
    httpOnly: true,
  },
}));

// ── OIDC client ──────────────────────────────────────────────────────────────

let client;
async function initializeClient() {
  const issuer = await Issuer.discover(process.env.COGNITO_ISSUER_URL);
  client = new issuer.Client({
    client_id:     process.env.CLIENT_ID,
    client_secret: process.env.CLIENT_SECRET,
    redirect_uris: [process.env.REDIRECT_URI],
    response_types: ['code'],
  });
}
initializeClient().catch(console.error);

// ── Middleware ────────────────────────────────────────────────────────────────

const checkClientReady = (req, res, next) => {
  if (!client) return res.status(503).send('Service is starting, please try again shortly.');
  next();
};

const checkAuth = (req, res, next) => {
  req.isAuthenticated = !!req.session.userInfo;
  next();
};

// ── Helper: extract invite token embedded in state ───────────────────────────

function splitState(rawState) {
  if (!rawState) return { baseState: '', inviteToken: null };
  const idx = rawState.indexOf('|inviteToken:');
  if (idx === -1) return { baseState: rawState, inviteToken: null };
  return {
    baseState:   rawState.slice(0, idx),
    inviteToken: rawState.slice(idx + '|inviteToken:'.length) || null,
  };
}

// ── Helper: process an invite token after successful login ───────────────────

async function processInviteAndRedirect(req, res, inviteToken, userInfo) {
  try {
    const { data: invite, error } = await supabaseAdmin
      .from('invite_tokens')
      .select('*, orgs(*)')
      .eq('token', inviteToken)
      .single();

    if (!error && invite && (!invite.expires_at || new Date(invite.expires_at) > new Date())) {
      await supabaseAdmin.from('org_members').upsert({
        org_id:   invite.org_id,
        user_sub: userInfo.sub,
        email:    userInfo.email,
        role:     'member',
      }, { onConflict: 'org_id,user_sub' });

      req.session.orgId   = invite.org_id;
      req.session.orgName = invite.orgs.name;
      req.session.mode    = 'org';
      req.session.memberships = [
        ...(req.session.memberships || []).filter(m => m.org_id !== invite.org_id),
        { org_id: invite.org_id, role: 'member', orgs: { id: invite.org_id, name: invite.orgs.name } },
      ];
    } else {
      console.warn('[invite] Invalid or expired token:', inviteToken, error?.message);
    }
  } catch (err) {
    console.error('[invite] processInviteAndRedirect error:', err.message);
  }

  return req.session.save(() => {
    res.redirect(`${process.env.FRONTEND_URL}/dashboard`);
  });
}

// ── Routes ────────────────────────────────────────────────────────────────────

app.get('/', (req, res) => {
  if (!req.session.tokens && !req.session.userInfo) {
    return res.json({ isAuthenticated: false });
  }
  res.json({
    isAuthenticated: true,
    user:         req.session.userInfo             || null,
    id_token:     req.session.tokens?.id_token     || null,
    access_token: req.session.tokens?.access_token || null,
    orgId:        req.session.orgId                || null,
    orgName:      req.session.orgName              || null,
    mode:         req.session.mode                 || null,
    memberships:  req.session.memberships          || [],
  });
});

// ── Login ─────────────────────────────────────────────────────────────────────

app.get('/login', checkClientReady, (req, res) => {
  const nonce        = generators.nonce();
  const baseState    = generators.state();
  const redirectPath = req.query.redirect || '';
  const inviteMatch  = redirectPath.match(/\/org\/join\/([^/?#]+)/);

  const fullState = inviteMatch
    ? `${baseState}|inviteToken:${inviteMatch[1]}`
    : baseState;

  // FIX: Store nonce and the BASE state (without the invite suffix) so
  // the callback can verify them correctly.
  req.session.nonce = nonce;
  req.session.state = baseState;

  req.session.save((err) => {
    if (err) {
      console.error('Session save error on /login:', err);
      return res.status(500).send('Session error');
    }

    const authUrl = client.authorizationUrl({
      scope: 'phone openid email',
      state: fullState,
      nonce,
    });

    res.redirect(authUrl);
  });
});

// ── Callback ──────────────────────────────────────────────────────────────────

app.get('/callback', checkClientReady, async (req, res) => {
  try {
    const rawState = req.query.state || '';
    const { baseState, inviteToken: stateInviteToken } = splitState(rawState);

    // FIX: Read the expected nonce and state strictly from the session.
    // Never fall back to the request value — that defeats the CSRF protection.
    const expectedNonce = req.session.nonce;
    const expectedState = req.session.state;

    // If the session lost the nonce/state (e.g. session not rehydrated),
    // redirect to login cleanly instead of throwing an unhandled error.
    if (!expectedNonce || !expectedState) {
      console.error('[callback] Missing nonce or state in session. Session may not have been rehydrated. Redirecting to login.');
      return res.redirect(`${process.env.FRONTEND_URL}/login?error=session_expired`);
    }

    // FIX: openid-client checks state against what we pass here.
    // We must pass the BASE state (without the invite suffix) because that
    // is what we stored in the session at login time.
    const params = client.callbackParams(req);

    // Temporarily override the state param so openid-client validates
    // against the base state we stored, not the full compound state.
    // (callbackParams reads req.query.state; we reconstruct params cleanly.)
    const callbackChecks = {
      nonce: expectedNonce,
      state: expectedState,
      // Pass the base state as the expected value; provide the base state
      // as the received value so the library comparison passes.
      // We do this by re-assigning params.state before validation.
    };

    // Replace the compound state in params with just the base portion
    // so openid-client's state comparison (params.state === checks.state) passes.
    params.state = baseState;

    const tokenSet = await client.callback(
      process.env.REDIRECT_URI,
      params,
      callbackChecks
    );

    const userInfo = await client.userinfo(tokenSet.access_token);

    req.session.userInfo = userInfo;
    req.session.tokens   = {
      access_token:  tokenSet.access_token,
      id_token:      tokenSet.id_token,
      refresh_token: tokenSet.refresh_token,
    };

    // FIX: Clear nonce and state AFTER successful validation.
    delete req.session.nonce;
    delete req.session.state;

    const { data: memberships } = await supabaseAdmin
      .from('org_members')
      .select('org_id, role, orgs(id, name)')
      .eq('user_sub', userInfo.sub);

    req.session.memberships = memberships || [];

    if (stateInviteToken) {
      return processInviteAndRedirect(req, res, stateInviteToken, userInfo);
    }

    const pendingToken = req.session.pendingInviteToken;
    if (pendingToken) {
      delete req.session.pendingInviteToken;
      return processInviteAndRedirect(req, res, pendingToken, userInfo);
    }

    if (memberships?.length === 1) {
      req.session.orgId   = memberships[0].org_id;
      req.session.orgName = memberships[0].orgs?.name;
      req.session.mode    = 'org';
    }

    req.session.save(() => {
      res.redirect(`${process.env.FRONTEND_URL}/dashboard`);
    });

  } catch (err) {
    console.error('Callback error:', err);
    res.redirect(`${process.env.FRONTEND_URL}?error=auth_failed`);
  }
});

// ── Refresh token ─────────────────────────────────────────────────────────────

app.post('/refresh', async (req, res) => {
  const refreshToken = req.session.tokens?.refresh_token;
  if (!refreshToken) {
    return res.status(401).json({ error: "No refresh token — please log in again." });
  }
  try {
    const tokenSet = await client.refresh(refreshToken);
    req.session.tokens = {
      access_token:  tokenSet.access_token,
      id_token:      tokenSet.id_token,
      refresh_token: tokenSet.refresh_token || refreshToken,
    };
    return req.session.save(() => res.json({ access_token: tokenSet.access_token }));
  } catch (err) {
    console.error("Token refresh failed:", err.message);
    return res.status(401).json({ error: "Session expired — please log in again." });
  }
});

// ── Org: join via invite link ─────────────────────────────────────────────────

app.get('/org/join/:token', async (req, res) => {
  const { token } = req.params;
  const user       = req.session.userInfo;

  if (!user) {
    req.session.pendingInviteToken = token;
    return req.session.save(() => {
      const loginUrl =
        `${process.env.FRONTEND_URL}/login` +
        `?redirect=${encodeURIComponent(`/org/join/${token}`)}`;
      res.redirect(loginUrl);
    });
  }

  const { data: invite, error } = await supabaseAdmin
    .from('invite_tokens')
    .select('*, orgs(*)')
    .eq('token', token)
    .single();

  if (error || !invite) {
    return res.redirect(`${process.env.FRONTEND_URL}/dashboard?error=invalid_invite`);
  }
  if (invite.expires_at && new Date(invite.expires_at) < new Date()) {
    return res.redirect(`${process.env.FRONTEND_URL}/dashboard?error=expired_invite`);
  }

  await supabaseAdmin.from('org_members').upsert({
    org_id:   invite.org_id,
    user_sub: user.sub,
    email:    user.email,
    role:     'member',
  }, { onConflict: 'org_id,user_sub' });

  req.session.orgId   = invite.org_id;
  req.session.orgName = invite.orgs.name;
  req.session.mode    = 'org';
  req.session.memberships = [
    ...(req.session.memberships || []).filter(m => m.org_id !== invite.org_id),
    { org_id: invite.org_id, role: 'member', orgs: { id: invite.org_id, name: invite.orgs.name } },
  ];

  req.session.save(() => {
    res.redirect(`${process.env.FRONTEND_URL}/dashboard`);
  });
});

// ── Org: memberships ──────────────────────────────────────────────────────────

app.get('/org/memberships', checkAuth, (req, res) => {
  res.json({
    memberships:    req.session.memberships  || [],
    currentOrgId:   req.session.orgId        || null,
    currentOrgName: req.session.orgName      || null,
  });
});

app.get('/org/me', checkAuth, (req, res) => {
  res.json({
    orgId:   req.session.orgId   || null,
    orgName: req.session.orgName || null,
  });
});

// ── Org: select workspace ─────────────────────────────────────────────────────

app.post('/org/select', checkAuth, async (req, res) => {
  const { orgId, mode } = req.body;

  if (mode === 'personal') {
    req.session.orgId   = null;
    req.session.orgName = null;
    req.session.mode    = 'personal';
    return req.session.save(() => res.json({ mode: 'personal' }));
  }

  if (mode === 'org' && orgId) {
    const { data } = await supabaseAdmin
      .from('org_members')
      .select('org_id, orgs(name)')
      .eq('user_sub', req.session.userInfo.sub)
      .eq('org_id', orgId)
      .single();

    if (!data) return res.status(403).json({ error: 'Not a member of this org' });

    req.session.orgId   = data.org_id;
    req.session.orgName = data.orgs?.name;
    req.session.mode    = 'org';
    return req.session.save(() =>
      res.json({ mode: 'org', orgId: data.org_id, orgName: data.orgs?.name })
    );
  }

  res.status(400).json({ error: 'Invalid selection' });
});

// ── Org: create ───────────────────────────────────────────────────────────────

app.post('/org/create', async (req, res) => {
  if (!req.session?.userInfo) {
    return res.status(401).json({ error: "not_authenticated" });
  }

  const { name } = req.body;
  const user     = req.session.userInfo;
  if (!name) return res.status(400).json({ error: 'Org name required' });

  try {
    const { data: org, error } = await supabaseAdmin
      .from('orgs')
      .insert({ name, created_by: user.sub })
      .select()
      .single();

    if (error) return res.status(500).json({ error: error.message });

    await supabaseAdmin.from('org_members').insert({
      org_id:   org.id,
      user_sub: user.sub,
      email:    user.email,
      role:     'admin',
    });

    const inviteToken = crypto.randomUUID();
    await supabaseAdmin.from('invite_tokens').insert({
      org_id:     org.id,
      token:      inviteToken,
      created_by: user.sub,
      expires_at: inviteExpiresAt(7),
    });

    req.session.orgId   = org.id;
    req.session.orgName = org.name;
    req.session.mode    = 'org';
    req.session.memberships = [
      ...(req.session.memberships || []),
      { org_id: org.id, role: 'admin', orgs: { id: org.id, name: org.name } },
    ];

    req.session.save(() => res.json({ org, inviteToken }));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Org: generate invite link ─────────────────────────────────────────────────

app.post('/org/invite', checkAuth, async (req, res) => {
  const orgId = req.session.orgId;
  if (!orgId) return res.status(400).json({ error: 'Not in an org' });

  const token = uuidv4();
  await supabaseAdmin.from('invite_tokens').insert({
    org_id:     orgId,
    token,
    created_by: req.session.userInfo.sub,
    expires_at: inviteExpiresAt(7),
  });
  res.json({ inviteUrl: `${process.env.FRONTEND_URL}/org/join/${token}` });
});

// ── Org: send invite email ────────────────────────────────────────────────────

app.post('/org/invite/email', checkAuth, async (req, res) => {
  const { email, inviteUrl } = req.body;
  const orgName    = req.session.orgName             || 'SecureStream';
  const senderName = req.session.userInfo?.given_name || 'A teammate';

  if (!email || !inviteUrl) {
    return res.status(400).json({ error: 'Email and inviteUrl required' });
  }
  if (!process.env.EMAIL_USER || !process.env.EMAIL_PASS) {
    return res.status(500).json({ error: 'Email not configured on server' });
  }

  try {
    const transporter = nodemailer.createTransport({
      service: 'gmail',
      auth: {
        user: process.env.EMAIL_USER,
        pass: process.env.EMAIL_PASS,
      },
    });

    const info = await transporter.sendMail({
      from:    `"SecureStream" <${process.env.EMAIL_USER}>`,
      to:      email,
      subject: `${senderName} invited you to join ${orgName} on SecureStream`,
      html: `
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
          <h2 style="font-size:20px;font-weight:700;margin:0 0 8px">You're invited</h2>
          <p style="color:#6b7280;font-size:14px;margin:0 0 24px">
            ${senderName} has invited you to join <strong>${orgName}</strong> on SecureStream.
          </p>
          <a href="${inviteUrl}"
            style="display:inline-block;background:#185FA5;color:white;padding:12px 24px;border-radius:10px;text-decoration:none;font-size:14px;font-weight:500">
            Accept invitation
          </a>
          <p style="color:#9ca3af;font-size:12px;margin-top:24px">Link expires in 7 days.</p>
        </div>
      `,
    });
    res.json({ success: true, messageId: info.messageId });
  } catch (err) {
    console.error('EMAIL SEND ERROR:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── Org: members ─────────────────────────────────────────────────────────────

app.get('/org/members', checkAuth, async (req, res) => {
  const orgId = req.session.orgId;
  if (!orgId) return res.json({ members: [] });
  try {
    const { data, error } = await supabaseAdmin
      .from('org_members')
      .select('user_sub, email, role, joined_at')
      .eq('org_id', orgId);
    if (error) return res.status(500).json({ error: error.message });
    res.json({ members: data || [] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/org/members/:user_sub', checkAuth, async (req, res) => {
  const orgId   = req.session.orgId;
  const { user_sub } = req.params;
  if (!orgId) return res.status(400).json({ error: 'Not in an org' });
  try {
    await supabaseAdmin
      .from('org_members')
      .delete()
      .eq('org_id', orgId)
      .eq('user_sub', user_sub);
    res.json({ removed: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.patch('/org/members/:user_sub/role', checkAuth, async (req, res) => {
  const orgId   = req.session.orgId;
  const { user_sub } = req.params;
  const { role }     = req.body;
  if (!orgId) return res.status(400).json({ error: 'Not in an org' });
  if (!['admin', 'member', 'viewer'].includes(role)) {
    return res.status(400).json({ error: 'Invalid role' });
  }
  try {
    const { data, error } = await supabaseAdmin
      .from('org_members')
      .update({ role })
      .eq('org_id', orgId)
      .eq('user_sub', user_sub)
      .select()
      .single();
    if (error) return res.status(500).json({ error: error.message });
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Org: online presence ──────────────────────────────────────────────────────

app.get('/org/online', checkAuth, async (req, res) => {
  const orgId = req.session.orgId;
  if (!orgId) return res.json({ online: [] });
  try {
    const twoMinsAgo = new Date(Date.now() - 2 * 60 * 1000).toISOString();
    const { data, error } = await supabaseAdmin
      .from('user_presence')
      .select('user_sub, email, last_seen')
      .eq('org_id', orgId)
      .gte('last_seen', twoMinsAgo);
    if (error) return res.status(500).json({ error: error.message });
    res.json({ online: data || [] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/org/presence', checkAuth, async (req, res) => {
  const user  = req.session.userInfo;
  const orgId = req.session.orgId;
  if (!user || !orgId) return res.json({ ok: false });
  try {
    await supabaseAdmin
      .from('user_presence')
      .upsert({
        user_sub:  user.sub,
        org_id:    orgId,
        email:     user.email,
        last_seen: new Date().toISOString(),
      }, { onConflict: 'user_sub' });
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Logout ────────────────────────────────────────────────────────────────────

app.get('/logout', (req, res) => {
  req.session.destroy(() => {
    res.clearCookie('connect.sid');
    const logoutUrl =
      `${process.env.COGNITO_LOGOUT_URL}` +
      `?client_id=${process.env.CLIENT_ID}` +
      `&logout_uri=${process.env.LOGOUT_URI}`;
    res.redirect(logoutUrl);
  });
});

// ── Start ─────────────────────────────────────────────────────────────────────

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));