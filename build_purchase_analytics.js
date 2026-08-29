const fs = require('fs');
const path = require('path');
const root = __dirname;
const purchases = JSON.parse(fs.readFileSync(path.join(root,'view_exports','export_Purchase.json'),'utf8'));
const details = JSON.parse(fs.readFileSync(path.join(root,'view_exports','export_PurchaseDetails.json'),'utf8'));
const clean = value => String(value ?? '').replace(/\u00a0/g,' ').trim();
const result = {};

for (const row of purchases) {
  const year=clean(row.Year), month=Number(row.MonthNum), supplier=clean(row.supcod)||'ไม่ระบุ';
  if(!year || !month) continue;
  if(!result[year]) result[year]={documents:[],products:{}};
  result[year].documents.push({supplier,month,value:Number(row.netval)||0,type:clean(row.Type)||'ไม่ระบุ',payterm:Number(row.paytrm)||0,vat:Number(row.vatamt)||0});
}
for (const row of details) {
  const year=clean(row.Year), product=clean(row.stkdes)||clean(row.stkcod)||'ไม่ระบุ';
  if(!result[year]) result[year]={documents:[],products:{}};
  const p=result[year].products[product] ||= {value:0,qty:0,documents:new Set()};
  p.value += Number(row.netval)||0; p.qty += Number(row.trnqty)||0; p.documents.add(clean(row.docnum));
}

for (const [year,data] of Object.entries(result)) {
  const supplierMap=new Map(), monthMap=new Map(), typeMap=new Map(), termMap=new Map();
  const termKey=t=>t<=0?'เงินสด/ครบกำหนดทันที':t<=30?'เครดิต 1–30 วัน':t<=60?'เครดิต 31–60 วัน':'เครดิตมากกว่า 60 วัน';
  for(const d of data.documents){
    const s=supplierMap.get(d.supplier)||{supplier:d.supplier,value:0,documents:0,months:new Set(),termTotal:0,lastMonth:0};
    s.value+=d.value;s.documents++;s.months.add(d.month);s.termTotal+=d.payterm;s.lastMonth=Math.max(s.lastMonth,d.month);supplierMap.set(d.supplier,s);
    const m=monthMap.get(d.month)||{month:d.month,value:0,documents:0,suppliers:new Set()};m.value+=d.value;m.documents++;m.suppliers.add(d.supplier);monthMap.set(d.month,m);
    const t=typeMap.get(d.type)||{key:d.type,value:0,documents:0};t.value+=d.value;t.documents++;typeMap.set(d.type,t);
    const tk=termKey(d.payterm), tm=termMap.get(tk)||{key:tk,value:0,documents:0};tm.value+=d.value;tm.documents++;termMap.set(tk,tm);
  }
  const suppliers=[...supplierMap.values()].map(s=>({...s,activeMonths:s.months.size,avgDocument:s.documents?s.value/s.documents:0,avgPayterm:s.documents?s.termTotal/s.documents:0,months:undefined})).sort((a,b)=>b.value-a.value);
  const total=data.documents.reduce((s,d)=>s+d.value,0), positive=Math.max(0,total);let running=0;
  suppliers.forEach(s=>{running+=Math.max(0,s.value);const share=positive?running/positive:1;s.segment=share<=.8?'A':share<=.95?'B':'C';});
  const products=Object.entries(data.products).map(([product,p])=>({product,value:p.value,qty:p.qty,documents:p.documents.size})).sort((a,b)=>b.value-a.value);
  result[year]={summary:{value:total,documents:data.documents.length,suppliers:suppliers.length,avgDocument:data.documents.length?total/data.documents.length:0,vat:data.documents.reduce((s,d)=>s+d.vat,0)},
    monthly:[...monthMap.values()].map(m=>({...m,suppliers:m.suppliers.size})).sort((a,b)=>a.month-b.month),suppliers,products,types:[...typeMap.values()].sort((a,b)=>b.value-a.value),terms:[...termMap.values()].sort((a,b)=>b.value-a.value)};
}
fs.writeFileSync(path.join(root,'purchase_analytics.js'),'window.PURCHASE_ANALYTICS='+JSON.stringify(result)+';','utf8');
console.log(`Generated purchase analytics for ${Object.keys(result).length} years.`);
