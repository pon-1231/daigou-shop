const path = require('path');
const express = require('express');
const router = express.Router();

router.get('/login', (req, res) => {
  if (req.session.loggedIn) return res.redirect('/pricing.html');
  res.sendFile(path.join(__dirname, '..', 'public', 'login.html'));
});

router.post('/login', express.urlencoded({ extended: false }), (req, res) => {
  const { password } = req.body || {};
  const appPassword = process.env.APP_PASSWORD;

  if (!appPassword) {
    return res.status(500).send('伺服器尚未設定 APP_PASSWORD，請參考 .env.example');
  }
  if (password && password === appPassword) {
    req.session.loggedIn = true;
    return res.redirect('/pricing.html');
  }
  res.redirect('/login?error=1');
});

router.post('/logout', (req, res) => {
  req.session.destroy(() => res.redirect('/login'));
});

function requireAuth(req, res, next) {
  if (req.session && req.session.loggedIn) return next();
  if (req.path.startsWith('/api/')) return res.status(401).json({ error: '未登入' });
  res.redirect('/login');
}

module.exports = { router, requireAuth };
