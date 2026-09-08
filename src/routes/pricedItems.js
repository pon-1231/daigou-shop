const express = require('express');
const router = express.Router();
const supabase = require('../supabaseClient');

router.get('/', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { data, error } = await supabase
    .from('priced_items')
    .select('*')
    .order('created_at', { ascending: false });
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

function buildRow(body) {
  return {
    name: String(body.name || '').slice(0, 200),
    category_key: String(body.categoryKey || ''),
    category_label: String(body.categoryLabel || '未分類'),
    rmb_price: Number(body.rmbPrice) || 0,
    rmb_ship: Number(body.rmbShip) || 0,
    fx_rate: Number(body.fxRate) || 0,
    cross_ship: Number(body.crossShip) || 0,
    packaging: Number(body.packaging) || 0,
    payment_fee: Number(body.paymentFee) || 0,
    domestic_ship: Number(body.domesticShip) || 0,
    multiplier: Number(body.multiplier) || 0,
    manual_price: body.manualPrice === '' || body.manualPrice == null ? null : Number(body.manualPrice),
    china_cost: Number(body.chinaCost) || 0,
    actual_cost: Number(body.actualCost) || 0,
    final_price: Number(body.finalPrice) || 0,
    profit: Number(body.profit) || 0,
    margin: Number(body.margin) || 0,
    photo_url: body.photoUrl || null,
    sizes: body.sizes ? String(body.sizes).slice(0, 200) : null,
    sort_group: body.sortGroup ? String(body.sortGroup).slice(0, 50) : null
  };
}

router.post('/', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const row = buildRow(req.body || {});

  let maxQuery = supabase
    .from('priced_items')
    .select('item_no')
    .order('item_no', { ascending: false, nullsFirst: false })
    .limit(1);
  maxQuery = row.sort_group ? maxQuery.eq('sort_group', row.sort_group) : maxQuery.is('sort_group', null);
  const { data: maxRow } = await maxQuery.maybeSingle();
  row.item_no = (maxRow && maxRow.item_no ? maxRow.item_no : 0) + 1;

  const { data, error } = await supabase.from('priced_items').insert(row).select().single();
  if (error) return res.status(500).json({ error: error.message });
  res.status(201).json(data);
});

router.put('/:id', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const row = buildRow(req.body || {});
  const { data, error } = await supabase
    .from('priced_items')
    .update(row)
    .eq('id', req.params.id)
    .select()
    .single();
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.patch('/:id/item-no', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const raw = req.body ? req.body.itemNo : undefined;
  const parsed = raw === '' || raw == null ? null : Number(raw);
  if (parsed !== null && !Number.isFinite(parsed)) {
    return res.status(400).json({ error: '編號要是數字' });
  }
  const itemNo = parsed === null ? null : Math.round(parsed);
  const { data, error } = await supabase
    .from('priced_items')
    .update({ item_no: itemNo })
    .eq('id', req.params.id)
    .select()
    .single();
  if (error) return res.status(500).json({ error: error.message });
  res.json(data);
});

router.delete('/:id', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });
  const { error } = await supabase.from('priced_items').delete().eq('id', req.params.id);
  if (error) return res.status(500).json({ error: error.message });
  res.status(204).end();
});

module.exports = router;
