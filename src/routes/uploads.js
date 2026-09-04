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

router.post('/photo', (req, res) => {
  upload.single('photo')(req, res, async (uploadErr) => {
    if (uploadErr) return res.status(400).json({ error: uploadErr.message });
    if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
    if (!req.file) return res.status(400).json({ error: '沒有收到檔案' });

    try {
      const extMatch = (req.file.originalname || '').match(/\.([a-zA-Z0-9]+)$/);
      const ext = extMatch ? extMatch[1].toLowerCase() : 'jpg';
      const fileName = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`;
      const { error: uploadError } = await supabase.storage
        .from('sales-photos')
        .upload(fileName, req.file.buffer, { contentType: req.file.mimetype });
      if (uploadError) throw uploadError;
      const { data: pub } = supabase.storage.from('sales-photos').getPublicUrl(fileName);
      res.status(201).json({ url: pub.publicUrl });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
});

module.exports = router;
