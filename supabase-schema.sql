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
  photo_url text,
  sizes text,
  created_at timestamptz not null default now()
);

-- 1b. 如果 priced_items 表已經建立過，單獨執行這幾行：
alter table priced_items add column if not exists photo_url text;
alter table priced_items add column if not exists sizes text;
alter table priced_items add column if not exists sort_group text;
alter table priced_items add column if not exists item_no integer;
alter table priced_items add column if not exists color text;
alter table priced_items add column if not exists deleted_at timestamptz;
alter table priced_items add column if not exists spec1_name text;
alter table priced_items add column if not exists spec1_value text;
alter table priced_items add column if not exists spec2_name text;
alter table priced_items add column if not exists spec2_value text;

-- 1e. 轉換工具：把舊的 color/sizes 資料回填成新的規格欄位（可重複執行，不會覆蓋已經填過的資料）
update priced_items set spec1_name = '顏色', spec1_value = color
where color is not null and color <> '' and spec1_value is null;

update priced_items set spec2_name = '尺寸', spec2_value = sizes
where sizes is not null and sizes <> '' and spec2_value is null;

-- 1c. 把還沒有編號的舊資料，依照建立時間先補上 1,2,3...（之後可以在畫面上自己改）
with numbered as (
  select id, row_number() over (order by created_at asc) as rn
  from priced_items
  where item_no is null
)
update priced_items p
set item_no = numbered.rn
from numbered
where p.id = numbered.id;

-- 1d. 編號改成「每個分類群組各自獨立」，重新依分類群組(sort_group)分開編號 1,2,3...
--     這行可以重複執行，每次都會依目前的分類重新排一次
with numbered as (
  select id, row_number() over (partition by coalesce(sort_group, '') order by created_at asc) as rn
  from priced_items
)
update priced_items p
set item_no = numbered.rn
from numbered
where p.id = numbered.id;

-- 2.（舊版，保留給還沒升級的人參考，新安裝可以跳過）單一商品的銷售紀錄
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
  order_status text not null default '待處理',
  ship_by date,
  sold_at date not null default current_date,
  created_at timestamptz not null default now()
);

-- 3. 訂單（一張訂單 = 一個客人一次買的東西，可以有多樣商品）
create table if not exists orders (
  id uuid primary key default gen_random_uuid(),
  customer_name text not null default '',
  order_status text not null default '待處理',
  status_updated_at timestamptz not null default now(),
  ship_by date,
  shipping_fee numeric not null default 0,
  note text not null default '',
  photo_url text,
  sold_at date not null default current_date,
  created_at timestamptz not null default now()
);

-- 3b. 如果 orders 表已經建立過，單獨執行這行加「狀態最後更新時間」欄位：
alter table orders add column if not exists status_updated_at timestamptz not null default now();
alter table orders add column if not exists deleted_at timestamptz;

-- 4. 訂單裡的每一樣商品
create table if not exists order_items (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references orders(id) on delete cascade,
  item_name text not null default '',
  size text,
  photo_url text,
  cost numeric not null default 0,
  sold_price numeric not null default 0,
  created_at timestamptz not null default now()
);

create index if not exists order_items_order_id_idx on order_items(order_id);

-- 4b. 訂單編輯歷史(每次編輯或改狀態之前，先把當時的樣子存一份快照)
create table if not exists order_history (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references orders(id) on delete cascade,
  snapshot jsonb not null,
  edited_at timestamptz not null default now()
);

create index if not exists order_history_order_id_idx on order_history(order_id);

-- 5. 把舊的 sales_records 資料搬進新的 orders / order_items（只需執行一次）
--    如果這是全新安裝、sales_records 是空的，這段執行了也不會出錯，會直接跳過。
insert into orders (id, customer_name, order_status, ship_by, shipping_fee, note, photo_url, sold_at, created_at)
select
  id,
  customer_name,
  case order_status
    when '待付款' then '待處理'
    when '已付款' then '已下貨'
    when '已出貨' then '已出貨'
    when '已完成' then '已完成'
    else '待處理'
  end,
  ship_by, shipping_fee, note, photo_url, sold_at, created_at
from sales_records
where not exists (select 1 from orders where orders.id = sales_records.id);

insert into order_items (order_id, item_name, cost, sold_price, created_at)
select id, item_name, cost, sold_price, created_at
from sales_records
where not exists (select 1 from order_items where order_items.order_id = sales_records.id);

-- 這個工具是後端(Express 伺服器)用 service_role 金鑰連線,不會把 Supabase 金鑰交給瀏覽器,
-- 所以不需要另外設定 RLS 政策;service_role 本來就會繞過 RLS。

-- 6b. 登入狀態(session)表 —— 不用手動建，伺服器啟動時 connect-pg-simple 會自動建立(createTableIfMissing)。
--     這裡只是留紀錄：表名叫 session，欄位是 sid/sess/expire，跟這個工具本身的資料無關，可以放著不用管。

-- 6. 照片儲存空間(Storage bucket)
-- SQL Editor 沒辦法建立 bucket,請改在左側選單「Storage」手動建立:
--   Bucket 名稱:sales-photos
--   Public bucket:打勾(方便直接用網址顯示照片,商品照片不算敏感資料)
--   （這個 bucket 商品照片、訂單照片會共用，不用另外再建一個）
