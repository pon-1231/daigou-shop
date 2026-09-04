const express = require('express');
const multer = require('multer');
const router = express.Router();
const supabase = require('../supabaseClient');

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

router.get('/', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('sales_records')
    .select('*')
    .order('sold_at', { ascending: false })
    .order('created_at', { ascending: false });
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.post('/', (req, res) => {
  upload.single('photo')(req, res, async (uploadErr) => {
    if (uploadErr) return res.status(400).json({ error: uploadErr.message });
    if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

    try {
      const body = req.body || {};
      let photoUrl = null;

      if (req.file) {
        const extMatch = (req.file.originalname || '').match(/\.([a-zA-Z0-9]+)$/);
        const ext = extMatch ? extMatch[1].toLowerCase() : 'jpg';
        const fileName = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`;
        const { error: uploadError } = await supabase.storage
          .from('sales-photos')
          .upload(fileName, req.file.buffer, { contentType: req.file.mimetype });
        if (uploadError) throw uploadError;
        const { data: pub } = supabase.storage.from('sales-photos').getPublicUrl(fileName);
        photoUrl = pub.publicUrl;
      }

      const cost = Number(body.cost) || 0;
      const soldPrice = Number(body.soldPrice) || 0;
      const shippingFee = Number(body.shippingFee) || 0;

      const row = {
        customer_name: String(body.customerName || '').slice(0, 200),
        item_name: String(body.itemName || '').slice(0, 200),
        photo_url: photoUrl,
        cost,
        sold_price: soldPrice,
        shipping_fee: shippingFee,
        profit: soldPrice - cost,
        note: String(body.note || '').slice(0, 1000),
        sold_at: body.soldAt || new Date().toISOString().slice(0, 10)
      };

      const { data, error } = await supabase.from('sales_records').insert(row).select().single();
      if (error) throw error;
      res.status(201).json(data);
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
});

router.delete('/:id', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  const { data: existing } = await supabase
    .from('sales_records')
    .select('photo_url')
    .eq('id', req.params.id)
    .single();

  const { error } = await supabase.from('sales_records').delete().eq('id', req.params.id);
  if (error) return res.status(500).json({ error: error.message });

  if (existing && existing.photo_url) {
    const fileName = existing.photo_url.split('/').pop();
    supabase.storage.from('sales-photos').remove([fileName]).catch(() => {});
  }
  res.status(204).end();
});

module.exports = router;
