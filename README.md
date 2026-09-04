# 代購賣場工具

給朋友的代購賣場用的小網站，兩個分頁：

1. **定價計算機** — 算成本、算建議售價，存商品清單
2. **銷售紀錄** — 記錄客人買了什麼、上傳照片、自動算利潤、看統計

資料存在 Supabase（雲端資料庫 + 照片空間），不是存在伺服器本機，所以伺服器重開機也不會不見。另外有「匯出Excel備份」按鈕，可以隨時把所有資料下載成一份 Excel 檔案。

## 第一次設定（只需要做一次）

### 1. 建立 Supabase 專案

1. 到 [supabase.com](https://supabase.com) 註冊帳號（免費）
2. 建立一個新專案（New Project），資料庫密碼自己記一下
3. 專案建好後，左側選單找「SQL Editor」，新增一個 Query，把 [`supabase-schema.sql`](./supabase-schema.sql) 整份貼上去，按 Run
4. 左側選單找「Storage」，建立一個新的 bucket：
   - 名稱填 `sales-photos`
   - 「Public bucket」打勾
5. 左側選單找「Project Settings → API」，會看到：
   - `Project URL`（等一下填到 `SUPABASE_URL`）
   - `service_role` 金鑰（等一下填到 `SUPABASE_SERVICE_KEY`，**這組金鑰不要外流、不要放到會公開的地方**）

### 2. 設定環境變數

1. 把 `.env.example` 複製一份改名叫 `.env`
2. 打開 `.env`，把裡面的值換成：
   - `APP_PASSWORD`：你想給朋友用的登入密碼
   - `SESSION_SECRET`：隨便打一長串英數字（例如 32 個亂碼字元）
   - `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`：貼上一步拿到的值

### 3. 安裝套件並在本機測試

```bash
npm install
npm start
```

打開 http://localhost:3100，應該會先跳到登入頁，輸入 `.env` 裡設的 `APP_PASSWORD` 就能進去。

## 部署到 Render（跟 peter agent 一樣的模式）

1. 這個資料夾建一個新的 GitHub repo（跟 peter agent 分開，不要共用）
2. 到 [render.com](https://render.com)，New → Web Service，選這個 repo
3. Build Command 留空（沒有前端打包步驟），Start Command 填 `npm start`
4. 在 Render 的 Environment 分頁，把 `.env` 裡的四個變數（`APP_PASSWORD`、`SESSION_SECRET`、`SUPABASE_URL`、`SUPABASE_SERVICE_KEY`）都加進去 —— **不要把 `.env` 檔案傳上 GitHub**，`.gitignore` 已經排除了
5. 部署完成後，Render 會給一個網址，把那個網址傳給朋友，密碼另外用其他管道（例如當面講、LINE 私訊）告訴她，不要寫在網址裡

## 檔案結構

```
daigou-shop/
├── server.js              # 進入點
├── src/
│   ├── auth.js             # 登入 / 登出 / 驗證 middleware
│   ├── supabaseClient.js   # Supabase 連線
│   └── routes/
│       ├── pricedItems.js  # 定價計算機清單 API（含商品照片）
│       ├── orders.js       # 訂單 API（一張訂單可以有多樣商品）
│       ├── uploads.js      # 共用照片上傳端點
│       └── exportExcel.js  # Excel 匯出
├── public/
│   ├── login.html
│   ├── pricing.html         # 定價計算機頁面
│   ├── records.html         # 銷售紀錄頁面（點商品圖片建立訂單）
│   └── shared.css           # 共用樣式
└── supabase-schema.sql      # 資料庫建表 SQL（貼到 Supabase SQL Editor 執行）
```

## 已知限制

- 目前只有一組帳號密碼，全部人共用同一個登入
- 照片上傳沒有壓縮，大圖片會直接存原始檔案大小（8MB 上限）
- Excel 匯出是「當下資料庫的完整快照」，不是自動排程備份，要記得手動點
