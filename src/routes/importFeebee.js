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

function matchItem(rawItem, priceIndex) {
  const prefix = itemNoPrefix(rawItem.rawName);
  if (!prefix) {
    return { status: 'needs_review', reason: 'no_prefix', matched: null, suggestedId: null, candidates: [] };
  }
  const candidates = priceIndex[prefix.group + ':' + prefix.itemNo] || [];
  if (candidates.length === 0) {
    return { status: 'needs_review', reason: 'unknown_item_no', matched: null, suggestedId: null, candidates: [] };
  }

  const candidateView = candidates.map((c) => ({
    id: c.id,
    name: c.name,
    color: c.color || c.spec1_value || '',
    cost: Number(c.actual_cost) || 0,
    price: Number(c.final_price) || 0
  }));

  const colorMatches = candidateView.filter((c) => c.color && prefix.remainder.includes(c.color));
  const strongMatches = colorMatches.filter((c) => Math.abs(c.price - rawItem.unitPrice) < 0.01);

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
        .select('id, name, sort_group, item_no, color, spec1_value, actual_cost, final_price')
        .is('deleted_at', null);
      if (pricedErr) throw pricedErr;
      const priceIndex = {};
      (priced || []).forEach((p) => {
        if (!p.sort_group || p.item_no == null) return;
        const key = String(p.sort_group).toUpperCase() + ':' + Number(p.item_no);
        (priceIndex[key] = priceIndex[key] || []).push(p);
      });

      const duplicates = [];
      const newOrders = [];
      grouped.forEach((o) => {
        if (existingOrderNos.has(o.orderNo)) {
          duplicates.push({ orderNo: o.orderNo, date: o.date, customerMasked: o.customerMasked, itemCount: o.items.length });
          return;
        }
        const items = o.items.map((it) => Object.assign({ rawName: it.rawName, qty: it.qty, unitPrice: it.unitPrice }, matchItem(it, priceIndex)));
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
          items: items
        });
      });

      res.json({ sheetsRead, duplicates, newOrders });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
});

module.exports = router;
