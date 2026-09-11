# 下一步：接上 TradingView 的架構規劃

你提到「成功之後架一個檢測網站，讓你跟 TradingView 連通」。
這件事技術上不難，難的是**接的順序**。先講結論：

> **不要先接 TradingView。** 先讓引擎在歷史資料上證明自己，
> 再接即時資料。順序反了，你會拿一個還沒驗證的策略去看即時盤，
> 然後憑感覺改它——那就回到原點了。

---

## 階段規劃

### 階段 1（現在）— 離線驗證
```bash
python3 -m ictgold backtest --csv xauusd_m5.csv --tz <broker_tz>
python3 -m ictgold walkforward --csv xauusd_m5.csv --tz <broker_tz>
```
**通過條件**：樣本外期望值為正、t 值 > 2、樣本數 > 200。
沒過就不要往下走，往下走只是把問題搬到更貴的地方。

### 階段 2 — 唯讀儀表板（沒有下單）
一個小服務，定時抓 M5 資料 → 跑 `scan` → 把七關的通過／否決狀態畫成介面。

`scan` 已經直接輸出這個介面需要的 JSON：

```json
{
  "symbol": "XAUUSD",
  "as_of": "2024-06-03T13:35:00+00:00",
  "price": 2332.15,
  "signal": null,
  "setups": [
    {"setup": "judas_reversal", "fired": false, "veto_stage": "structure",
     "trace": [
       {"stage": "time", "passed": true, "reason": "in ny_am on Mon"},
       {"stage": "structure", "passed": false, "reason": "no CHOCH bullish in last 12 bars"}
     ]}
  ]
}
```

**這個階段的價值不是賺錢，是讓你每天看到「它在想什麼」。**
你交易知識的缺口，會在這個階段被補起來。

### 階段 3 — TradingView 雙向連通

兩個方向，用途完全不同：

**(a) TradingView → 引擎（webhook 觸發）**
```
TradingView alert ──webhook──> /api/tv-hook ──> ictgold.scan ──> 回傳訊號
```
適合把 TradingView 當作「便宜的即時報價與觸發器」。
注意 TradingView 的 webhook **沒有簽章機制**，一定要自己加共享密鑰 + 來源 IP 白名單。

**(b) 引擎 → TradingView（畫圖驗證）**
把 `state.py` 的定義移植成一支 Pine Script 指示器，只畫圖、不下單。
用途是**視覺對帳**：Python 認定的 FVG／sweep／CHoCH，跟你在圖上看到的是不是同一個東西。

> 這一步是**必要的**，不是加分項。視覺對帳是你抓出定義錯誤最快的方法，
> 而定義錯誤是這類系統最常見、也最難用數字發現的 bug。

### 階段 4 — 半自動執行
引擎出訊號 → 推播到手機 → **由你按下確認** → 才送單。

全自動留到最後。不是因為技術難，是因為**全自動會讓你停止學習**，
而你現在最需要的恰恰是學習。

---

## 架構建議

```
┌──────────────┐   M5 OHLCV    ┌──────────────┐
│ 資料來源      │ ────────────> │  ictgold     │
│ (broker API) │               │  引擎 (純 py) │
└──────────────┘               └───────┬──────┘
                                       │ scan() -> JSON
                          ┌────────────┴────────────┐
                          ▼                         ▼
                   ┌─────────────┐          ┌──────────────┐
                   │ 網頁儀表板   │          │ 推播 / 告警   │
                   │ (七關可視化) │          │ (人工確認)    │
                   └─────────────┘          └──────────────┘
```

引擎**零第三方依賴**就是為了這個：它可以直接跑在任何 web handler 裡，
不需要 pandas、不需要建置流程、不需要容器裡塞一堆東西。

```python
from ictgold import Config, EntryPipeline
from ictgold.cli import _build_models

cfg = Config.default()
ltf, htf, mtf = _build_models(cfg, recent_candles)   # 最近 ~3000 根 M5
signal, results = EntryPipeline(cfg).best(ltf, htf, equity, mtf)
```

---

## 幾個一定要先想清楚的問題

1. **即時 K 棒未走完的問題**：引擎只吃**已收盤**的 K 棒。
   即時串流一定要丟掉當前未完成的那根，否則你的即時行為跟回測不一致
   （這是實盤與回測對不起來最常見的原因）。
2. **資料源必須跟回測同一家**：不同 broker 的黃金報價差幾毛錢，
   FVG 和掃盤的判定就可能不同。
3. **延遲預算**：M5 模型在收盤後有 1–2 秒決策時間綽綽有餘，不需要為速度做任何優化。
4. **絕對不要把下單金鑰放在網頁服務裡。** 儀表板唯讀，下單走另一個隔離的程序。
