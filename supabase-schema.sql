-- 代購賣場工具 — Supabase 資料表設定
-- 使用方式:登入 Supabase 專案 → 左側選單「SQL Editor」→ 新增 Query → 貼上整份 → Run

-- 1. 定價計算機儲存的商品清單
create table if not exists priced_items (
  id uuid primary key default gen_random_uuid(),
  name text not null default '',
  category_key text not null default '',
  category_label text not null default '未分類',
  rmb_price numeric not null default 0,
  rmb_ship numeric not null default 0,
  fx_rate numeric not null default 4.5,
  cross_ship numeric not null default 0,
  packaging numeric not null default 0,
  payment_fee numeric not null default 0,
  domestic_ship numeric not null default 0,
  multiplier numeric not null default 0,
  manual_price numeric,
  china_cost numeric not null default 0,
  actual_cost numeric not null default 0,
  final_price numeric not null default 0,
  profit numeric not null default 0,
  margin numeric not null default 0,
  created_at timestamptz not null default now()
);

-- 2. 銷售紀錄(客人買了什麼、照片、利潤)
create table if not exists sales_records (
  id uuid primary key default gen_random_uuid(),
  customer_name text not null default '',
  item_name text not null default '',
  photo_url text,
  cost numeric not null default 0,
  sold_price numeric not null default 0,
  shipping_fee numeric not null default 0,
  profit numeric not null default 0,
  note text not null default '',
  sold_at date not null default current_date,
  created_at timestamptz not null default now()
);

-- 這個工具是後端(Express 伺服器)用 service_role 金鑰連線,不會把 Supabase 金鑰交給瀏覽器,
-- 所以不需要另外設定 RLS 政策;service_role 本來就會繞過 RLS。

-- 3. 照片儲存空間(Storage bucket)
-- SQL Editor 沒辦法建立 bucket,請改在左側選單「Storage」手動建立:
--   Bucket 名稱:sales-photos
--   Public bucket:打勾(方便直接用網址顯示照片,商品照片不算敏感資料)
