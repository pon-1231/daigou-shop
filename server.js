const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '.env') });
const express = require('express');
const session = require('express-session');

const { router: authRouter, requireAuth } = require('./src/auth');
const pricedItemsRouter = require('./src/routes/pricedItems');
const ordersRouter = require('./src/routes/orders');
const uploadsRouter = require('./src/routes/uploads');
const exportRouter = require('./src/routes/exportExcel');

const app = express();
const PORT = process.env.PORT || 3100;

app.set('trust proxy', 1);

app.use(session({
  secret: process.env.SESSION_SECRET || 'dev-secret-change-me',
  resave: false,
  saveUninitialized: false,
  cookie: {
    maxAge: 1000 * 60 * 60 * 24 * 30,
    secure: process.env.NODE_ENV === 'production'
  }
}));

app.use(express.json({ limit: '2mb' }));

// 登入 / 登出（不需要驗證）
app.use('/', authRouter);

// 以下都需要先登入
app.use(requireAuth);

app.use('/api/priced-items', pricedItemsRouter);
app.use('/api/orders', ordersRouter);
app.use('/api/uploads', uploadsRouter);
app.use('/api/export', exportRouter);

app.use(express.static(path.join(__dirname, 'public')));

app.get('/', (req, res) => res.redirect('/pricing.html'));

app.listen(PORT, () => {
  console.log(`daigou-shop 伺服器已啟動：http://localhost:${PORT}`);
});
