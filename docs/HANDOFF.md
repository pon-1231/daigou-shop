# daigou-shop 交接筆記（2026-10-03）

新的 session 接手時先讀這份。專案本身的使用說明看 [README.md](../README.md)。

---

## 1. 這次 session 做了什麼

### 庫存系統上線（commit `c3b54f3`，已 push）

新增第三個分頁 `/stock.html`「庫存管理」。四個設計決定是跟使用者討論後定的，**不要擅自改掉**：

| 決定 | 選的做法 | 為什麼 |
|---|---|---|
| 經營模式 | 兩種混用 | 有些商品囤現貨、有些客人下單才去中國調貨 → 所以有 `track_stock` 開關，不是所有商品都管庫存 |
| 扣帳時機 | **訂單成立就扣** | 最直覺。沒有做「已出貨才扣 / 先算佔用」那套三個數字的版本 |
| 進貨紀錄 | **留流水帳** | 可追溯「為什麼只剩 3 個」，而且做法上不會出 bug |
| 庫存 0 | 還是能加入購物車 | 代購本來就能預購，只顯示紅字警告 |

**核心架構（兩句話）：**

1. **「目前庫存」不是一個欄位，而是 `stock_movements` 這張流水帳的 `delta` 加總。** 這樣永遠不會出現「欄位數字跟進出貨紀錄對不上」。330 個 SKU 的量級，每次 API 全撈回來自己加完全沒負擔（`src/routes/stock.js` 的 `sumStockByItem()`）。
2. **訂單的庫存異動跟著 `order_id` 整批重算**，跟 `order_items` 本來就是 replace-all 同一套策略（`src/routes/orders.js` 的 `syncOrderStock()`）。所以編輯訂單、丟垃圾桶、從垃圾桶還原**全部自動正確**，程式裡沒有任何「比對前後差異」的邏輯 —— 要改這塊之前先想清楚，這是刻意的。

**其他要知道的：**
- `track_stock` 預設 **false**。330 個 SKU 一次全開，使用者會看到 330 筆「庫存 0」紅色警示。按「+進貨」會自動幫忙打開追蹤。
- 低庫存門檻：全站預設 5（`stock.js` 的 `DEFAULT_LOW_STOCK_THRESHOLD`），每個商品可用 `low_stock_threshold` 覆寫，null 就是用預設。
- `order_items.priced_item_id` 是這次新加的，**是整個自動扣庫存的地基**。前端購物車本來就帶著 `product.id`，只是送出時被丟掉，現在補上了。舊訂單沒有這個欄位，所以不會回頭扣歷史訂單的庫存 —— 這是對的，進貨時填的數字就是「現在手上實際有幾個」，已經反映過歷史銷售了。
- 商品清單縮圖一定要 `loading="lazy"`。測試時展開 330 筆沒有 lazy load 直接把瀏覽器卡死。

### Schema 改動

已經**直接跑在線上資料庫**了（用 `pg` 直連，不是請使用者貼 SQL），同時也補進 `supabase-schema.sql` 的 `4c.` 區塊留檔。

```
priced_items.track_stock            boolean not null default false
priced_items.low_stock_threshold    integer              -- null = 用全站預設 5
order_items.priced_item_id          uuid references priced_items(id) on delete set null
stock_movements (新表)               -- delta / reason / order_id / note
```

---

## 2. ✅ Supabase 搬家（2026-10-03 已完成）

舊專案 `zokxrgmofiqbobtxycbs`（不屬於使用者帳號，管不到）已經搬到使用者自己帳號（`ansonhsiao2002-8336's projects`）底下的新專案 **`buocrbnwoisidzlkqouu`**（region: ap-southeast-1）。

**做完的事**：
1. `supabase-schema.sql` 跑進新專案建表
2. 資料整批搬完，筆數核對一致：`priced_items` 383、`orders` 18、`order_items` 154、`order_history` 12、`stock_movements` 4、`sales_records` 5；外鍵無孤兒紀錄
3. Storage `sales-photos` bucket（public）另建 + 60 張圖全部搬過去，DB 裡 `photo_url` 的網域已批次改成新專案
4. 本機 `.env` 與 Render 的 `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `DATABASE_URL` 三個環境變數都已更新，Render 重新部署過，網站確認正常

舊專案還是留著當備援（反正也刪不掉），備份 `C:\Users\陳有朋\Desktop\daigou-shop-備份-2026-10-03\`（7 張表 JSON，不含 Storage 圖片）也留著。**這件事不用重做。**

---

## 3. 操作這個專案最有用的一招

`.env` 的 `DATABASE_URL` 可以直接用 `pg` 連，身分是 **`postgres` 最高權限**：

```bash
cd "C:/Users/陳有朋/Desktop/daigou-shop" && node -e "
require('dotenv').config();
const { Pool } = require('pg');
const pool = new Pool({ connectionString: process.env.DATABASE_URL, ssl: { rejectUnauthorized: false } });
pool.query('select count(*) from priced_items').then(r => { console.log(r.rows); return pool.end(); });
"
```

DDL、資料稽核、搬家全部不需要使用者進 Supabase 網頁貼 SQL。這招在這個專案裡用途很大。

---

## 4. 本機怎麼跑 / 怎麼測

- `.claude/launch.json` **沒有** daigou-shop 的設定（只有別的專案的），所以 `preview_start({name:"daigou-shop"})` 會啟動錯的專案。要自己 `cd daigou-shop && npm start`（port 3100），再 `preview_start({url:"http://localhost:3100"})`。
- 登入密碼在 `.env` 的 `APP_PASSWORD`。
- 訂單 route 用 **multer**，只吃 `multipart/form-data`。寫測試腳本時要用 `FormData`，送 urlencoded 會得到「訂單至少要有一樣商品」。
- 2026-10-03 這次 Browser 工具的 **screenshot 一直 timeout**（頁面本身正常回應 JS）。驗證前端改用 `javascript_tool` 讀 DOM，一樣能真實觸發事件委派。
- **測試一定要清乾淨**。這是有真實資料在用的專案（330 筆商品、12 張有效訂單）。測試商品固定用 `【測試用-可刪除】` 前綴 + `sort_group='__TEST__'`，跑完用 `/permanent` 硬刪。

---

## 5. 使用者的習慣與偏好

- 講**繁體中文**。本名陳有朋，叫「有朋」。
- **「之後我說好了都幫我push」** —— 這個專案測試確認沒問題後，不用再問就能 commit + push。
- 會直接丟截圖報 bug，期待你看圖找出問題，不要反問一堆。
- **數字要自己重算，不要憑記憶**。之前我講錯編號，他直接問「為什麼不是 11」，是他對的。遇到數字對不上就重新查一次線上資料。
- 刪檔案優先用 `~/.claude/scripts/trash.ps1` 丟資源回收桶，不要直接硬刪。

---

## 6. 幾個容易搞混的點

- **`sort_group`（分類群組 A~E）跟 `category_label`（價格級距：小額/中價/高價）是兩回事。** `item_no` 是在 `sort_group` 裡各自獨立編號的。這兩個「分類」我之前搞混過好幾次。
- `priced_items` 目前 **1 列 = 1 個規格組合**，`sizes`/`color` 含逗號多值的舊資料已經是 **0 筆**。所以庫存可以直接掛在 `priced_items` 上，一列就是一個 SKU。
- `order_items` **沒有數量欄位**，一件存一行。records.html 是「送出時展開、讀回來合併」做出數量的感覺（commit `28b058d`）。庫存流水帳則會把同商品合併成一筆 `-N`，不是 N 筆 `-1`。
- 免費方案：**Render 只會休眠不會消失**（醒來等 30–60 秒）；**Supabase 閒置約一週會暫停**。想一勞永逸就用 UptimeRobot 每 5 分鐘 ping `https://daigou-shop.onrender.com`，兩個問題一起解決。

---

## 7. 沒做 / 刻意不做的

- 「已出貨才扣庫存、待處理算佔用」的三數字版本 —— 使用者選了「訂單成立就扣」，別自作主張加回來。
- 舊 Render 服務的歸屬沒查出來，使用者說放著不管（現在用的是 `https://daigou-shop.onrender.com`）。
- commit `c3b54f3` 的訊息開頭多了一個 `@`（here-string 語法打錯），內容完整。要清掉得 force push，被使用者的 `permissions.deny` 擋著，就留著了。
