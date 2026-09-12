# ictgold — ICT 黃金進場管線

一套把 ICT（Inner Circle Trader）概念變成**可測量、可回測、可稽核**的
XAUUSD 日內進場引擎。

> ⚠️ 這是一個研究與教學工具，不是投資建議，也不保證獲利。
> 在你用真錢之前，請先讀完 [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)。

**跟 `daigou-shop` 完全無關**：這個目錄自成一個 Python 專案，
沒有 import 任何賣場的程式，賣場也沒有 import 它。

> 📊 **已經在真實（非合成）黃金價格上跑過一次完整回測**，2012–2022 年的
> XAUUSD M15，固定 1:2R：525 筆、勝率 35.8%、期望值 **-0.160R（統計顯著為負，
> t=-3.32）**。**這不是「今年」的資料**——本沙盒環境目前抓不到 2026 年的即時
> 盤中資料，而且這次測試也順手抓到並修掉一個讓結果差很多的計分 bug。
> 原因、bug、完整結果全部寫在
> [`docs/BACKTEST_REAL_2012_2022.md`](docs/BACKTEST_REAL_2012_2022.md)，
> 請務必先讀這份文件再看任何數字。
>
> 🔍 **逐筆翻完那 525 筆之後的檢討在
> [`docs/TRADER_REVIEW.md`](docs/TRADER_REVIEW.md)**：問題不在盈虧比、
> 也不在一天進場幾筆，而在進場的執行方式——30% 的單在 75 分鐘內死掉，
> 貢獻 -175R；而不管目標放 1R、1.5R 還是 2R，命中率都差打平門檻約 21pp。
>
> 🔧 **照著這份檢討重寫進場執行之後（到價確認、修平倉時間、掛單不佔額度、
> 新聞黑名單）再測了一次，結果在
> [`docs/REWRITE_RESULTS.md`](docs/REWRITE_RESULTS.md)**：235 筆、期望值
> **-0.090R（t=-1.39，回到雜訊範圍，不再是顯著為負）**，賠率從 1.27 升到
> 1.53，快死單的損失從 -175R 降到 -29R（降了 84%）。**這是進步，不是修好**
> ——命中率跟打平門檻的缺口幾乎沒有改善，因為那個缺口的成因（bias 判斷、
> HTF/LTF 巢狀）這次沒有動。

---

## 為什麼是這樣設計的

ICT 的規則之所以難以驗證，是因為它們是**用人話描述的**——
「等流動性被掃」「等位移」「在折價區進場」。
人話沒辦法被證偽，所以第一件事是把每一句話翻譯成一行程式碼。

翻譯完之後，剩下的就只是測量。這個專案的全部價值在於：
**它讓「這個 ICT 想法到底有沒有用」變成一個可以被回答的問題。**

三個設計決定：

- **零第三方依賴。** 純標準函式庫。可以直接跑在 VPS、筆電或 web handler 裡，
  不需要 pandas、不需要建置流程。
- **每個否決都留下理由。** 訊號很少，但「為什麼沒有訊號」每一根 K 棒都有記錄。
  這是最好的除錯工具，也是最好的教材。
- **回測假設一律取悲觀值。** 同一根 K 同時碰到停損停利 → 算停損。
  樂觀的回測不是回測，是廣告。

---

## 快速開始

```bash
cd ict-gold

# 1. 合成資料煙霧測試（只驗證程式能跑，不代表任何績效）
python3 -m ictgold demo --bars 20000

# 2. 用真實資料回測
python3 -m ictgold backtest --csv xauusd_m5.csv --tz Europe/Athens \
    --out report.txt --trades journal.json

# 3. 樣本外驗證（唯一算數的成績）
python3 -m ictgold walkforward --csv xauusd_m5.csv --tz Europe/Athens --folds 5

# 4. 看最新一根 K 的判斷（輸出 JSON，給未來的網站用）
python3 -m ictgold scan --csv xauusd_m5.csv --tz Europe/Athens

# 5. 看某個時間點，七關各自的通過／否決理由
python3 -m ictgold explain --csv xauusd_m5.csv --tz Europe/Athens \
    --at 2024-06-03T13:35:00+00:00

# 6. 把回測的進出場畫成 K 線圖（HTML，開瀏覽器看）
python3 -m ictgold chart --csv xauusd_m5.csv --tz Europe/Athens \
    --trades journal.json --out chart.html

# 測試
python3 -m unittest discover -s tests
```

### 這條 pipe 會不會自己告訴你何時進出場、畫出來？

**`backtest` 會**：`--trades journal.json` 產出的每一筆都有進場／出場的
時間與價格，那是真實發生過的事，不是預測。**`chart` 會把它畫出來**——
K 線圖上標進場、停損、停利、出場點，用瀏覽器打開 `chart.html` 就能看，
可以照劇本／輸贏篩選（`--setup`、`--outcome`、`--limit`）。

**`scan` 會告訴你「現在」符不符合條件**：餵最新的 K 棒進去，
它會回報現在這一刻算不算一個訊號、entry/stop/target 是多少、
七關各自的通過或否決理由——但那是**你手動執行一次**才會得到的答案，
不是它在背景盯著盤幫你盯。

**這條 pipe 不會**：自動盯盤、自動下單、或是即時跳出提醒。
它是一個你餵資料進去、它吐報告出來的命令列工具，不是一個持續運行的
服務。要做到「即時通知」或「畫在 TradingView 上」，是
[`docs/ROADMAP.md`](docs/ROADMAP.md) 裡規劃、但故意排在後面的階段——
先把管線本身在歷史資料上證明可信，再談即時。

### ⚠️ `--tz` 是最容易出錯的參數

多數零售黃金資料是 **broker time（常見 UTC+2／+3）**，不是 UTC。
搞錯的話**每一個 killzone 都會平移數小時**，回測結果會看起來像個策略結論，
其實只是時鐘 bug。匯出資料時先確認時區，然後人工核對一次已知的倫敦開盤。

---

## 管線七關

```
① TIME      在殺戮時段內嗎？（紐約時間，含日光節約；08:30 NY 前後 15 分鐘的新聞黑名單）
② BIAS      H4 方向與目標？（M15 對齊則加分，相反則扣分）
③ LIQUIDITY 停損池被掃了嗎？（影線穿過 + 收盤回到內側）
④ STRUCTURE 然後結構轉變了嗎？（CHoCH／BOS + 位移）
⑤ PD ARRAY  折價區的進場點在哪？（FVG／OB／OTE）
⑥ RISK      停損、停利、R:R、倉位、硬性風控
⑦ SCORE     八因子信心分數門檻
```

順序不是排版：**先掃停損，才有轉折**。對調就是另一個想法了。

三個劇本（`judas_reversal`、`silver_bullet`、`ote_continuation`）共用這條管線，
細節見 [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md)。

---

## 回測報告讀什麼

```
win rate            : 42.1%  (16W / 22L / 0BE)
expectancy          : +0.184 R per trade
max drawdown        : 15.7 R  (7.6% of equity)
t-stat of expectancy: 2.31   (>2.0 before you believe it)
sample needed       : 187  (have 38)        <- 樣本還不夠，別下結論

BY KILLZONE / BY SETUP / BY DAY / BY SCORE  <- 改善策略靠這幾張表
MONTE CARLO: 95th pct drawdown 14.7 R       <- 用這個決定倉位大小
PIPELINE VETOES                             <- 策略死在哪一關
```

**先看 `sample needed`，再看 `win rate`。** 順序反過來就是大部分人虧錢的原因。

---

## 目錄結構

```
ictgold/
  core.py       K 棒、時間框架、重採樣、ATR
  sessions.py   殺戮時段（紐約時間，日光節約由 tz 資料庫處理）
  state.py      增量市場模型：擺動點／BOS-CHoCH／FVG／OB／流動性池／掃盤
  config.py     設定與三個劇本
  pipeline.py   ★ 七關管線本體
  backtest.py   事件驅動回測（悲觀成交假設）
  metrics.py    績效、分組統計、Monte Carlo、walk-forward
  data.py       CSV 載入（含 broker 時區）、合成資料產生器
  cli.py        指令列介面
docs/
  METHODOLOGY.md            ★ 訓練方法論：怎麼逼出統計優勢、怎麼不騙自己
  TRADER_REVIEW.md          ★ 逐筆翻完 525 筆之後的檢討與漏洞清單
  REWRITE_RESULTS.md        ★ 照著檢討重寫進場執行之後的前後對比
  WHAT_TO_FIX_NEXT.md       ★ 第二輪檢討：提前平倉為何無效、還剩哪些洞
  BACKTEST_REAL_2012_2022.md  真實資料回測的完整結果與限制
  PLAYBOOK.md               三個劇本的完整規格
  ROADMAP.md                接上 TradingView 的架構與順序
results/          真實資料回測的原始報告與逐筆交易紀錄
tests/            38 個測試，鎖住「前視偏差」等致命不變量
config/xauusd.json
```

---

## 三個不能被破壞的不變量

如果哪天有人改壞了這三件事，回測會開始顯示漂亮但假的績效：

1. **擺動點只在確認棒之後才公佈**（分型需要 N 根後續 K 棒才成立）
2. **高時框只餵已收盤的 K 棒**
3. **訂單永遠不會在產生訊號的那根 K 成交**

`tests/test_engine.py` 專門鎖這三件事。**跑測試比讀報告重要。**

---

## 現實的期待值

固定 1:2R 之後（見下方），賠率會比理論上的「盈虧比拉到下一個流動性池」低：
目標沒打到就被收盤平倉、或先摸到保本出場的次數不會是零，實際拿到的平均
賠率通常落在 **1.2–1.7**，不是乾淨的 2.0。這不是 bug，是固定盈虧比的必然代價。

| 指標 | 合理範圍 |
|---|---|
| 勝率 | 35–50% |
| 賠率（實拿） | 1.2–1.7（目標固定 1:2R，但不是每筆都吃滿） |
| 期望值 | 0 ~ +0.2 R |
| 年交易數 | 60–150（⚠️ 加了到價確認之後實際只有 **23**，見下） |
| 最大回撤 | 10–20 R |

⚠️ **樣本數現在是這個專案最大的限制**：到價確認把 3989 個訊號篩到 235 筆成交
（10 年、一年 23 筆）。任何再收緊的改動都會讓統計失去意義——
細節與後續建議見 [`docs/WHAT_TO_FIX_NEXT.md`](docs/WHAT_TO_FIX_NEXT.md)。

**如果回測顯示 70% 勝率、期望值 0.8R，最可能的解釋是資料洩漏，不是聖杯。**

真實（非合成）資料上的一次完整測試結果在
[`docs/BACKTEST_REAL_2012_2022.md`](docs/BACKTEST_REAL_2012_2022.md)：
勝率 35.8%、實拿賠率 1.27、期望值 **-0.160R，t=-3.32（統計顯著為負）**——
比上面這個表的範圍更差，而且這次樣本數夠、t 值也過關，**不是雜訊**。
但這份資料本身有三個限制（不是今年、時區用猜的、M15 不是 M5），
所以還不能直接推論到「這個 pipe 現在不能用」，見該文件第 5 節。
