const express = require('express');
const cors = require('cors');
const session = require('express-session');
const { Issuer, generators } = require('openid-client');
require('dotenv').config();


const app = express();

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
    saveUninitialized: false,
    cookie: {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        sameSite: 'lax',
        maxAge: 1000 * 60 * 60 // 1 hour
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

app.get('/', checkAuth, (req, res) => {
    res.json({
    isAuthenticated: req.isAuthenticated,
    user: req.session.userInfo || null
});
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

app.get('/callback', checkClientReady, async (req, res) => {
    console.log('Session at callback:', req.session);
    try {
        const params = client.callbackParams(req);

        // ✅ Pass stored nonce + state for validation
        const tokenSet = await client.callback(
            process.env.REDIRECT_URI,
            params,
            {
                nonce: req.session.nonce,
                state: req.session.state,
            }
        );

        const userInfo = await client.userinfo(tokenSet.access_token);

        // ✅ Store user, clean up one-time values
        req.session.userInfo = userInfo;
        delete req.session.nonce;
        delete req.session.state;

        // ✅ Redirect back to React frontend landing page
        res.redirect('http://localhost:5173');
    } catch (err) {
        console.error('Callback error:', err);
        res.redirect('http://localhost:5173?error=auth_failed');
    }
});

app.get('/logout', (req, res) => {
    req.session.destroy(() => {
        const logoutUrl = `${process.env.COGNITO_LOGOUT_URL}?client_id=${process.env.CLIENT_ID}&logout_uri=${process.env.LOGOUT_URI}`;
        
        console.log("FINAL LOGOUT URL:", logoutUrl); 
        
        res.redirect(logoutUrl);
    });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));