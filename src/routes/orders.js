const express = require('express');
const multer = require('multer');
const router = express.Router();
const supabase = require('../supabaseClient');

const ORDER_STATUSES = ['待處理', '已下貨', '已出貨', '已完成'];

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 8 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    if (!/^image\/(png|jpe?g|webp|gif)$/.test(file.mimetype)) {
      return cb(new Error('只能上傳圖片檔（png / jpg / webp / gif）'));
    }
    cb(null, true);
  }
});

function parseItems(raw) {
  let items;
  try {
    items = JSON.parse(raw || '[]');
  } catch (e) {
    return null;
  }
  if (!Array.isArray(items) || items.length === 0) return null;
  return items.map((it) => ({
    item_name: String(it.itemName || '').slice(0, 200),
    size: it.size ? String(it.size).slice(0, 50) : null,
    photo_url: it.photoUrl || null,
    cost: Number(it.cost) || 0,
    sold_price: Number(it.soldPrice) || 0,
    // 從商品圖庫選的才有，手動輸入的是 null，null 就不扣庫存
    priced_item_id: it.productId || null
  }));
}

/* ---------- 庫存連動 ----------
   策略跟 order_items 一樣是「整批重算」：訂單有任何變動，就先把這張訂單產生的
   庫存紀錄全部刪掉，再依訂單目前的內容重新寫一次。這樣改數量、刪商品、刪訂單、
   從垃圾桶還原全部自動正確，不用寫任何比對前後差異的邏輯。 */

async function clearOrderStockMovements(orderId) {
  const { error } = await supabase.from('stock_movements').delete().eq('order_id', orderId);
  if (error) throw error;
}

async function applyOrderStockMovements(orderId, items) {
  // 同一個商品買好幾件時，order_items 是一件一行，但庫存紀錄合併成一筆 -N 比較好讀。
  const qtyByItem = {};
  items.forEach((it) => {
    if (!it.priced_item_id) return;
    qtyByItem[it.priced_item_id] = (qtyByItem[it.priced_item_id] || 0) + 1;
  });

  const itemIds = Object.keys(qtyByItem);
  if (itemIds.length === 0) return;

  // 只扣有開啟追蹤的商品，沒在管庫存的不要莫名其妙生出負數。
  const { data: tracked, error: trackErr } = await supabase
    .from('priced_items')
    .select('id')
    .in('id', itemIds)
    .eq('track_stock', true);
  if (trackErr) throw trackErr;
  if (!tracked || tracked.length === 0) return;

  const rows = tracked.map((t) => ({
    priced_item_id: t.id,
    delta: -qtyByItem[t.id],
    reason: '訂單',
    order_id: orderId
  }));
  const { error } = await supabase.from('stock_movements').insert(rows);
  if (error) throw error;
}

async function syncOrderStock(orderId, items) {
  await clearOrderStockMovements(orderId);
  if (items && items.length) await applyOrderStockMovements(orderId, items);
}

async function recordHistory(orderId) {
  const { data: snapshot } = await supabase
    .from('orders')
    .select('*, order_items(*)')
    .eq('id', orderId)
    .single();
  if (!snapshot) return;
  await supabase.from('order_history').insert({ order_id: orderId, snapshot });
}

async function uploadOrderPhoto(file) {
  const extMatch = (file.originalname || '').match(/\.([a-zA-Z0-9]+)$/);
  const ext = extMatch ? extMatch[1].toLowerCase() : 'jpg';
  const fileName = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`;
  const { error: uploadError } = await supabase.storage
    .from('sales-photos')
    .upload(fileName, file.buffer, { contentType: file.mimetype });
  if (uploadError) throw uploadError;
  const { data: pub } = supabase.storage.from('sales-photos').getPublicUrl(fileName);
  return pub.publicUrl;
}

router.get('/', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('orders')
    .select('*, order_items(*)')
    .is('deleted_at', null)
    .order('sold_at', { ascending: false })
    .order('created_at', { ascending: false });
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.get('/trash', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('orders')
    .select('*, order_items(*)')
    .not('deleted_at', 'is', null)
    .order('deleted_at', { ascending: false });
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.get('/:id/history', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('order_history')
    .select('*')
    .eq('order_id', req.params.id)
    .order('edited_at', { ascending: false });
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.post('/', (req, res) => {
  upload.single('photo')(req, res, async (uploadErr) => {
    if (uploadErr) return res.status(400).json({ error: uploadErr.message });
    if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

    const body = req.body || {};
    const items = parseItems(body.items);
    if (!items) return res.status(400).json({ error: '訂單至少要有一樣商品' });

    try {
      let photoUrl = null;
      if (req.file) photoUrl = await uploadOrderPhoto(req.file);

      const orderRow = {
        customer_name: String(body.customerName || '').slice(0, 200),
        order_status: ORDER_STATUSES.includes(body.orderStatus) ? body.orderStatus : ORDER_STATUSES[0],
        status_updated_at: new Date().toISOString(),
        ship_by: body.shipBy || null,
        shipping_fee: Number(body.shippingFee) || 0,
        note: String(body.note || '').slice(0, 1000),
        photo_url: photoUrl,
        sold_at: body.soldAt || new Date().toISOString().slice(0, 10)
      };

      const { data: order, error: orderErr } = await supabase.from('orders').insert(orderRow).select().single();
      if (orderErr) throw orderErr;

      const itemRows = items.map((it) => Object.assign({ order_id: order.id }, it));
      const { data: savedItems, error: itemsErr } = await supabase.from('order_items').insert(itemRows).select();
      if (itemsErr) throw itemsErr;

      await syncOrderStock(order.id, savedItems);

      order.order_items = savedItems;
      res.status(201).json(order);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
});

router.put('/:id', (req, res) => {
  upload.single('photo')(req, res, async (uploadErr) => {
    if (uploadErr) return res.status(400).json({ error: uploadErr.message });
    if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

    const body = req.body || {};
    const items = parseItems(body.items);
    if (!items) return res.status(400).json({ error: '訂單至少要有一樣商品' });

    try {
      let photoUrl = body.keepPhotoUrl || null;
      if (req.file) photoUrl = await uploadOrderPhoto(req.file);

      const newStatus = ORDER_STATUSES.includes(body.orderStatus) ? body.orderStatus : ORDER_STATUSES[0];
      const { data: existingOrder } = await supabase
        .from('orders')
        .select('order_status')
        .eq('id', req.params.id)
        .single();

      await recordHistory(req.params.id);

      const orderRow = {
        customer_name: String(body.customerName || '').slice(0, 200),
        order_status: newStatus,
        ship_by: body.shipBy || null,
        shipping_fee: Number(body.shippingFee) || 0,
        note: String(body.note || '').slice(0, 1000),
        photo_url: photoUrl,
        sold_at: body.soldAt || new Date().toISOString().slice(0, 10)
      };
      if (!existingOrder || existingOrder.order_status !== newStatus) {
        orderRow.status_updated_at = new Date().toISOString();
      }

      const { data: order, error: orderErr } = await supabase
        .from('orders')
        .update(orderRow)
        .eq('id', req.params.id)
        .select()
        .single();
      if (orderErr) throw orderErr;

      const { error: delErr } = await supabase.from('order_items').delete().eq('order_id', req.params.id);
      if (delErr) throw delErr;

      const itemRows = items.map((it) => Object.assign({ order_id: req.params.id }, it));
      const { data: savedItems, error: itemsErr } = await supabase.from('order_items').insert(itemRows).select();
      if (itemsErr) throw itemsErr;

      await syncOrderStock(req.params.id, savedItems);

      order.order_items = savedItems;
      res.json(order);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
});

router.patch('/:id', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const body = req.body || {};
  const update = {};

  if (body.orderStatus !== undefined) {
    if (!ORDER_STATUSES.includes(body.orderStatus)) {
      return res.status(400).json({ error: '訂單狀態不合法' });
    }
    update.order_status = body.orderStatus;
    update.status_updated_at = new Date().toISOString();
  }
  if (body.shipBy !== undefined) update.ship_by = body.shipBy || null;

  if (Object.keys(update).length === 0) {
    return res.status(400).json({ error: '沒有要更新的欄位' });
  }

  await recordHistory(req.params.id);

  const { data, error } = await supabase
    .from('orders')
    .update(update)
    .eq('id', req.params.id)
    .select('*, order_items(*)')
    .single();
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.delete('/:id', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('orders')
    .update({ deleted_at: new Date().toISOString() })
    .eq('id', req.params.id)
    .select('*, order_items(*)')
    .single();
  if (error) return res.status(500).json({ error: error.message });

  // 丟到垃圾桶等於這張訂單不算數了，庫存要還回去。
  try {
    await clearOrderStockMovements(req.params.id);
  } catch (err) {
    return res.status(500).json({ error: '訂單已刪除，但庫存回補失敗：' + err.message });
  }
  res.json(data);
});

router.post('/:id/restore', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('orders')
    .update({ deleted_at: null })
    .eq('id', req.params.id)
    .select('*, order_items(*)')
    .single();
  if (error) return res.status(500).json({ error: error.message });

  // 還原回來就要重新扣掉（軟刪除時 order_items 沒有被刪，直接照它重算）。
  try {
    await syncOrderStock(req.params.id, data.order_items || []);
  } catch (err) {
    return res.status(500).json({ error: '訂單已還原，但庫存重新扣帳失敗：' + err.message });
  }
  res.json(data);
});

router.delete('/:id/permanent', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  const { data: existing } = await supabase
    .from('orders')
    .select('photo_url')
    .eq('id', req.params.id)
    .single();

  const { error } = await supabase.from('orders').delete().eq('id', req.params.id);
  if (error) return res.status(500).json({ error: error.message });

  if (existing && existing.photo_url) {
    const fileName = existing.photo_url.split('/').pop();
    supabase.storage.from('sales-photos').remove([fileName]).catch(() => {});
  }
  res.status(204).end();
});

module.exports = router;
