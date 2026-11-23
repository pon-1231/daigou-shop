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
- **商品的顏色/規格清單排序是照固定色票，不是建立時間。** `pricing.html`（商品清單的「顏色:」摘要）跟 `records.html`（選商品時的尺寸/款式清單）各自有一份一模一樣的 `COLOR_ORDER` 常數 + `colorRank()` 函式（兩邊沒有共用模組，改的話兩個檔案都要改）：
  - `COLOR_ORDER = ['紅','粉','橘','橙','黃','綠','青','藍','紫','黑','白','棕','灰','金','銀','彩色']`
  - 完全比對得到的值（例如單純的「紅」）排前面；比對不到的複合色（「紅黑」「紫粉」「冰藍」「黑白」這種）改成找字串裡含哪個基礎色，歸類排在那個色系附近（「紅黑」含「紅」→排在「紅」旁邊）；再比對不到的（尺寸、款式名稱這種非顏色的值）維持原本相對順序排在最後，不受影響
  - 這是 2026-10-15 改的，之前是「第一次出現的順序」（＝建立時間），新增/編輯顏色會直接掉到清單最後面、跟相近色系斷開，使用者拿「冥河虞影 徽章展示架」這個商品的實際順序（紅、紅黑、粉、紫粉、橙、黃、綠、青、冰藍、淺藍、深藍、紫、黑、黑白、白）當驗收基準，改完兩個頁面都測過對得上
- **同一個 `item_no` 底下有好幾個不同名稱的商品（不是顏色/尺寸變體，是完全獨立的商品，只是想排在一起）時，使用者會自己在名稱最前面手動加「(1)」「(2)」「(3)」當排序依據**（例如 B 區 9 號的「(1) 暗黑毒蘋果 壓克力展示框（適用3.5寸）」「(2) ...適用5寸」「(3) ...適用6寸」、A 區 5 號的「(1) 哥德風叉子」「(2) 哥德風餐刀」）。`pricing.html`/`records.html` 的 `sortByItemNo()` 已經改成：item_no 相同時優先看名稱開頭的這個編號（`nameOrderPrefix()`），沒有編號才照建立時間排，兩個檔案一樣要一起改。2026-11-05 加的，之前同一個 item_no 底下只比建立時間，使用者手動編的號碼沒被看，畫面順序照樣會跑掉。
- **庫存可以跨規格共用（2026-11-10 加的功能）。** 情境：同一批貨拆成不同包裝賣（例如「可變色乾燥劑」的「50包/袋」整袋賣、「1包」拆開賣），兩個是獨立的 `priced_items` 列，但物理上是同一批庫存，不該各自算各自的數字。
  - `priced_items.stock_group_id`（nullable uuid）：同一組的商品填一樣的值，null 代表沒有共用（絕大多數商品都是 null，不受影響）
  - `priced_items.stock_unit_size`（預設 1）：這個規格的 1 個＝幾個「基本單位」，例如「50包/袋」填 50、「1包」填 1
  - `stock_movements.delta` 永遠存「基本單位」的量。進貨/調整(+/−)時前端會先乘上 `stock_unit_size` 再送出；「設定為」(盤點)例外，直接操作基本單位不乘；訂單結帳扣庫存在 `orders.js` 的 `applyOrderStockMovements()` 也會乘
  - `stock.js` 的 `sumStockByItem()` 算的時候會把同一個 `stock_group_id` 底下**所有**商品的異動紀錄（不管個別有沒有開 `track_stock`）加起來，同一組的每個規格顯示出來的庫存數字是一樣的
  - **目前沒有串 UI 讓使用者自己設定共用**（怕過度開發，用的人不多），要連結兩個規格直接用 `pg` 改這兩個欄位就好（參考 2026-11-10 的 commit）
  - 測過：顯示、進貨、調整、盤點設定、訂單扣帳、其中一個規格停止追蹤後另一個還是算得出共用池、不影響沒設定共用的一般商品，全部正常

---

## 7. 沒做 / 刻意不做的

- 「已出貨才扣庫存、待處理算佔用」的三數字版本 —— 使用者選了「訂單成立就扣」，別自作主張加回來。
- 舊 Render 服務的歸屬沒查出來，使用者說放著不管（現在用的是 `https://daigou-shop.onrender.com`）。
- commit `c3b54f3` 的訊息開頭多了一個 `@`（here-string 語法打錯），內容完整。要清掉得 force push，被使用者的 `permissions.deny` 擋著，就留著了。

---

## 8. 賣貨便訂單匯入（2026-11-16 加的功能，commit `2809cfe`）

新分頁 `/import.html`：上傳賣貨便匯出的 `.xlsx`，自動比對哪些訂單已經key過（略過）、哪些是新的，新訂單裡的商品再比對商品庫，比對有信心的直接可匯入、沒信心的標紅字讓使用者手動處理，確認後才真正建單。

**幾個刻意的設計決定，不要擅自改掉：**

- **去重靠訂單編號，不是比對商品或日期。** 使用者習慣把賣貨便的訂單編號接在顧客名稱後面（`handle/CM2611153257095`），所以掃現有 `orders.customer_name` 找 `CM\d{10,}` 就能準確判斷哪些已經key過。這個判斷方式完全依賴這個命名慣例——如果以後顧客名稱欄位不再帶訂單編號，這條路就會失效，要先確認這個慣例還在才能信任比對結果。
- **商品比對要兩個訊號都對才算有信心（`src/routes/importFeebee.js` 的 `matchItem()`）**：用「編號前綴（如 A3.）」鎖定 `sort_group`+`item_no` 縮小候選範圍，再同時核對「顏色字串有沒有出現在商品名稱裡」跟「賣貨便單價跟商品庫 `final_price` 是否一致」。只要有一個不成立，就整筆標 `needs_review` 讓人工選，不會因為編號對了就自動信任顏色或價格——這是故意保守，寧可多幾筆要人工確認，也不要悄悄建錯金額/成本的訂單。
- **`POST /api/import/feebee/parse` 只做解析+比對，不寫資料庫。** 確認匯入時前端是直接迴圈呼叫既有的 `POST /api/orders`（跟 `records.html` 既有的批次改狀態 `bulkApply` 一樣的模式），沒有另外寫 bulk-create 的後端端點，折價驗證、庫存扣帳全部沿用原本單筆建單的邏輯，零風險。
- **賣貨便匯出檔的 `!ref`（尺寸標籤）有時是壞的，只標到標題列。** `exceljs` 讀這個檔案不受影響（`worksheet.rowCount` 是對的，不是從壞掉的 `!ref` 算出來），所以解析時一律用 `rowCount` 往下讀，不要用任何依賴 `!ref` 的寫法（例如某些函式庫的 `sheet_to_json` 預設行為）。
- **審核畫面的手動輸入欄位同時聽 `input` 跟 `change`**（`import.html` 的 `onOrderListEvent`）。只聽 `change`（失焦才觸發）的話，使用者打完字直接點「確認匯入」有時候會來不及失焦，畫面看起來填好了但 `resolved` 狀態沒跟上——這是測試時真的踩到的坑，不是假設性風險。

沒做的：沒有做真正的 bulk-create 後端端點（前端逐筆呼叫，速度夠用，330 筆等級的量沒問題）；沒有處理 `.xls`（舊格式）或 CSV，只收 `.xlsx`。

### 8a. 賣貨便「訂單合併」偵測（2026-11-17 加的，commit `238c598`）

賣貨便的「訂單合併」：客人分兩次下單（A、B），賣貨便後台會多生一筆合併訂單（C），**A、B 不會被刪除**，匯出檔裡三筆都還在。如果照原本的去重邏輯（只看訂單編號本身在不在系統裡），A、B 會被當成兩筆全新訂單匯入，但這個客人的消費其實早就用合併後的 C 記過帳了——匯入 A、B 會讓金額重複算兩次。

- **賣貨便匯出的資料裡沒有明講「這筆是合併訂單」的欄位**，是用「同一個帳號 + 金額加總剛好對得上系統裡已存在的某筆訂單」反推（`detectMergeSuspects()`）。只在同一個帳號底下比對，不跨帳號比，避免金額剛好相同但其實無關的訂單被誤判。
- **使用者明確選了「標紅字讓我手動確認」**，不要改成自動略過或自動匯入：疑似合併的訂單會在卡片裡跳出紅色警告框，要勾「我確認過了，這筆真的要匯入」才能放行，邏輯位置在 `import.html` 的 commit 前檢查，跟商品 `needs_review` 紅字是分開但平行的兩道關卡。
- 真實驗證過的案例：帳號 `omomo_ouo` 的兩筆新訂單 290+810=1100，剛好等於系統裡已存在的訂單總額——這組數字只是這次踩到的真實例子，不是寫死在程式裡的規則。

### 8b. 「新增商品」改用圖片選圖面板（2026-11-17 加的，commit `f21137b`）

「新增商品」(例如補飛機盒這種包材) 原本只是陽春的文字搜尋+下拉選單，使用者反映體驗太簡陋、有東西搜不到，要求做到跟 `records.html` 新增訂單選商品一樣的圖片網格體驗。

- **`import.html` 的圖庫/選規格邏輯是直接從 `records.html` 搬過來的同一套算法**（`COLOR_ORDER`/`colorRank`/`groupOptions`/`groupProducts`/`sortByItemNo`/`groupBySortGroup`/`groupStock`），不是重新發明。以後 `records.html` 這幾個函式的排序/分組規則改了，`import.html` 要記得跟著改，不然兩邊挑商品的順序/分組會長得不一樣。
- `src/routes/importFeebee.js` 的 parse 端點 `catalog` 欄位因此改成**回傳完整商品庫原始欄位**（不是精簡摘要），前端才做得出二維顏色×尺寸分組。
- **故意沒搬過來的**：`records.html` picker 裡「新增尺寸／編輯這個尺寸的成本售價」那些商品庫管理功能，匯入頁面只需要「挑一個已存在的商品」，不需要能改商品庫本身。
- 共用一個 `#pickerPanel`（不是每個商品都各自一份 gallery），用 `state.picker.{orderIdx,itemIdx}` 記住現在是在幫哪張訂單、哪一行選；`itemIdx` 是 `null` 代表新增一行，不是 `null` 代表「編輯」時原地換掉那一行的 `resolution`（不會變成重複兩行）。
- 另外呼叫一次 `/api/stock` 取得庫存數字（只有開啟追蹤的商品才有），跟 `records.html` 的 `stockById` 同一套顯示邏輯（售完/低於門檻/正常三種顏色）。

### 8c. 淨利/贈品/備註/回購次數/可編輯名稱（2026-11-17 加的，commit `49f2d4a`）

**數量欄位改小會自動拆成兩筆，不是單純的數量編輯器。** 這是使用者講清楚實際情境才定案的：賣貨便同一筆 qty=2，客人常常在備註寫「兩個要不同顏色」，但系統只認qty數字、不會讀備註文字去拆。改成 `oi-qty-input` 的 `change` 事件裡，新數字比原本小就把差額拆成緊接在後面的新一行（`JSON.parse(JSON.stringify(it))` 深複製，兩行从此各自獨立解析/設贈品），數字變大則單純加數量，没有「從哪裡補」的對應關係不用特別處理。**不要改成單純的『改數字』，拆分這個副作用是刻意的。**

**贈品邏輯直接照搬 `records.html` 購物車的 `.cr-gift`**：`it.isGift` + `it.giftOriginalPrice` 備份，切成贈品時 `resolution.price` 歸零、`giftOriginalPrice` 存舊值，取消時換回來——不是用「售價===0」反推是不是贈品，這個決定從很早之前就定了，這次只是在匯入頁面重用同一套。

**每樣商品一律顯示完整卡片**（縮圖/名稱/規格/成本→售價），不再有「比對成功就收合成一行字」的省略顯示——`itemCardHtml()` 永遠畫，下面的下拉選單/搜尋/手動輸入控制區才按照原本的收合規則決定要不要顯示。

**回購次數的定義是「下這筆單當下，已經有幾筆更早的訂單」**，不是「總共買過幾次」——`importFeebee.js` 用 `repeatCountByHandle`（小寫比對，跟 `records.html` 的 `customerKey()` 同一套）；`records.html` 的既有訂單列表也補了同樣的提示，用 `created_at` 早於這筆的數量去算，兩邊定義要維持一致，不要有一邊改了另一邊沒跟著改。

**顧客名稱/訂單備註改成可編輯欄位時故意不整個 `renderNewOrders()`**（直接寫 state，commit 時才讀），原因跟 `pickerSearch` 一樣：重畫會把正在打字的欄位整個換掉、游標跟焦點都不見。

**選圖面板選完不自動關**：`applyPickerResult()` 不再呼叫 `closePicker()`，改成重置 `itemIdx=null`（下一次選預設變「新增」）再重畫，「✕ 完成」按鈕才是真正關閉的動作——一次訂單常常要連續補好幾樣東西（飛機盒+贈品+⋯），選一個就關掉要重開很煩。

## 9. `records.html` 刪除確認 + 編輯訂單誤判缺貨（2026-11-20 加的）

**刪除訂單（軟刪/永久刪）加了 `confirm()` 確認框**，訊息裡帶客人名稱。沒有特別的設計取捨，單純補一個使用者要求的安全網。

**編輯既有訂單時，購物車的「庫存不夠」警示原本會誤判成真的缺貨，其實沒有：**

- 根因：`stockOf(productId)` 讀的是 `/api/stock` 當下的**即時**庫存，而這張訂單早就存在資料庫裡、自己原本訂的量已經反映在這個即時數字上了。`syncOrderStock()`（`src/routes/orders.js`）的做法是「整批重算」——存檔時先把這張訂單的舊 `stock_movements` 全部刪掉再依新內容重新套用，所以如果編輯時數量沒超過原本訂的量，淨庫存變化其實是 0。但購物車渲染那段邏輯（`records.html` 大約 1264-1270 行）直接拿 `item.qty` 跟這個已經扣過自己的即時庫存比，等於多扣了一次，會錯誤顯示「庫存只剩 X 個，缺 Y 個」，即使實際上完全沒問題。
- 修法：進編輯模式時（`editOrder()`）把「這張訂單原本每個商品各訂了幾件」記進 `editingOriginalQtyByProduct`，離開編輯模式（`exitEditMode()`）要清空。購物車渲染算 `avail` 時，如果是編輯模式且這個商品有原本的配額，就把配額加回去再跟 `item.qty` 比，這樣「維持原數量不動」永遠不會誤判，但如果編輯時真的把數量往上調到超過「原配額+目前庫存」，警示依然會正確跳出來。
- 已用真實資料庫（紙箱庫存 7 → 下單扣 5 剩 2）驗證兩種情況：維持原數量 5 不跳警示；改成 8（超過原本配額 7）正確跳出「缺 1 個」。測試訂單事後刪乾淨。
- **這個邏輯只套用在「編輯既有訂單」，新增訂單（包括 `import.html` 匯入流程走的都是全新 `POST /api/orders`）不受影響**，因為新訂單沒有「原本配額」這個概念，`stockOf()` 的即時數字本來就是對的比較基準。

## 10. `import.html` 細節修正（2026-11-24 加的）

**商品候選選單一定要顯示完整商品名稱，不能只看顏色+價格。** 真實資料裡同一個 `item_no`（如 A5）底下可能有兩個完全不同的商品（例如「哥德風叉子」跟「哥德風餐刀」），剛好同色同價——選單原本只顯示顏色+價格，這種情況下兩個選項長得一模一樣，使用者分不出來、感覺像「名稱不完整」。`oi-candidate-select` 的 `<option>` 文字改成「完整商品名稱（顏色）· 價格」，`itemCardHtml()` 也加回原始排序編號徽章（`sortGroup+itemNo`，例如「A19」），兩邊都串到 `priced_items.sort_group`/`item_no`，從後端 `candidateView`（`importFeebee.js` 的 `buildCandidateMatch`）一路透傳到前端的 `resolution.sortGroup`/`itemNo`——所有會建立 `resolution` 的路徑（自動比對、下拉選單選、圖片選圖面板選）都要記得帶這兩個欄位，漏一個那個路徑選出來的商品就不會有編號徽章。

**價格一律用 `Math.round()` 顯示，不留小數點**：`import.html` 的 `fmt()` 原本沒有四捨五入（跟 `records.html`/`pricing.html` 的慣例不一樣），商品成本/售價如果是資料庫裡的原始浮點數（例如 `118.10699999999999`）就會整串顯示出來。手動輸入成本/售價欄位的預設值也補上 `Math.round()`，使用者看到的／沒特別去動的數字都會是整數。**實際存進 `order_items` 的 `cost`/`sold_price` 不受影響**——跟 `records.html` 的 `addProductToCart()` 一樣,是故意保留原始精度（只有畫面顯示跟使用者自己輸入的欄位要整數），不要為了這個需求去動 `buildItemsPayload()`。

**選商品的圖片選圖面板（`#pickerGallery`）滾動位置要保留，點了商品不能跳回最上面**：這個容器是 `orderCardHtml()` 動態插進訂單卡片裡的 DOM，不是頁面載入時就固定存在的節點——任何觸發 `renderNewOrders()` 整個訂單列表重畫的操作（設贈品、改數量、選下拉選單……）都會把舊的 `#pickerGallery` 整個砍掉重建成一個全新、`scrollTop=0` 的容器。一開始想用 `'scroll'` 事件監聽器記錄捲動位置，但在測試環境裡發現**程式設定 `scrollTop` 不會可靠觸發 `'scroll'` 事件**（非同步派送，重畫往往比事件先發生），改成在真正造成節點被砍掉的那一刻（`renderNewOrders()` 的 `$('orderList').innerHTML = ...` 之前、`pickerSearch` 的 `input` 事件處理常式裡）同步讀一次 `el.scrollTop` 存進模組變數 `pickerGalleryScrollTop`，`renderPickerGallery()` 本身只負責還原、不要在裡面覆寫這個變數（它拿到的 `el` 常常已經是剛重建的新節點，讀到的會是 0，把前面存好的值蓋掉）。`records.html` 的 `#productGallery` 不會被外部重畫砍掉、是固定節點，所以那邊維持原本「函式自己存+還原」的簡單寫法就夠。

**庫存提醒（「此商品剩餘 X 個，目前扣除 N 個庫存」）順便補了一個既有的時序 bug**：`/api/stock` 是在 `renderNewOrders()` 之後才打的，原本沒有任何東西在它回來之後重畫過畫面，導致剛解析完的當下庫存數字（跟圖庫的售完/剩餘標籤）都還沒出現，要等使用者隨便做點什麼觸發重畫才會補上來。現在 `/api/stock` 的 `.then()` 最後補一行 `renderNewOrders()`，解析完立刻就能看到庫存提醒。

## 11. 數量相關三個修正（2026-11-24 加的）

**商品卡片顯示的成本/售價改成「單價 × 數量」的總額，不是單價。** 原本 `itemCardHtml()` 的 `fmt(cost) + ' → ' + fmt(price)` 用的是 `itemEffectiveCost(it)`/`itemEffectivePrice(it)` 的原始回傳值（單價），但 `currentOrderTotal()`/`currentOrderProfit()` 算訂單總額時都有乘上 `it.qty`——使用者把數量從 1 調到 10，訂單總額正確變成 10 倍，但那張商品卡片上的數字卻紋風不動，看起來像「最上面對、下面這行是錯的」。修法很單純：`itemCardHtml()` 裡的 `cost`/`price` 直接乘上 `it.qty` 再顯示，是真實發生過的使用者回報，不是假設性問題。

**「新增商品」加的品項，改數量不應該觸發自動拆分。** `oi-qty-input` 的 `change` 事件原本只要數量變小就無條件拆成兩行（見本檔第 8c 節）——這個設計是為了解決賣貨便解析出來的商品「客人備註裡可能藏著要分兩色」的不確定性。但使用者自己用「新增商品」圖片選圖面板手動加的品項（`it.addedManually === true`），當下就已經確定規格了，調降數量只是單純的數量變動，不該也拆出一行一模一樣的重複商品，使用者會以為是刪不掉的 bug（實際上是新多出來一行要刪兩次才會乾淨）。現在拆分邏輯多判斷一個 `!it.addedManually` 條件。

**商品編號的「子編號」格式沒處理，賣貨便原始商品名稱比對失敗。** 商品庫同一個 `item_no` 底下有時塞了兩個完全不同的商品（例如 A12 底下「(1) 信中花語-玫瑰」跟「(2) 信中花語-向日葵」，用名稱開頭的「(1)」「(2)」人工排序/區分——跟 `records.html`/`pricing.html` 的 `nameOrderPrefix()` 同一套慣例），賣貨便匯出的編號因此會變成「A12-2.」這種「字母+主編號-子編號」格式。原本 `itemNoPrefix()` 的 regex 只認「字母+數字+句點」，碰到中間多一截「-2」會直接比對失敗（連 `matchByName()` 退路也救不回來，因為那段退路只會去掉開頭純數字編號，不會去掉字母開頭的編號），導致整筆商品明明商品庫裡找得到、卻一直顯示「編號跟名稱都比對不到」。修法：`itemNoPrefix()` 的 regex 多認一段可選的 `-(\d{1,2})` 子編號，`importFeebee.js` 新增跟前端同名的 `nameOrderPrefix()`，抓到子編號後先把候選商品縮小到名稱開頭數字符合的那一組，再進去跑原本的顏色+價格雙訊號比對——縮小不到（子編號在商品庫裡其實沒有對應）就退回用全部候選，不會比對更差。已經拿真實資料庫驗證過（A12-2.信中花語-向日葵...正確比對到「(2) 信中花語-向日葵」的「原色」/「白色」，A12-1.正確比對到「(1) 信中花語-玫瑰」，不會混到彼此）。
