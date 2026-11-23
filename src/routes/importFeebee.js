const express = require('express');
const multer = require('multer');
const ExcelJS = require('exceljs');
const router = express.Router();
const supabase = require('../supabaseClient');

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 15 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    const isXlsxMime = file.mimetype === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    const isXlsxExt = /\.xlsx$/i.test(file.originalname || '');
    if (!isXlsxMime && !isXlsxExt) {
      return cb(new Error('只能上傳賣貨便匯出的 .xlsx 檔案'));
    }
    cb(null, true);
  }
});

// 標題文字可能帶換行（例如「商品名稱\n(品名/規格)」），比對前先拿掉換行再比對。
const HEADER_ALIASES = {
  '訂購日期': 'orderDate',
  '訂單編號': 'orderNo',
  '狀態': 'status',
  '收件人姓名': 'customerMasked',
  '商品名稱(品名/規格)': 'itemName',
  '單價': 'unitPrice',
  '優惠價': 'discPrice',
  '數量': 'qty',
  '運費(B)': 'shippingFee',
  '使用賣家折價券抵扣(C)': 'discC',
  '使用平台運費券折抵(D)': 'discD',
  '使用平台折價券折抵(E)': 'discE',
  '回饋資訊1': 'feedback1',
  '其它資訊(FB/LINE/IG帳號)': 'feedback1',
  '訂單備註': 'note',
  '商品備註': 'note'
};

function normalizeHeader(v) {
  return String(v == null ? '' : v).replace(/[\r\n]+/g, '').trim();
}

function cellText(cell) {
  const v = cell ? cell.value : null;
  if (v == null) return '';
  if (typeof v === 'object') {
    if (v.richText) return v.richText.map((t) => t.text).join('');
    if (v.result != null) return String(v.result);
    if (v instanceof Date) return v.toISOString().slice(0, 10);
    return '';
  }
  return String(v);
}

/* 賣貨便匯出的 xlsx 有時候 !ref（尺寸標籤）壞掉，只標到標題列，實際資料遠遠不止——
   不能信標籤，要自己找「真的有資料」的標題列再往下讀。 */
function findHeaderRow(worksheet) {
  const maxScan = Math.min(worksheet.rowCount || 10, 10);
  for (let r = 1; r <= maxScan; r++) {
    const row = worksheet.getRow(r);
    const colMap = {};
    let hitCount = 0;
    row.eachCell({ includeEmpty: false }, (cell, colNumber) => {
      const key = HEADER_ALIASES[normalizeHeader(cell.value)];
      if (key && !colMap[key]) {
        colMap[key] = colNumber;
        hitCount++;
      }
    });
    if (colMap.orderNo && colMap.itemName && colMap.unitPrice) {
      return { rowNumber: r, colMap, hitCount };
    }
  }
  return null;
}

function parseSheet(worksheet) {
  const header = findHeaderRow(worksheet);
  if (!header) return [];
  const rows = [];
  const lastRow = worksheet.rowCount || header.rowNumber;
  for (let r = header.rowNumber + 1; r <= lastRow; r++) {
    const row = worksheet.getRow(r);
    const get = (key) => (header.colMap[key] ? cellText(row.getCell(header.colMap[key])) : '');
    const orderNo = get('orderNo').trim();
    if (!orderNo) continue;
    rows.push({
      orderNo,
      orderDate: get('orderDate').trim(),
      status: get('status').trim(),
      customerMasked: get('customerMasked').trim(),
      itemName: get('itemName').trim(),
      unitPrice: Number(get('unitPrice')) || 0,
      discPrice: Number(get('discPrice')) || 0,
      qty: Math.max(1, Math.round(Number(get('qty')) || 1)),
      shippingFee: Number(get('shippingFee')) || 0,
      discC: Number(get('discC')) || 0,
      discD: Number(get('discD')) || 0,
      discE: Number(get('discE')) || 0,
      feedback1: get('feedback1').trim(),
      note: get('note').trim()
    });
  }
  return rows;
}

function groupIntoOrders(rows) {
  const byOrderNo = {};
  const order = [];
  rows.forEach((r) => {
    if (!byOrderNo[r.orderNo]) {
      byOrderNo[r.orderNo] = {
        orderNo: r.orderNo,
        date: r.orderDate.replace(/\//g, '-'),
        status: r.status,
        customerMasked: r.customerMasked,
        feedback1: r.feedback1,
        note: r.note,
        shippingFee: r.shippingFee,
        discC: r.discC,
        discD: r.discD,
        discE: r.discE,
        items: []
      };
      order.push(byOrderNo[r.orderNo]);
    }
    byOrderNo[r.orderNo].items.push({
      rawName: r.itemName,
      qty: r.qty,
      unitPrice: r.discPrice || r.unitPrice
    });
  });
  return order;
}

/* 「訂購日期」欄位只有日期，實際下單時間藏在「狀態」欄位裡，例如
   「訂單成立\n(11/15 23:46)\n」。同一天常常有好幾筆訂單，只比日期的話
   同一天的訂單還是會照檔案原本的順序（賣貨便通常新的在前），排序排不準。 */
function orderDateTimeKey(o) {
  const m = String(o.status || '').match(/\((\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})\)/);
  if (!m) return o.date + 'T00:00';
  const pad = (s) => String(s).padStart(2, '0');
  const year = o.date.slice(0, 4);
  return year + '-' + pad(m[1]) + '-' + pad(m[2]) + 'T' + pad(m[3]) + ':' + m[4];
}

// arr 所有大小 >= 2 的子集合，客人在同一批裡重複下單通常不會超過幾筆，暴力列舉沒問題。
function subsetsOfSizeTwoPlus(arr) {
  const result = [];
  const n = arr.length;
  for (let mask = 1; mask < (1 << n); mask++) {
    const subset = [];
    for (let i = 0; i < n; i++) if (mask & (1 << i)) subset.push(arr[i]);
    if (subset.length >= 2) result.push(subset);
  }
  return result;
}

/* 疑似「訂單合併」偵測：同一個帳號在這批裡有好幾筆新訂單，如果其中幾筆的金額
   加總剛好對上系統裡已經記過的某一筆訂單，很可能那筆就是賣貨便後台合併出來的
   C 訂單，這幾筆是還沒刪除的 A、B。只在「同一個帳號」底下比對，不跨帳號比，
   避免金額剛好相同但其實無關的訂單被誤判。 */
function detectMergeSuspects(candidateNew, existingByHandle) {
  const byHandle = {};
  candidateNew.forEach((o) => {
    if (!o.handle) return;
    (byHandle[o.handle] = byHandle[o.handle] || []).push(o);
  });

  const result = {};
  Object.keys(byHandle).forEach((handle) => {
    const existingList = existingByHandle[handle];
    const group = byHandle[handle];
    if (!existingList || !existingList.length || group.length < 2 || group.length > 6) return;

    subsetsOfSizeTwoPlus(group).some((subset) => {
      const sum = subset.reduce((s, o) => s + o.orderTotal, 0);
      const match = existingList.find((e) => Math.abs(e.total - sum) < 0.01);
      if (!match) return false;
      subset.forEach((o) => {
        result[o.orderNo] = {
          intoOrderNo: match.orderNo,
          combinedWithOrderNos: subset.map((s) => s.orderNo).filter((no) => no !== o.orderNo),
          combinedTotal: sum
        };
      });
      return true; // 找到一組就夠了，同一個帳號不用窮舉所有可能都標一遍
    });
  });
  return result;
}

/* feedback1 常常是問卷式文字，例如「您的threads帳號？（...）：ssannn._.9」，
   帳號在最後一個全/半形冒號後面；訂單匯入分頁的「其它資訊」有時就是單純帳號本身。 */
function parseHandle(raw) {
  if (!raw) return null;
  const idx = Math.max(raw.lastIndexOf('：'), raw.lastIndexOf(':'));
  const text = idx === -1 ? raw : raw.slice(idx + 1);
  const trimmed = text.trim();
  return trimmed || null;
}

function itemNoPrefix(rawName) {
  // 同一個編號底下如果商品庫塞了好幾個不同商品（例如 A12 底下有「信中花語-玫瑰」
  // 跟「信中花語-向日葵」），賣貨便匯出的編號會多一段「-子編號」(例如 A12-2.)，
  // 子編號對應的是商品庫名稱開頭手動加的「(1)」「(2)」排序前綴（跟 records.html/
  // pricing.html 的 nameOrderPrefix() 同一套慣例），不是另一層獨立的語法。
  const m = String(rawName || '').match(/^([A-Za-z]{1,3})(\d{1,3})(?:-(\d{1,2}))?[.．]\s*(.+)$/);
  if (!m) return null;
  return { group: m[1].toUpperCase(), itemNo: Number(m[2]), subIndex: m[3] ? Number(m[3]) : null, remainder: m[4].trim() };
}

// 跟 records.html/pricing.html 的 nameOrderPrefix() 同一套規則：名稱開頭手動加的
// 「(1)」「(2)」決定同一個 item_no 底下好幾個商品的排序/子編號。
function nameOrderPrefix(name) {
  const m = /^[（(]\s*(\d+)\s*[）)]/.exec(name || '');
  return m ? Number(m[1]) : null;
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 有些商品只用 color 欄位存顏色；有些商品（例如同一個 item_no 底下同時有
// 「款式」跟「顏色」兩種變化）實際上是把「款式」存進 color/spec1_value，
// 真正的顏色反而存在 sizes/spec2_value——欄位怎麼用要看商品當初怎麼建。
// 只看 c.color 會漏掉真正的顏色資訊，候選清單上看起來只有款式、沒有顏色，
// 使用者會分不出到底哪個對應哪個顏色。這裡把「這個候選商品所有有填的
// 描述欄位」去重後全部收集起來，缺一個都不行。
function descriptorsOf(c) {
  const descs = [];
  [c.color, c.sizes, c.spec1_value, c.spec2_value].forEach((v) => {
    const t = (v || '').toString().trim();
    if (t && descs.indexOf(t) === -1) descs.push(t);
  });
  return descs;
}

function buildCandidateMatch(candidates, remainder, unitPrice) {
  const candidateView = candidates.map((c) => {
    const descs = descriptorsOf(c);
    return {
      id: c.id,
      name: c.name,
      // 顯示/比對都用「完整」描述（可能是款式+顏色兩段），不是只挑一個欄位，
      // 不然候選選單上會看不出顏色，使用者沒辦法選。
      color: descs.join('・'),
      descriptors: descs,
      cost: Number(c.actual_cost) || 0,
      price: Number(c.final_price) || 0,
      photoUrl: c.photo_url || null,
      sortGroup: c.sort_group || null,
      itemNo: c.item_no != null ? c.item_no : null
    };
  });

  // 候選的「每一段」描述都要在賣貨便原始文字裡找得到，才算這個候選對得上——
  // 只要求「其中一段」對得上的話，同一個編號下不同款式但顏色剛好一樣的候選
  // 會全部誤判成候選，反而製造新的「比對到多個顏色候選」假歧義。
  const colorMatches = candidateView.filter((c) => c.descriptors.length > 0 && c.descriptors.every((d) => remainder.includes(d)));
  const strongMatches = colorMatches.filter((c) => Math.abs(c.price - unitPrice) < 0.01);

  if (strongMatches.length === 1) {
    return { status: 'matched', reason: null, matched: strongMatches[0], suggestedId: strongMatches[0].id, candidates: candidateView };
  }
  // 編號+顏色都只對到一個候選，只是價格跟商品庫對不起來——這種情況通常是賣場
  // 調整過售價、但商品庫（或賣貨便那邊）還沒跟著更新，不是真的比對不準，不需要
  // 卡成人工確認。改成直接採用、售價以賣貨便這次匯出的 unitPrice 為準（成本還是
  // 用商品庫的 actual_cost，賣貨便沒有成本資訊），但標記 priceMismatch 讓前端用
  // 不擋匯入的提示文字顯示出來，方便使用者自己判斷是不是真的調價、還是漏按贈品
  // 之類的操作失誤。
  if (colorMatches.length === 1) {
    const base = colorMatches[0];
    const resolved = Object.assign({}, base, { price: unitPrice, priceMismatch: true, catalogPrice: base.price });
    return { status: 'matched', reason: 'price_mismatch', matched: resolved, suggestedId: base.id, candidates: candidateView };
  }
  if (colorMatches.length > 1) {
    return { status: 'needs_review', reason: 'ambiguous_color', matched: null, suggestedId: null, candidates: candidateView };
  }
  return { status: 'needs_review', reason: 'no_color_match', matched: null, suggestedId: null, candidates: candidateView };
}

/* 不是每個賣場都用「字母+數字」編號（例如 A3.），有些賣場匯出只有純數字
   編號（2.）甚至完全沒有編號——這種情況退而求其次，去掉開頭可能的純數字
   編號後，拿剩下的文字跟商品庫的 name 做「最長前綴」比對，比對到了再用
   一樣的顏色+價格雙訊號判斷，不會因為編號系統不同就直接放棄自動比對。 */
function matchByName(rawName, unitPrice, nameIndex, sortedNames) {
  const afterNumPrefix = rawName.replace(/^\d{1,3}[.．]\s*/, '').trim();
  const nameMatches = [];
  sortedNames.forEach((name) => {
    const re = new RegExp('^' + escapeRegex(name).replace(/\s+/g, '\\s*') + '\\s*(.*)$');
    const m = afterNumPrefix.match(re);
    if (m) nameMatches.push({ name, remainder: m[1].trim() });
  });
  if (nameMatches.length === 0) {
    return { status: 'needs_review', reason: 'no_prefix', matched: null, suggestedId: null, candidates: [] };
  }
  const maxLen = Math.max.apply(null, nameMatches.map((m) => m.name.length));
  const longest = nameMatches.filter((m) => m.name.length === maxLen);
  if (longest.length > 1) {
    return { status: 'needs_review', reason: 'ambiguous_name', matched: null, suggestedId: null, candidates: [] };
  }
  return buildCandidateMatch(nameIndex[longest[0].name], longest[0].remainder, unitPrice);
}

function matchItem(rawItem, priceIndex, nameIndex, sortedNames) {
  const prefix = itemNoPrefix(rawItem.rawName);
  if (!prefix) {
    return matchByName(rawItem.rawName, rawItem.unitPrice, nameIndex, sortedNames);
  }
  let candidates = priceIndex[prefix.group + ':' + prefix.itemNo] || [];
  if (candidates.length === 0) {
    return { status: 'needs_review', reason: 'unknown_item_no', matched: null, suggestedId: null, candidates: [] };
  }
  // 「A12-2.」的 -2 是子編號，先用它把候選縮小到名稱開頭「(2)」的那個商品，
  // 不然同一個 item_no 底下的好幾個不同商品會混在一起比對顏色/價格。
  if (prefix.subIndex != null) {
    const narrowed = candidates.filter((c) => nameOrderPrefix(c.name) === prefix.subIndex);
    if (narrowed.length > 0) candidates = narrowed;
  }
  return buildCandidateMatch(candidates, prefix.remainder, rawItem.unitPrice);
}

router.post('/feebee/parse', (req, res) => {
  upload.single('file')(req, res, async (uploadErr) => {
    if (uploadErr) return res.status(400).json({ error: uploadErr.message });
    if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
    if (!req.file) return res.status(400).json({ error: '沒有收到檔案' });

    try {
      const wb = new ExcelJS.Workbook();
      await wb.xlsx.load(req.file.buffer);

      let rawRows = [];
      const sheetsRead = [];
      wb.worksheets.forEach((ws) => {
        const parsed = parseSheet(ws);
        if (parsed.length > 0) {
          sheetsRead.push(ws.name);
          rawRows = rawRows.concat(parsed);
        }
      });
      if (rawRows.length === 0) {
        return res.status(400).json({ error: '這份檔案裡找不到任何訂單資料（檢查一下是不是匯出的分頁都是空的）' });
      }

      const grouped = groupIntoOrders(rawRows);

      const { data: existingOrders, error: ordersErr } = await supabase
        .from('orders')
        .select('customer_name, order_items(sold_price)')
        .is('deleted_at', null);
      if (ordersErr) throw ordersErr;
      const existingOrderNos = new Set();
      // 賣貨便「訂單合併」：客人分兩次下單(A、B)，賣貨便後台會多生一筆合併訂單(C)，
      // A、B 不會被刪除，匯出檔裡三筆都還在。用「同一個帳號 + 金額加總剛好對得上
      // 系統裡已經記過的某筆訂單」反推，抓到的話整筆標記讓人工確認，不自動略過也
      // 不自動匯入——這只是推論，不是賣貨便資料裡有明講的欄位。
      const existingByHandle = {};
      // 回購次數跟 records.html 的 customerKey() 同一套（小寫比對），獨立一份、
      // 不跟上面合併偵測共用，因為合併偵測刻意只認「有訂單編號」的那些，回購
      // 次數要算全部既有訂單，兩邊的篩選條件不一樣。
      const repeatCountByHandle = {};
      (existingOrders || []).forEach((o) => {
        const name = String(o.customer_name || '');
        const m = name.match(/CM\d{10,}/);
        if (m) existingOrderNos.add(m[0]);
        const handle = name.split('/')[0].trim();
        if (handle) {
          const key = handle.toLowerCase();
          repeatCountByHandle[key] = (repeatCountByHandle[key] || 0) + 1;
        }
        if (!handle || !m) return;
        const total = (o.order_items || []).reduce((s, it) => s + (Number(it.sold_price) || 0), 0);
        (existingByHandle[handle] = existingByHandle[handle] || []).push({ orderNo: m[0], total: total });
      });

      const { data: priced, error: pricedErr } = await supabase
        .from('priced_items')
        .select('id, name, sort_group, item_no, color, sizes, spec1_name, spec1_value, spec2_name, spec2_value, actual_cost, final_price, photo_url, stock_group_id, created_at')
        .is('deleted_at', null);
      if (pricedErr) throw pricedErr;
      const priceIndex = {};
      const nameIndex = {};
      (priced || []).forEach((p) => {
        if (p.sort_group && p.item_no != null) {
          const key = String(p.sort_group).toUpperCase() + ':' + Number(p.item_no);
          (priceIndex[key] = priceIndex[key] || []).push(p);
        }
        const name = (p.name || '').trim();
        if (name) (nameIndex[name] = nameIndex[name] || []).push(p);
      });
      // 比對時要先試最長的商品名稱，不然短名稱（例如「娃用奶嘴」）會搶先比對到
      // 其實是另一個更長名稱商品（例如「娃用奶嘴架」）的前綴，比對錯商品。
      const sortedNames = Object.keys(nameIndex).sort((a, b) => b.length - a.length);

      const duplicates = [];
      const candidateNew = [];
      grouped.forEach((o) => {
        if (existingOrderNos.has(o.orderNo)) {
          duplicates.push({ orderNo: o.orderNo, date: o.date, customerMasked: o.customerMasked, itemCount: o.items.length, _sortKey: orderDateTimeKey(o) });
          return;
        }
        o.handle = parseHandle(o.feedback1);
        o.orderTotal = o.items.reduce((s, it) => s + it.unitPrice * it.qty, 0);
        candidateNew.push(o);
      });
      duplicates.sort((a, b) => (a._sortKey < b._sortKey ? -1 : a._sortKey > b._sortKey ? 1 : 0));
      duplicates.forEach((d) => { delete d._sortKey; });

      const mergeSuspectByOrderNo = detectMergeSuspects(candidateNew, existingByHandle);

      const newOrders = candidateNew.map((o) => {
        const items = o.items.map((it) => Object.assign({ rawName: it.rawName, qty: it.qty, unitPrice: it.unitPrice }, matchItem(it, priceIndex, nameIndex, sortedNames)));
        const discountAmount = (o.discC || 0) + (o.discD || 0) + (o.discE || 0);
        return {
          orderNo: o.orderNo,
          date: o.date,
          customerMasked: o.customerMasked,
          feedbackHandle: o.handle,
          note: o.note,
          shippingFee: o.shippingFee || 0,
          discountAmount: discountAmount,
          discountReason: discountAmount !== 0 ? '賣貨便折價券/運費券抵扣' : null,
          orderTotal: o.orderTotal,
          mergeSuspect: mergeSuspectByOrderNo[o.orderNo] || null,
          repeatCount: (o.handle && repeatCountByHandle[o.handle.toLowerCase()]) || 0,
          items: items,
          _sortKey: orderDateTimeKey(o)
        };
      });
      newOrders.sort((a, b) => (a._sortKey < b._sortKey ? -1 : a._sortKey > b._sortKey ? 1 : 0));
      newOrders.forEach((o) => { delete o._sortKey; });

      // 讓前端「新增商品」可以用跟 records.html 一樣的圖片選圖（按分類瀏覽、
      // 選顏色/尺寸、看庫存），所以這裡直接把完整的商品庫原始欄位傳回去，
      // 不要只傳精簡摘要——不然像顏色/尺寸分組、庫存共用池這些邏輯都做不出來。
      const catalog = priced || [];

      res.json({ sheetsRead, duplicates, newOrders, catalog });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
});

module.exports = router;
