const express = require('express');
const cors = require('cors');
const session = require('express-session');
const { Issuer, generators } = require('openid-client');
require('dotenv').config();
const { v4: uuidv4 } = require('uuid');
const app = express();
app.use(express.json());
const nodemailer = require('nodemailer');

const transporter = nodemailer.createTransport({
  service: 'gmail',
  auth: {
    user: process.env.EMAIL_USER,
    pass: process.env.EMAIL_PASS
  }
});

const { createClient } = require('@supabase/supabase-js');
const supabaseAdmin = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

app.use(cors({
    origin: 'http://localhost:5173',
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
    secure: false,   
    sameSite: "lax"
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
app.post('/org/create', checkAuth, async (req, res) => {
  const { name } = req.body;
  const user = req.session.userInfo;
  if (!name) return res.status(400).json({ error: 'Org name required' });

  const { data: org, error } = await supabaseAdmin
    .from('orgs')
    .insert({ name, created_by: user.sub })
    .select()
    .single();

  if (error) return res.status(500).json({ error: error.message });

  // Add creator as admin member
  await supabaseAdmin.from('org_members').insert({
    org_id: org.id,
    user_sub: user.sub,
    email: user.email,
    role: 'admin'
  });

  // Generate invite token
  const token = uuidv4();
  await supabaseAdmin.from('invite_tokens').insert({
    org_id: org.id,
    token,
    created_by: user.sub
  });

  // Store org in session
  req.session.orgId = org.id;
  req.session.orgName = org.name;
  req.session.save();

  res.json({ org, inviteToken: token });
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

  if (error || !invite) return res.redirect('http://localhost:5173?error=invalid_invite');
  if (invite.expires_at && new Date(invite.expires_at) < new Date())
    return res.redirect('http://localhost:5173?error=expired_invite');

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
    res.redirect('http://localhost:5173/dashboard');
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

  res.json({ token, inviteUrl: `http://localhost:3000/org/join/${token}` });
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
      res.redirect('http://localhost:5173/workspace-select');
    });
  } catch (err) {
    console.error('Callback error:', err);
    res.redirect('http://localhost:5173?error=auth_failed');
  }
});

app.post('/org/invite/email', checkAuth, async (req, res) => {
  const { email, inviteUrl } = req.body;
  const orgName = req.session.orgName || 'SecureStream';
  const senderName = req.session.userInfo?.given_name || 'A teammate';

  if (!email || !inviteUrl) {
    return res.status(400).json({ error: 'Email and inviteUrl required' });
  }

  try {
    await transporter.sendMail({
      from: `"SecureStream" <${process.env.EMAIL_USER}>`,
      to: email,
      subject: `${senderName} invited you to join ${orgName} on SecureStream`,
      html: `
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:24px">
            <div style="width:28px;height:28px;background:#185FA5;border-radius:8px;display:flex;align-items:center;justify-content:center">
              <span style="color:white;font-size:14px">S</span>
            </div>
            <span style="font-weight:600;font-size:16px">SecureStream</span>
          </div>
          <h1 style="font-size:22px;font-weight:700;margin:0 0 8px">You're invited</h1>
          <p style="color:#6b7280;font-size:14px;margin:0 0 24px">
            ${senderName} has invited you to join <strong>${orgName}</strong> on SecureStream — a secure document intelligence platform.
          </p>
          <a href="${inviteUrl}"
            style="display:inline-block;background:#185FA5;color:white;padding:12px 24px;border-radius:10px;text-decoration:none;font-size:14px;font-weight:500">
            Accept invitation
          </a>
          <p style="color:#9ca3af;font-size:12px;margin-top:24px">
            This link expires in 7 days. If you weren't expecting this, you can ignore it.
          </p>
        </div>
      `
    });
    res.json({ success: true });
  } catch (err) {
    console.error('Email error:', err);
    res.status(500).json({ error: 'Failed to send email' });
  }
});

app.get('/logout', (req, res) => {
    req.session.destroy((err) => {
        res.clearCookie('connect.sid');   // 🔥 IMPORTANT

        const logoutUrl = `${process.env.COGNITO_LOGOUT_URL}?client_id=${process.env.CLIENT_ID}&logout_uri=${process.env.LOGOUT_URI}`;

        res.redirect(logoutUrl);
    });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));