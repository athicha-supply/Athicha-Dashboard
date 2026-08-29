const fs = require('fs');
const path = require('path');

const root = __dirname;
const sales = JSON.parse(fs.readFileSync(path.join(root, 'view_exports', 'export_Sales.json'), 'utf8'));
const customers = JSON.parse(fs.readFileSync(path.join(root, 'view_exports', 'export_Customer.json'), 'utf8'));
const clean = value => String(value ?? '').replace(/\u00a0/g, ' ').trim();
const master = new Map(customers.map(c => [clean(c.cuscod), {
  name: clean(c.cusnam) || clean(c.cuscod), province: clean(c.province) || 'ไม่ระบุจังหวัด'
}]));
const firstYear = new Map();
const byYear = new Map();

for (const row of sales) {
  const code = clean(row.cuscod);
  const year = String(row.Year || '').trim();
  const month = Number(row.MonthNum);
  if (!code || !year || !month) continue;
  if (!firstYear.has(code) || year < firstYear.get(code)) firstYear.set(code, year);
  if (!byYear.has(year)) byYear.set(year, new Map());
  const yearMap = byYear.get(year);
  if (!yearMap.has(code)) yearMap.set(code, {sales:0, orders:0, months:new Set(), lastMonth:0});
  const item = yearMap.get(code);
  item.sales += Number(row.netval) || 0;
  item.orders += 1;
  item.months.add(month);
  item.lastMonth = Math.max(item.lastMonth, month);
}

const result = {};
for (const [year, yearMap] of [...byYear.entries()].sort()) {
  const rows = [...yearMap.entries()].map(([code, item]) => {
    const info = master.get(code) || {name:code, province:'ไม่ระบุจังหวัด'};
    return {code, name:info.name, province:info.province, sales:item.sales, orders:item.orders,
      activeMonths:item.months.size, months:[...item.months].sort((a,b)=>a-b), avgOrder:item.orders ? item.sales/item.orders : 0,
      lastMonth:item.lastMonth, firstYear:firstYear.get(code), lifecycle:firstYear.get(code)===year?'new':'returning'};
  }).sort((a,b)=>b.sales-a.sales);

  const positiveTotal = rows.reduce((s,r)=>s+Math.max(0,r.sales),0);
  let running = 0;
  for (const row of rows) {
    running += Math.max(0,row.sales);
    const share = positiveTotal ? running/positiveTotal : 1;
    row.segment = share <= .8 ? 'A' : share <= .95 ? 'B' : 'C';
  }
  const summarize = subset => ({customers:subset.length, sales:subset.reduce((s,r)=>s+r.sales,0), orders:subset.reduce((s,r)=>s+r.orders,0)});
  result[year] = {
    summary:summarize(rows),
    segments:['A','B','C'].map(key=>({key,...summarize(rows.filter(r=>r.segment===key))})),
    lifecycle:['new','returning'].map(key=>({key,...summarize(rows.filter(r=>r.lifecycle===key))})),
    customers:rows
  };
}

fs.writeFileSync(path.join(root, 'customer_analytics.js'), 'window.CUSTOMER_ANALYTICS='+JSON.stringify(result)+';', 'utf8');
console.log(`Generated customer analytics for ${Object.keys(result).length} years.`);
