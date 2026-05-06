const express = require('express');
const cors = require('cors');
const session = require('express-session');
const { Issuer, generators } = require('openid-client');
require('dotenv').config();
const { v4: uuidv4 } = require('uuid');
const app = express();
app.set("trust proxy", 1);
app.use(express.json());
const nodemailer = require('nodemailer');

const transporter = nodemailer.createTransport({
  host: 'smtp.gmail.com',
  port: 587,
  secure: false,           // true for 465, false for 587
  auth: {
    user: process.env.EMAIL_USER,
    pass: process.env.EMAIL_PASS
  },
  tls: {
    rejectUnauthorized: false
  }
});

// Verify connection on startup
transporter.verify((error, success) => {
  if (error) {
    console.error('EMAIL TRANSPORTER ERROR:', error.message);
  } else {
    console.log('Email server ready');
  }
});

const { createClient } = require('@supabase/supabase-js');
const supabaseAdmin = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

app.use(cors({
    origin: process.env.FRONTEND_URL,
    credentials: true
}));


let client;

async function initializeClient() {
    const issuer = await Issuer.discover(process.env.COGNITO_ISSUER_URL);
    client = new issuer.Client({
        client_id: process.env.CLIENT_ID,
        client_secret: process.env.CLIENT_SECRET,
        redirect_uris: [process.env.REDIRECT_URI],
        response_types: ['code']
    });
}

initializeClient().catch(console.error);

app.use(session({
  secret: process.env.SESSION_SECRET,
  resave: false,
  saveUninitialized: true,
  cookie: {
  secure: true,
  sameSite: "none"
}
}));

const checkClientReady = (req, res, next) => {
    if (!client) {
        return res.status(503).send('Service is starting, please try again shortly.');
    }
    next();
};

const checkAuth = (req, res, next) => {
    req.isAuthenticated = !!req.session.userInfo;
    next();
};

app.get('/', (req, res) => {
  console.log("SESSION ROOT:", req.session);

  if (!req.session.tokens && !req.session.userInfo) {
    return res.json({ isAuthenticated: false });
  }

  res.json({
    isAuthenticated: true,
    user:         req.session.userInfo        || null,
    id_token:     req.session.tokens?.id_token     || null,
    access_token: req.session.tokens?.access_token || null,
    orgId:        req.session.orgId           || null,
    orgName:      req.session.orgName         || null,
    mode:         req.session.mode            || null,
    memberships:  req.session.memberships     || []
  });
});
app.get('/org/memberships', checkAuth, (req, res) => {
  res.json({
    memberships: req.session.memberships || [],
    currentOrgId:   req.session.orgId   || null,
    currentOrgName: req.session.orgName || null
  });
});

// Select which org/mode to use
app.post('/org/select', checkAuth, async (req, res) => {
  const { orgId, mode } = req.body;
  // mode: "personal" | "org"

  if (mode === 'personal') {
    req.session.orgId   = null;
    req.session.orgName = null;
    req.session.mode    = 'personal';
    return req.session.save(() => res.json({ mode: 'personal' }));
  }

  if (mode === 'org' && orgId) {
    // Verify user is actually a member
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
app.post('/org/create', async (req, res) => {
  // Check auth manually instead of using middleware
  if (!req.session?.userInfo) {
    return res.status(401).json({ error: "not_authenticated" });
  }

  const { name } = req.body;
  const user = req.session.userInfo;
  if (!name) return res.status(400).json({ error: 'Org name required' });

  try {
    const { data: org, error } = await supabaseAdmin
      .from('orgs')
      .insert({ name, created_by: user.sub })
      .select()
      .single();

    if (error) return res.status(500).json({ error: error.message });

    await supabaseAdmin.from('org_members').insert({
      org_id: org.id,
      user_sub: user.sub,
      email: user.email,
      role: 'admin'
    });

    const token = require('crypto').randomUUID();
    await supabaseAdmin.from('invite_tokens').insert({
      org_id: org.id,
      token,
      created_by: user.sub
    });

    req.session.orgId   = org.id;
    req.session.orgName = org.name;
    req.session.mode    = 'org';

    // Add to memberships list
    req.session.memberships = [
      ...(req.session.memberships || []),
      { org_id: org.id, role: 'admin', orgs: { id: org.id, name: org.name } }
    ];

    req.session.save();
    res.json({ org, inviteToken: token });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Join org via invite token
app.get('/org/join/:token', checkAuth, async (req, res) => {
  const { token } = req.params;
  const user = req.session.userInfo;

  const { data: invite, error } = await supabaseAdmin
    .from('invite_tokens')
    .select('*, orgs(*)')
    .eq('token', token)
    .single();

  if (error || !invite) return res.redirect(`${process.env.FRONTEND_URL}?error=invalid_invite`);
  if (invite.expires_at && new Date(invite.expires_at) < new Date())
    return res.redirect(`${process.env.FRONTEND_URL}?error=expired_invite`);

  // Add member if not already in org
  await supabaseAdmin.from('org_members').upsert({
    org_id: invite.org_id,
    user_sub: user.sub,
    email: user.email,
    role: 'member'
  }, { onConflict: 'org_id,user_sub' });

  // Store org in session
  req.session.orgId = invite.org_id;
  req.session.orgName = invite.orgs.name;
  req.session.save(() => {
    res.redirect(`${process.env.FRONTEND_URL}/dashboard`);
  });
});

// Get current org info
app.get('/org/me', checkAuth, (req, res) => {
  res.json({
    orgId: req.session.orgId || null,
    orgName: req.session.orgName || null
  });
});

// Generate new invite link
app.post('/org/invite', checkAuth, async (req, res) => {
  const orgId = req.session.orgId;
  if (!orgId) return res.status(400).json({ error: 'Not in an org' });

  const token = uuidv4();
  await supabaseAdmin.from('invite_tokens').insert({
    org_id: orgId,
    token,
    created_by: req.session.userInfo.sub
  });
  res.json({ inviteUrl: `${process.env.FRONTEND_URL}/org/join/${token}` });
});

app.get('/login', checkClientReady, (req, res) => {
    const nonce = generators.nonce();
    const state = generators.state();

    req.session.nonce = nonce;
    req.session.state = state;

    // ✅ CRITICAL: save session BEFORE redirecting, so state/nonce
    // are persisted before Cognito sends the user back to /callback
    req.session.save((err) => {
        if (err) {
            console.error('Session save error:', err);
            return res.status(500).send('Session error');
        }

        const authUrl = client.authorizationUrl({
            scope: 'phone openid email',
            state: state,
            nonce: nonce,
        });

        res.redirect(authUrl);
    });
});

// Select which org/mode to use
app.post('/org/select', checkAuth, async (req, res) => {
  const { orgId, mode } = req.body;
  // mode: "personal" | "org"

  if (mode === 'personal') {
    req.session.orgId   = null;
    req.session.orgName = null;
    req.session.mode    = 'personal';
    return req.session.save(() => res.json({ mode: 'personal' }));
  }

  if (mode === 'org' && orgId) {
    // Verify user is actually a member
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
// Replace your /callback route with this:
app.get('/callback', checkClientReady, async (req, res) => {
  try {
    const params   = client.callbackParams(req);
    const tokenSet = await client.callback(
      process.env.REDIRECT_URI,
      params,
      { nonce: req.session.nonce, state: req.session.state }
    );

    const userInfo = await client.userinfo(tokenSet.access_token);
    req.session.userInfo = userInfo;
    req.session.tokens = {
  access_token: tokenSet.access_token,
  id_token: tokenSet.id_token,
  refresh_token: tokenSet.refresh_token
};
    delete req.session.nonce;
    delete req.session.state;

    // Look up all orgs this user belongs to
    const { data: memberships } = await supabaseAdmin
      .from('org_members')
      .select('org_id, role, orgs(id, name)')
      .eq('user_sub', userInfo.sub);

    req.session.memberships = memberships || [];

    // If only one org, auto-restore it
    if (memberships?.length === 1) {
      req.session.orgId   = memberships[0].org_id;
      req.session.orgName = memberships[0].orgs?.name;
    }

    req.session.save(() => {
      res.redirect(`${process.env.FRONTEND_URL}/dashboard`);
    });
  } catch (err) {
    console.error('Callback error:', err);
    res.redirect(`${process.env.FRONTEND_URL}?error=auth_failed`);
  }
});

app.post('/org/invite/email', checkAuth, async (req, res) => {
  const { email, inviteUrl } = req.body;
  const orgName    = req.session.orgName    || 'SecureStream';
  const senderName = req.session.userInfo?.given_name || 'A teammate';

  console.log("Sending invite to:", email);

  if (!email || !inviteUrl) {
    return res.status(400).json({ error: 'Email and inviteUrl required' });
  }

  if (!process.env.EMAIL_USER || !process.env.EMAIL_PASS) {
    console.error('EMAIL_USER or EMAIL_PASS not set in .env');
    return res.status(500).json({ error: 'Email not configured on server' });
  }

  try {
    const info = await transporter.sendMail({
      from: `"SecureStream" <${process.env.EMAIL_USER}>`,
      to: email,
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
          <p style="color:#9ca3af;font-size:12px;margin-top:24px">
            Link expires in 7 days.
          </p>
        </div>
      `
    });

    console.log('Email sent:', info.messageId);
    res.json({ success: true, messageId: info.messageId });

  } catch (err) {
    console.error('EMAIL SEND ERROR:', err.message);
    console.error('FULL ERROR:', err);
    res.status(500).json({ error: err.message });
  }
});

app.get('/logout', (req, res) => {
    req.session.destroy((err) => {
        res.clearCookie('connect.sid');   // 🔥 IMPORTANT

        const logoutUrl = `${process.env.COGNITO_LOGOUT_URL}?client_id=${process.env.CLIENT_ID}&logout_uri=${process.env.LOGOUT_URI}`;

        res.redirect(logoutUrl);
    });
});

// ── GET /org/members ──
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

// ── GET /org/online ──
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

// ── POST /org/presence ──
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
        last_seen: new Date().toISOString()
      }, { onConflict: 'user_sub' });

    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));