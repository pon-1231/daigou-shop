const express = require('express');
const router = express.Router();
const supabase = require('../supabaseClient');

// 低於這個數量就算「庫存不足」，商品沒有自己設門檻時用這個。
const DEFAULT_LOW_STOCK_THRESHOLD = 5;
const REASONS = ['進貨', '訂單', '手動調整'];

/* 目前庫存不存成欄位，而是 stock_movements 的 delta 加總算出來，
   這樣不會有「欄位數字跟進出貨紀錄對不上」的狀況。 */
async function sumStockByItem(itemIds) {
  const totals = {};
  if (!itemIds || itemIds.length === 0) return totals;

  // Supabase 沒有 group by，資料量不大（幾百筆商品）就整批撈回來自己加。
  const { data, error } = await supabase
    .from('stock_movements')
    .select('priced_item_id, delta')
    .in('priced_item_id', itemIds);
  if (error) throw error;

  data.forEach(function (m) {
    totals[m.priced_item_id] = (totals[m.priced_item_id] || 0) + Number(m.delta || 0);
  });
  return totals;
}

function thresholdOf(item) {
  return item.low_stock_threshold == null ? DEFAULT_LOW_STOCK_THRESHOLD : item.low_stock_threshold;
}

router.get('/', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  const { data: items, error } = await supabase
    .from('priced_items')
    .select('id, name, photo_url, sort_group, item_no, spec1_name, spec1_value, spec2_name, spec2_value, color, sizes, final_price, track_stock, low_stock_threshold')
    .is('deleted_at', null);
  if (error) return res.status(500).json({ error: error.message });

  try {
    const tracked = items.filter(function (it) { return it.track_stock; });
    const totals = await sumStockByItem(tracked.map(function (it) { return it.id; }));

    const rows = items.map(function (it) {
      const stock = it.track_stock ? (totals[it.id] || 0) : null;
      const threshold = thresholdOf(it);
      return Object.assign({}, it, {
        stock: stock,
        threshold: threshold,
        low_stock: it.track_stock && stock <= threshold
      });
    });

    res.json({ defaultThreshold: DEFAULT_LOW_STOCK_THRESHOLD, items: rows });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/:itemId/movements', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('stock_movements')
    .select('*, orders(customer_name, sold_at, deleted_at)')
    .eq('priced_item_id', req.params.itemId)
    .order('created_at', { ascending: false });
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.post('/:itemId/movements', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  const body = req.body || {};
  const delta = Math.round(Number(body.delta));
  if (!Number.isFinite(delta) || delta === 0) {
    return res.status(400).json({ error: '數量要是不等於 0 的整數' });
  }
  const reason = REASONS.indexOf(body.reason) !== -1 ? body.reason : '手動調整';
  if (reason === '訂單') {
    return res.status(400).json({ error: '訂單的庫存異動由系統自動記錄，不能手動新增' });
  }

  const { data: item, error: itemErr } = await supabase
    .from('priced_items')
    .select('id, track_stock')
    .eq('id', req.params.itemId)
    .is('deleted_at', null)
    .maybeSingle();
  if (itemErr) return res.status(500).json({ error: itemErr.message });
  if (!item) return res.status(404).json({ error: '找不到這個商品' });

  // 進貨/調整本身就代表要開始管這個商品的庫存，順手把追蹤打開，少一個步驟。
  if (!item.track_stock) {
    const { error: trackErr } = await supabase
      .from('priced_items')
      .update({ track_stock: true })
      .eq('id', item.id);
    if (trackErr) return res.status(500).json({ error: trackErr.message });
  }

  const { data, error } = await supabase
    .from('stock_movements')
    .insert({
      priced_item_id: item.id,
      delta: delta,
      reason: reason,
      note: String(body.note || '').slice(0, 500)
    })
    .select()
    .single();
  if (error) return res.status(500).json({ error: error.message });
  res.status(201).json(data);
});

router.delete('/movements/:movementId', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  const { data: mv, error: findErr } = await supabase
    .from('stock_movements')
    .select('id, reason')
    .eq('id', req.params.movementId)
    .maybeSingle();
  if (findErr) return res.status(500).json({ error: findErr.message });
  if (!mv) return res.status(404).json({ error: '找不到這筆紀錄' });
  if (mv.reason === '訂單') {
    return res.status(400).json({ error: '訂單自動產生的紀錄不能單獨刪除，請改訂單本身' });
  }

  const { error } = await supabase.from('stock_movements').delete().eq('id', mv.id);
  if (error) return res.status(500).json({ error: error.message });
  res.status(204).end();
});

router.patch('/:itemId/settings', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  const body = req.body || {};
  const update = {};

  if (body.trackStock !== undefined) update.track_stock = !!body.trackStock;

  if (body.lowStockThreshold !== undefined) {
    const raw = body.lowStockThreshold;
    if (raw === '' || raw == null) {
      update.low_stock_threshold = null;
    } else {
      const n = Math.round(Number(raw));
      if (!Number.isFinite(n) || n < 0) return res.status(400).json({ error: '門檻要是 0 以上的整數' });
      update.low_stock_threshold = n;
    }
  }

  if (Object.keys(update).length === 0) {
    return res.status(400).json({ error: '沒有要更新的欄位' });
  }

  const { data, error } = await supabase
    .from('priced_items')
    .update(update)
    .eq('id', req.params.itemId)
    .select()
    .single();
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

module.exports = router;
module.exports.DEFAULT_LOW_STOCK_THRESHOLD = DEFAULT_LOW_STOCK_THRESHOLD;
