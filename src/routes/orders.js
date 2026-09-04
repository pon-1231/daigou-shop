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
    sold_price: Number(it.soldPrice) || 0
  }));
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
    .order('sold_at', { ascending: false })
    .order('created_at', { ascending: false });
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

      const orderRow = {
        customer_name: String(body.customerName || '').slice(0, 200),
        order_status: ORDER_STATUSES.includes(body.orderStatus) ? body.orderStatus : ORDER_STATUSES[0],
        ship_by: body.shipBy || null,
        shipping_fee: Number(body.shippingFee) || 0,
        note: String(body.note || '').slice(0, 1000),
        photo_url: photoUrl,
        sold_at: body.soldAt || new Date().toISOString().slice(0, 10)
      };

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
  }
  if (body.shipBy !== undefined) update.ship_by = body.shipBy || null;

  if (Object.keys(update).length === 0) {
    return res.status(400).json({ error: '沒有要更新的欄位' });
  }

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
