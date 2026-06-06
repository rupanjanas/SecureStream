const express  = require('express');
const cors     = require('cors');
const session  = require('express-session');
const { Issuer, generators } = require('openid-client');
require('dotenv').config();
const { createClient: createRedisClient } = require('redis');

const app = express();
app.set('trust proxy', 1);
app.use(express.json());

// ── Redis ─────────────────────────────────────────────────────────────────────

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
// ── CORS + Session ────────────────────────────────────────────────────────────

app.use(cors({
  origin:      process.env.FRONTEND_URL,
  credentials: true,
}));

app.use(session({
  store:             new RedisSessionStore(redisClient),
  secret:            process.env.SESSION_SECRET,
  resave:            false,
  saveUninitialized: false,
  proxy:             true,
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

// ── Routes ────────────────────────────────────────────────────────────────────

// Session status — called by the frontend on every page load
app.get('/', (req, res) => {
  if (!req.session.tokens && !req.session.userInfo)
    return res.json({ isAuthenticated: false });
  res.json({
    isAuthenticated: true,
    user:         req.session.userInfo || null,
    id_token:     req.session.tokens?.id_token     || null,
    access_token: req.session.tokens?.access_token || null,
  });
});

// ── Login ─────────────────────────────────────────────────────────────────────

app.get('/login', checkClientReady, async (req, res) => {
  const nonce = generators.nonce();
  const state = generators.state();

  req.session.nonce = nonce;
  req.session.state = state;

  req.session.save((err) => {
    if (err) {
      console.error('Session save error on /login:', err);
      return res.status(500).send('Session error');
    }
    const authUrl = client.authorizationUrl({
      scope: 'phone openid email',
      state,
      nonce,
    });
    res.redirect(authUrl);
  });
});

// ── Callback ──────────────────────────────────────────────────────────────────

app.get('/callback', checkClientReady, async (req, res) => {
  try {
    const expectedNonce = req.session.nonce;
    const expectedState = req.session.state;

    if (!expectedNonce || !expectedState) {
      console.error('[callback] Missing nonce or state in session');
      return res.redirect(`${process.env.FRONTEND_URL}/login?error=session_expired`);
    }

    const params   = client.callbackParams(req);
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