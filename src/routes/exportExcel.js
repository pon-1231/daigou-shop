const express = require('express');
const ExcelJS = require('exceljs');
const router = express.Router();
const supabase = require('../supabaseClient');

router.get('/excel', async (req, res) => {
  if (!supabase) return res.status(500).json({ error: 'Supabase 尚未設定' });

  try {
    const [pricedResult, salesResult] = await Promise.all([
      supabase.from('priced_items').select('*').order('created_at', { ascending: false }),
      supabase.from('sales_records').select('*').order('sold_at', { ascending: false })
    ]);
    if (pricedResult.error) throw pricedResult.error;
    if (salesResult.error) throw salesResult.error;

    const priced = pricedResult.data || [];
    const sales = salesResult.data || [];

    const wb = new ExcelJS.Workbook();
    wb.creator = '代購賣場工具';
    wb.created = new Date();

    const sheet1 = wb.addWorksheet('定價紀錄');
    sheet1.columns = [
      { header: '商品名稱', key: 'name', width: 22 },
      { header: '類型', key: 'category_label', width: 10 },
      { header: '商品人民幣', key: 'rmb_price', width: 12 },
      { header: '中國境內運費(¥)', key: 'rmb_ship', width: 16 },
      { header: '匯率', key: 'fx_rate', width: 8 },
      { header: '跨境運費', key: 'cross_ship', width: 10 },
      { header: '包材', key: 'packaging', width: 8 },
      { header: '金流/平台費', key: 'payment_fee', width: 12 },
      { header: '給客人運費', key: 'domestic_ship', width: 12 },
      { header: '倍數', key: 'multiplier', width: 8 },
      { header: '中國成本', key: 'china_cost', width: 10 },
      { header: '實際成本', key: 'actual_cost', width: 10 },
      { header: '建議售價', key: 'final_price', width: 10 },
      { header: '淨利', key: 'profit', width: 10 },
      { header: '淨利率%', key: 'margin', width: 10 },
      { header: '建立時間', key: 'created_at', width: 20 }
    ];
    priced.forEach((r) => sheet1.addRow(r));
    sheet1.getRow(1).font = { bold: true };

    const sheet2 = wb.addWorksheet('銷售紀錄');
    sheet2.columns = [
      { header: '日期', key: 'sold_at', width: 12 },
      { header: '客人', key: 'customer_name', width: 16 },
      { header: '商品', key: 'item_name', width: 22 },
      { header: '訂單狀態', key: 'order_status', width: 12 },
      { header: '成本', key: 'cost', width: 10 },
      { header: '售價', key: 'sold_price', width: 10 },
      { header: '給客人運費', key: 'shipping_fee', width: 12 },
      { header: '淨利', key: 'profit', width: 10 },
      { header: '備註', key: 'note', width: 24 },
      { header: '照片連結', key: 'photo_url', width: 40 },
      { header: '建立時間', key: 'created_at', width: 20 }
    ];
    sales.forEach((r) => sheet2.addRow(r));
    sheet2.getRow(1).font = { bold: true };

    if (sales.length > 0) {
      const totalProfit = sales.reduce((sum, r) => sum + (Number(r.profit) || 0), 0);
      sheet2.addRow({});
      const totalRow = sheet2.addRow({ item_name: '總計淨利', profit: totalProfit });
      totalRow.font = { bold: true };
    }

    res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    res.setHeader('Content-Disposition', `attachment; filename="daigou-backup-${new Date().toISOString().slice(0, 10)}.xlsx"`);
    await wb.xlsx.write(res);
    res.end();
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
