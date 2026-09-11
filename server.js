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

// 登入狀態(session)存到 Supabase 的 Postgres 裡，這樣伺服器重開/休眠不會把大家登出。
// 沒設定 DATABASE_URL 時 fallback 回預設的記憶體 store，至少本機還能跑，只是伺服器重開會登出。
let sessionStore;
if (process.env.DATABASE_URL) {
  const { Pool } = require('pg');
  const pgSession = require('connect-pg-simple')(session);
  const pgPool = new Pool({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false }
  });
  // 沒有這個監聽的話，連線失敗(例如連不到 IPv6)會讓整個 Node process 直接當機。
  pgPool.on('error', function (err) { console.error('[session] pg pool 連線錯誤：', err.message); });
  sessionStore = new pgSession({ pool: pgPool, tableName: 'session', createTableIfMissing: true });
  sessionStore.on('error', function (err) { console.error('[session] session store 錯誤：', err.message); });
} else {
  console.warn('[session] 尚未設定 DATABASE_URL，登入狀態會存在記憶體，伺服器重啟或休眠就會把大家登出。');
}

const sessionConfig = {
  secret: process.env.SESSION_SECRET || 'dev-secret-change-me',
  resave: false,
  saveUninitialized: false,
  cookie: {
    maxAge: 1000 * 60 * 60 * 24 * 30,
    secure: process.env.NODE_ENV === 'production'
  }
};
if (sessionStore) sessionConfig.store = sessionStore;
app.use(session(sessionConfig));

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
