const express    = require('express');
const cors       = require('cors');
const session    = require('express-session');
const { Issuer, generators } = require('openid-client');
require('dotenv').config();
const { v4: uuidv4 } = require('uuid');
const nodemailer = require('nodemailer');
const crypto     = require('crypto');
const { createClient: createRedisClient } = require('redis');

const app = express();
app.set('trust proxy', 1);
app.use(express.json());

function inviteExpiresAt(days = 7) {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

const { createClient } = require('@supabase/supabase-js');
const supabaseAdmin = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

const redisClient = createRedisClient({ url: process.env.REDIS_URL });
redisClient.on('error', (err) => console.error('Redis error:', err));
redisClient.connect().catch(console.error);

// ── Redis session store ───────────────────────────────────────────────────────

class RedisSessionStore extends session.Store {
  constructor(client, ttlSeconds = 7 * 24 * 60 * 60) {
    super();
    this.client = client;
    this.ttl    = ttlSeconds;
  }
  _key(sid) { return `sess:${sid}`; }
  get(sid, cb) {
    this.client.get(this._key(sid))
      .then(data => {
        if (!data) return cb(null, null);
        try { cb(null, JSON.parse(data)); }
        catch (e) { cb(e); }
      })
      .catch(cb);
  }
  set(sid, sess, cb) {
    let ttl = this.ttl;
    if (sess?.cookie?.expires)
      ttl = Math.max(1, Math.floor((new Date(sess.cookie.expires) - Date.now()) / 1000));
    this.client.setEx(this._key(sid), ttl, JSON.stringify(sess))
      .then(() => cb(null)).catch(cb);
  }
  destroy(sid, cb) {
    this.client.del(this._key(sid)).then(() => cb(null)).catch(cb);
  }
  touch(sid, sess, cb) {
    let ttl = this.ttl;
    if (sess?.cookie?.expires)
      ttl = Math.max(1, Math.floor((new Date(sess.cookie.expires) - Date.now()) / 1000));
    this.client.expire(this._key(sid), ttl).then(() => cb(null)).catch(cb);
  }
}

app.use(cors({
  origin:      process.env.FRONTEND_URL,
  credentials: true,
}));

app.use(session({
  store:             new RedisSessionStore(redisClient),
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

// ── OIDC client ───────────────────────────────────────────────────────────────

let client;
async function initializeClient() {
  const issuer = await Issuer.discover(process.env.COGNITO_ISSUER_URL);
  client = new issuer.Client({
    client_id:      process.env.CLIENT_ID,
    client_secret:  process.env.CLIENT_SECRET,
    redirect_uris:  [process.env.REDIRECT_URI],
    response_types: ['code'],
  });
}
initializeClient().catch(console.error);

// ── Middleware ────────────────────────────────────────────────────────────────

const checkClientReady = (req, res, next) => {
  if (!client) return res.status(503).send('Service is starting, please try again shortly.');
  next();
};

const requireAuth = (req, res, next) => {
  if (!req.session.userInfo) return res.status(401).json({ error: 'Not authenticated' });
  next();
};

const requireAdmin = async (req, res, next) => {
  if (!req.session.userInfo) return res.status(401).json({ error: 'Not authenticated' });
  const orgId = req.session.orgId;
  if (!orgId) return res.status(403).json({ error: 'Not in an org' });
  try {
    const { data } = await supabaseAdmin
      .from('org_members')
      .select('role')
      .eq('org_id', orgId)
      .eq('user_sub', req.session.userInfo.sub)
      .single();
    if (!data || data.role !== 'admin')
      return res.status(403).json({ error: 'Admin access required' });
    next();
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
};

// ── OAuth state helpers ───────────────────────────────────────────────────────
// Store nonce+state in Redis keyed by baseState so /callback can recover them
// even when Safari ITP or strict Android Chrome drops the session cookie.

const OAUTH_STATE_TTL = 600; // 10 minutes

function splitState(rawState) {
  if (!rawState) return { baseState: '', inviteToken: null };
  const idx = rawState.indexOf('|inviteToken:');
  if (idx === -1) return { baseState: rawState, inviteToken: null };
  return {
    baseState:   rawState.slice(0, idx),
    inviteToken: rawState.slice(idx + '|inviteToken:'.length) || null,
  };
}

async function saveOAuthParams(baseState, nonce) {
  await redisClient.setEx(`oauth:${baseState}`, OAUTH_STATE_TTL, JSON.stringify({ nonce }));
}

async function loadAndDeleteOAuthParams(baseState) {
  const raw = await redisClient.getDel(`oauth:${baseState}`);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

// ── Invite helper ─────────────────────────────────────────────────────────────

async function processInviteAndRedirect(req, res, inviteToken, userInfo) {
  try {
    const { data: invite, error } = await supabaseAdmin
      .from('invite_tokens')
      .select('*, orgs(*)')
      .eq('token', inviteToken)
      .single();

    if (!error && invite && (!invite.expires_at || new Date(invite.expires_at) > new Date())) {
      const { error: upsertErr } = await supabaseAdmin.from('org_members').upsert({
        org_id:   invite.org_id,
        user_sub: userInfo.sub,
        email:    userInfo.email,
        role:     'member',
      }, { onConflict: 'org_id,user_sub' });

      if (upsertErr) {
        console.error('[invite] upsert failed:', upsertErr.message);
      } else {
        req.session.orgId   = invite.org_id;
        req.session.orgName = invite.orgs.name;
        req.session.mode    = 'org';
        req.session.memberships = [
          ...(req.session.memberships || []).filter(m => m.org_id !== invite.org_id),
          { org_id: invite.org_id, role: 'member', orgs: { id: invite.org_id, name: invite.orgs.name } },
        ];
      }
    } else {
      console.warn('[invite] Invalid or expired token:', inviteToken, error?.message);
      return req.session.save(() =>
        res.redirect(`${process.env.FRONTEND_URL}/dashboard?error=invalid_invite`)
      );
    }
  } catch (err) {
    console.error('[invite] processInviteAndRedirect error:', err.message);
  }
  return req.session.save(() => res.redirect(`${process.env.FRONTEND_URL}/dashboard`));
}

// ── Routes ────────────────────────────────────────────────────────────────────

app.get('/', (req, res) => {
  if (!req.session.tokens && !req.session.userInfo)
    return res.json({ isAuthenticated: false });
  res.json({
    isAuthenticated: true,
    user:         req.session.userInfo || null,
    id_token:     req.session.tokens?.id_token     || null,
    access_token: req.session.tokens?.access_token || null,
    orgId:        req.session.orgId                || null,
    orgName:      req.session.orgName              || null,
    mode:         req.session.mode                 || null,
    memberships:  req.session.memberships          || [],
  });
});

// ── Login ─────────────────────────────────────────────────────────────────────

app.get('/login', checkClientReady, async (req, res) => {
  const nonce        = generators.nonce();
  const baseState    = generators.state();

  // FIX: read req.query.redirect (not returnTo — JoinPage now uses redirect=)
  const redirectPath = req.query.redirect || '';
  const inviteMatch  = redirectPath.match(/\/org\/join\/([^/?#]+)/);

  const fullState = inviteMatch
    ? `${baseState}|inviteToken:${inviteMatch[1]}`
    : baseState;

  req.session.nonce = nonce;
  req.session.state = baseState;

  try {
    await saveOAuthParams(baseState, nonce);
  } catch (err) {
    console.error('[login] Redis saveOAuthParams failed:', err.message);
  }

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

    let expectedNonce = req.session.nonce;
    let expectedState = req.session.state;

    if (!expectedNonce || !expectedState) {
      console.warn('[callback] Session nonce/state missing — trying Redis fallback');
      const stored = await loadAndDeleteOAuthParams(baseState);
      if (stored) {
        expectedNonce = stored.nonce;
        expectedState = baseState;
      }
    }

    if (!expectedNonce || !expectedState) {
      console.error('[callback] Missing nonce and state — both session and Redis failed');
      return res.redirect(`${process.env.FRONTEND_URL}/login?error=session_expired`);
    }

    const params   = client.callbackParams(req);
    params.state   = baseState;

    const tokenSet = await client.callback(
      process.env.REDIRECT_URI,
      params,
      { nonce: expectedNonce, state: expectedState }
    );

    const userInfo = await client.userinfo(tokenSet.access_token);

    req.session.userInfo = userInfo;
    req.session.tokens   = {
      access_token:  tokenSet.access_token,
      id_token:      tokenSet.id_token,
      refresh_token: tokenSet.refresh_token,
    };
    delete req.session.nonce;
    delete req.session.state;

    const { data: memberships } = await supabaseAdmin
      .from('org_members')
      .select('org_id, role, orgs(id, name)')
      .eq('user_sub', userInfo.sub);

    req.session.memberships = memberships || [];

    if (stateInviteToken)
      return processInviteAndRedirect(req, res, stateInviteToken, userInfo);

    if (memberships?.length === 1) {
      req.session.orgId   = memberships[0].org_id;
      req.session.orgName = memberships[0].orgs?.name;
      req.session.mode    = 'org';
    }

    req.session.save(() => res.redirect(`${process.env.FRONTEND_URL}/dashboard`));

  } catch (err) {
    console.error('Callback error:', err);
    res.redirect(`${process.env.FRONTEND_URL}?error=auth_failed`);
  }
});

// ── Refresh ───────────────────────────────────────────────────────────────────

app.post('/refresh', requireAuth, async (req, res) => {
  const refreshToken = req.session.tokens?.refresh_token;
  if (!refreshToken)
    return res.status(401).json({ error: 'No refresh token — please log in again.' });
  try {
    const tokenSet = await client.refresh(refreshToken);
    req.session.tokens = {
      access_token:  tokenSet.access_token,
      id_token:      tokenSet.id_token,
      refresh_token: tokenSet.refresh_token || refreshToken,
    };
    return req.session.save(() => res.json({ access_token: tokenSet.access_token }));
  } catch (err) {
    console.error('Token refresh failed:', err.message);
    return res.status(401).json({ error: 'Session expired — please log in again.' });
  }
});

// ── Org: join via invite link ─────────────────────────────────────────────────
// This route is the ACTUAL join handler — JoinPage.jsx redirects here after
// confirming the user is logged in.

app.get('/org/join/:token', checkClientReady, async (req, res) => {
  const { token } = req.params;
  const user      = req.session.userInfo;

  // Not logged in — redirect to login with the invite path baked into state
  if (!user) {
    return res.redirect(`/login?redirect=${encodeURIComponent(`/org/join/${token}`)}`);
  }

  const { data: invite, error } = await supabaseAdmin
    .from('invite_tokens')
    .select('*, orgs(*)')
    .eq('token', token)
    .single();

  if (error || !invite)
    return res.redirect(`${process.env.FRONTEND_URL}/dashboard?error=invalid_invite`);

  if (invite.expires_at && new Date(invite.expires_at) < new Date())
    return res.redirect(`${process.env.FRONTEND_URL}/dashboard?error=expired_invite`);

  const { error: upsertErr } = await supabaseAdmin.from('org_members').upsert({
    org_id:   invite.org_id,
    user_sub: user.sub,
    email:    user.email,
    role:     'member',
  }, { onConflict: 'org_id,user_sub' });

  if (upsertErr) {
    console.error('[join] upsert failed:', upsertErr.message);
    return res.redirect(`${process.env.FRONTEND_URL}/dashboard?error=join_failed`);
  }

  req.session.orgId   = invite.org_id;
  req.session.orgName = invite.orgs.name;
  req.session.mode    = 'org';
  req.session.memberships = [
    ...(req.session.memberships || []).filter(m => m.org_id !== invite.org_id),
    { org_id: invite.org_id, role: 'member', orgs: { id: invite.org_id, name: invite.orgs.name } },
  ];

  req.session.save(() => res.redirect(`${process.env.FRONTEND_URL}/dashboard`));
});

// ── Org: memberships ──────────────────────────────────────────────────────────

app.get('/org/memberships', requireAuth, (req, res) => {
  res.json({
    memberships:    req.session.memberships  || [],
    currentOrgId:   req.session.orgId        || null,
    currentOrgName: req.session.orgName      || null,
  });
});

app.get('/org/me', requireAuth, (req, res) => {
  res.json({
    orgId:   req.session.orgId   || null,
    orgName: req.session.orgName || null,
  });
});

// ── Org: select workspace ─────────────────────────────────────────────────────

app.post('/org/select', requireAuth, async (req, res) => {
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

app.post('/org/create', requireAuth, async (req, res) => {
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

app.post('/org/invite', requireAdmin, async (req, res) => {
  const orgId = req.session.orgId;
  const token = uuidv4();
  await supabaseAdmin.from('invite_tokens').insert({
    org_id:     orgId,
    token,
    created_by: req.session.userInfo.sub,
    expires_at: inviteExpiresAt(7),
  });
  // inviteUrl points to FRONTEND /org/join/:token — JoinPage handles the rest
  res.json({ inviteUrl: `${process.env.FRONTEND_URL}/org/join/${token}` });
});

// ── Org: send invite email ────────────────────────────────────────────────────

app.post('/org/invite/email', requireAdmin, async (req, res) => {
  const { email, inviteUrl } = req.body;
  const orgName    = req.session.orgName              || 'SecureStream';
  const senderName = req.session.userInfo?.given_name || 'A teammate';

  if (!email || !inviteUrl)
    return res.status(400).json({ error: 'Email and inviteUrl required' });
  if (!process.env.EMAIL_USER || !process.env.EMAIL_PASS)
    return res.status(500).json({ error: 'Email not configured on server' });

  try {
    const transporter = nodemailer.createTransport({
      service: 'gmail',
      auth: { user: process.env.EMAIL_USER, pass: process.env.EMAIL_PASS },
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

// ── Org: members ──────────────────────────────────────────────────────────────

app.get('/org/members', requireAuth, async (req, res) => {
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

app.delete('/org/members/:user_sub', requireAuth, async (req, res) => {
  const orgId        = req.session.orgId;
  const { user_sub } = req.params;
  const requestorSub = req.session.userInfo.sub;

  if (!orgId) return res.status(400).json({ error: 'Not in an org' });

  const { data: requestor } = await supabaseAdmin
    .from('org_members')
    .select('role')
    .eq('org_id', orgId)
    .eq('user_sub', requestorSub)
    .single();

  if (!requestor) return res.status(403).json({ error: 'Not a member of this org' });

  if (user_sub !== requestorSub && requestor.role !== 'admin')
    return res.status(403).json({ error: 'Admin access required to remove other members' });

  try {
    await supabaseAdmin.from('org_members').delete()
      .eq('org_id', orgId).eq('user_sub', user_sub);
    res.json({ removed: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.patch('/org/members/:user_sub/role', requireAdmin, async (req, res) => {
  const orgId        = req.session.orgId;
  const { user_sub } = req.params;
  const { role }     = req.body;
  if (!['admin', 'member', 'viewer'].includes(role))
    return res.status(400).json({ error: 'Invalid role' });
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

app.get('/org/online', requireAuth, async (req, res) => {
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

app.post('/org/presence', requireAuth, async (req, res) => {
  const user  = req.session.userInfo;
  const orgId = req.session.orgId;
  if (!orgId) return res.json({ ok: false });
  try {
    await supabaseAdmin.from('user_presence').upsert({
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

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));