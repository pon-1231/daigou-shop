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
  const m = String(rawName || '').match(/^([A-Za-z]{1,3})(\d{1,3})[.．]\s*(.+)$/);
  if (!m) return null;
  return { group: m[1].toUpperCase(), itemNo: Number(m[2]), remainder: m[3].trim() };
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function buildCandidateMatch(candidates, remainder, unitPrice) {
  const candidateView = candidates.map((c) => ({
    id: c.id,
    name: c.name,
    color: c.color || c.spec1_value || '',
    cost: Number(c.actual_cost) || 0,
    price: Number(c.final_price) || 0,
    photoUrl: c.photo_url || null
  }));

  const colorMatches = candidateView.filter((c) => c.color && remainder.includes(c.color));
  const strongMatches = colorMatches.filter((c) => Math.abs(c.price - unitPrice) < 0.01);

  if (strongMatches.length === 1) {
    return { status: 'matched', reason: null, matched: strongMatches[0], suggestedId: strongMatches[0].id, candidates: candidateView };
  }
  if (colorMatches.length === 1) {
    return { status: 'needs_review', reason: 'price_mismatch', matched: null, suggestedId: colorMatches[0].id, candidates: candidateView };
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
  const candidates = priceIndex[prefix.group + ':' + prefix.itemNo] || [];
  if (candidates.length === 0) {
    return { status: 'needs_review', reason: 'unknown_item_no', matched: null, suggestedId: null, candidates: [] };
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
        .select('customer_name')
        .is('deleted_at', null);
      if (ordersErr) throw ordersErr;
      const existingOrderNos = new Set();
      (existingOrders || []).forEach((o) => {
        const m = String(o.customer_name || '').match(/CM\d{10,}/);
        if (m) existingOrderNos.add(m[0]);
      });

      const { data: priced, error: pricedErr } = await supabase
        .from('priced_items')
        .select('id, name, sort_group, item_no, color, spec1_value, actual_cost, final_price, photo_url')
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
      const newOrders = [];
      grouped.forEach((o) => {
        const sortKey = orderDateTimeKey(o);
        if (existingOrderNos.has(o.orderNo)) {
          duplicates.push({ orderNo: o.orderNo, date: o.date, customerMasked: o.customerMasked, itemCount: o.items.length, _sortKey: sortKey });
          return;
        }
        const items = o.items.map((it) => Object.assign({ rawName: it.rawName, qty: it.qty, unitPrice: it.unitPrice }, matchItem(it, priceIndex, nameIndex, sortedNames)));
        const discountAmount = (o.discC || 0) + (o.discD || 0) + (o.discE || 0);
        newOrders.push({
          orderNo: o.orderNo,
          date: o.date,
          customerMasked: o.customerMasked,
          feedbackHandle: parseHandle(o.feedback1),
          note: o.note,
          shippingFee: o.shippingFee || 0,
          discountAmount: discountAmount,
          discountReason: discountAmount !== 0 ? '賣貨便折價券/運費券抵扣' : null,
          orderTotal: o.items.reduce((s, it) => s + it.unitPrice * it.qty, 0),
          items: items,
          _sortKey: sortKey
        });
      });

      // 賣貨便匯出通常是新的在前面，審核時比較習慣由舊到新一筆一筆往下處理。
      // 同一天可能有好幾筆訂單，只比日期不夠準，要連「狀態」欄位裡藏的下單時間一起比。
      function byDateAsc(a, b) { return a._sortKey < b._sortKey ? -1 : a._sortKey > b._sortKey ? 1 : 0; }
      duplicates.sort(byDateAsc);
      newOrders.sort(byDateAsc);
      duplicates.forEach((d) => { delete d._sortKey; });
      newOrders.forEach((o) => { delete o._sortKey; });

      res.json({ sheetsRead, duplicates, newOrders });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
});

module.exports = router;
