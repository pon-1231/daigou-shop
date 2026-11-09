const express = require('express');
const router = express.Router();
const supabase = require('../supabaseClient');

// 低於這個數量就算「庫存不足」，商品沒有自己設門檻時用這個。
const DEFAULT_LOW_STOCK_THRESHOLD = 5;
const REASONS = ['進貨', '訂單', '手動調整'];

/* 目前庫存不存成欄位，而是 stock_movements 的 delta 加總算出來，
   這樣不會有「欄位數字跟進出貨紀錄對不上」的狀況。
   有些商品是同一批貨拆成不同包裝賣（例如「50包/袋」跟「1包」），這種會共用
   stock_group_id，庫存要兩邊的異動紀錄一起加總、顯示同一個數字——不是各自獨立算。
   trackedItems 只需要用來決定「要不要顯示」，實際加總要連沒開追蹤、但剛好跟
   已追蹤商品同一組的其他規格也一起撈進來，不然共用池會漏算。 */
async function sumStockByItem(trackedItems, allItemsById) {
  const totals = {};
  if (!trackedItems || trackedItems.length === 0) return totals;

  function groupKeyOf(it) { return it.stock_group_id || it.id; }

  const groupToMemberIds = {};
  trackedItems.forEach(function (it) {
    const key = groupKeyOf(it);
    if (!groupToMemberIds[key]) groupToMemberIds[key] = new Set();
    groupToMemberIds[key].add(it.id);
    if (it.stock_group_id) {
      // 同一組的其他規格（不管自己有沒有開追蹤）也要一起算，不然共用池會漏掉它們的異動。
      Object.values(allItemsById).forEach(function (sibling) {
        if (sibling.stock_group_id === it.stock_group_id) groupToMemberIds[key].add(sibling.id);
      });
    }
  });

  const allMemberIds = Array.from(new Set(Object.values(groupToMemberIds).flatMap(function (s) { return Array.from(s); })));

  const { data, error } = await supabase
    .from('stock_movements')
    .select('priced_item_id, delta')
    .in('priced_item_id', allMemberIds);
  if (error) throw error;

  const sumByItemId = {};
  data.forEach(function (m) {
    sumByItemId[m.priced_item_id] = (sumByItemId[m.priced_item_id] || 0) + Number(m.delta || 0);
  });

  Object.keys(groupToMemberIds).forEach(function (key) {
    const memberIds = Array.from(groupToMemberIds[key]);
    const groupTotal = memberIds.reduce(function (s, id) { return s + (sumByItemId[id] || 0); }, 0);
    totals[key] = groupTotal; // key 是 trackedItems 裡任一成員的 id 或共用的 stock_group_id
  });

  // trackedItems 裡每個品項各自對應到自己那組的總數，方便外面直接用 totals[item.id] 查。
  const result = {};
  trackedItems.forEach(function (it) {
    result[it.id] = totals[groupKeyOf(it)] || 0;
  });
  return result;
}

function thresholdOf(item) {
  return item.low_stock_threshold == null ? DEFAULT_LOW_STOCK_THRESHOLD : item.low_stock_threshold;
}

router.get('/', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  const { data: items, error } = await supabase
    .from('priced_items')
    .select('id, name, photo_url, sort_group, item_no, spec1_name, spec1_value, spec2_name, spec2_value, color, sizes, final_price, track_stock, low_stock_threshold, stock_group_id, stock_unit_size')
    .is('deleted_at', null);
  if (error) return res.status(500).json({ error: error.message });

  try {
    const allItemsById = {};
    items.forEach(function (it) { allItemsById[it.id] = it; });
    const tracked = items.filter(function (it) { return it.track_stock; });
    const totals = await sumStockByItem(tracked, allItemsById);

    const rows = items.map(function (it) {
      const stock = it.track_stock ? (totals[it.id] || 0) : null;
      const threshold = thresholdOf(it);
      const groupMates = it.stock_group_id
        ? items.filter(function (sib) { return sib.stock_group_id === it.stock_group_id && sib.id !== it.id; }).map(function (sib) { return sib.name; })
        : [];
      return Object.assign({}, it, {
        stock: stock,
        threshold: threshold,
        low_stock: it.track_stock && stock <= threshold,
        stock_group_mates: groupMates
      });
    });

    res.json({ defaultThreshold: DEFAULT_LOW_STOCK_THRESHOLD, items: rows });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/:itemId/movements', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  // 共用庫存池的話，紀錄要連同一組的其他規格一起列出來，不然看不懂「怎麼突然少這麼多」。
  const { data: item, error: itemErr } = await supabase
    .from('priced_items')
    .select('id, name, stock_group_id')
    .eq('id', req.params.itemId)
    .maybeSingle();
  if (itemErr) return res.status(500).json({ error: itemErr.message });

  // 同一組的規格常常名稱完全一樣（只有顏色/尺寸不同），單用名稱認不出是哪個，
  // 要拼規格文字才看得出差異。
  function specLabelOf(it) {
    const parts = [];
    if (it.spec1_value) parts.push((it.spec1_name ? it.spec1_name + '：' : '') + it.spec1_value);
    else if (it.color) parts.push(it.color);
    if (it.spec2_value) parts.push((it.spec2_name ? it.spec2_name + '：' : '') + it.spec2_value);
    else if (it.sizes) parts.push(it.sizes);
    return it.name + (parts.length ? '（' + parts.join('　') + '）' : '');
  }

  let memberIds = [req.params.itemId];
  let namesById = {};
  if (item && item.stock_group_id) {
    const { data: siblings, error: sibErr } = await supabase
      .from('priced_items')
      .select('id, name, color, sizes, spec1_name, spec1_value, spec2_name, spec2_value')
      .eq('stock_group_id', item.stock_group_id);
    if (sibErr) return res.status(500).json({ error: sibErr.message });
    memberIds = siblings.map(function (s) { return s.id; });
    siblings.forEach(function (s) { namesById[s.id] = specLabelOf(s); });
  }

  const { data, error } = await supabase
    .from('stock_movements')
    .select('*, orders(customer_name, sold_at, deleted_at)')
    .in('priced_item_id', memberIds)
    .order('created_at', { ascending: false });
  if (error) return res.status(500).json({ error: error.message });

  const rows = data.map(function (m) {
    return Object.assign({}, m, {
      from_other_variant: m.priced_item_id !== req.params.itemId ? (namesById[m.priced_item_id] || null) : null
    });
  });
  res.json(rows);
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

  // 共用庫存：stockGroupId 給 null/'' 代表取消共用；stockUnitSize 是「這個規格的 1 個＝幾個基本單位」。
  if (body.stockGroupId !== undefined) {
    update.stock_group_id = body.stockGroupId || null;
  }
  if (body.stockUnitSize !== undefined) {
    const n = Math.round(Number(body.stockUnitSize));
    if (!Number.isFinite(n) || n < 1) return res.status(400).json({ error: '換算單位要是 1 以上的整數' });
    update.stock_unit_size = n;
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
