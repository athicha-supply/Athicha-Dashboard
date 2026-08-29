const fs=require('fs');
const path=require('path');
const root=__dirname;
const sales=JSON.parse(fs.readFileSync(path.join(root,'view_exports','export_SalesDetails.json'),'utf8'));
const stock=JSON.parse(fs.readFileSync(path.join(root,'view_exports','export_StockValue.json'),'utf8'));
const clean=value=>String(value??'').replace(/\u00a0/g,' ').trim();
const stockMap=new Map();
for(const row of stock){
  const code=clean(row.stkcod);if(!code)continue;
  const item=stockMap.get(code)||{stockQty:0,stockValue:0};
  item.stockQty+=Number(row['ยอดคงเหลือ'])||0;item.stockValue+=Number(row['มูลค่าคงเหลือ'])||0;stockMap.set(code,item);
}
const yearly=new Map();
for(const row of sales){
  const year=clean(row.Years),code=clean(row.stkcod),month=Number(row.Months);if(!year||!code||!month)continue;
  if(!yearly.has(year))yearly.set(year,new Map());const map=yearly.get(year);
  const p=map.get(code)||{code,product:clean(row.stkdes)||code,value:0,qty:0,documents:new Set(),customers:new Set(),months:new Set(),monthly:{},lastMonth:0};
  const value=Number(row.Total)||0;p.value+=value;p.qty+=Number(row.Qty)||0;p.monthly[month]=(p.monthly[month]||0)+value;p.documents.add(clean(row.docnum));p.customers.add(clean(row.cuscod));p.months.add(month);p.lastMonth=Math.max(p.lastMonth,month);map.set(code,p);
}
const result={};
for(const [year,map] of [...yearly.entries()].sort()){
  const previous=yearly.get(String(Number(year)-1));
  const cutoffMonth=Math.max(...[...map.values()].map(p=>p.lastMonth));
  const products=[...map.values()].map(p=>{
    const prev=previous?.get(p.code),stk=stockMap.get(p.code)||{stockQty:0,stockValue:0};
    const previousValue=prev?Object.entries(prev.monthly).filter(([month])=>Number(month)<=cutoffMonth).reduce((sum,[,value])=>sum+value,0):0;
    const growth=previousValue!==0?(p.value-previousValue)/Math.abs(previousValue)*100:null;
    const turnover=stk.stockQty>0?p.qty/stk.stockQty:null;
    let signal='stable';
    if(stk.stockQty<=0&&p.qty>0)signal='stockout';else if(stk.stockValue>0&&turnover!==null&&turnover<.5)signal='slow';else if(growth!==null&&growth>=20)signal='growing';else if(growth!==null&&growth<=-20)signal='declining';
    return {code:p.code,product:p.product,value:p.value,qty:p.qty,documents:p.documents.size,customers:p.customers.size,activeMonths:p.months.size,lastMonth:p.lastMonth,avgPrice:p.qty?p.value/p.qty:0,previousValue,growth,stockQty:stk.stockQty,stockValue:stk.stockValue,turnover,signal};
  }).sort((a,b)=>b.value-a.value);
  const total=products.reduce((s,p)=>s+p.value,0);let running=0;
  products.forEach(p=>{running+=Math.max(0,p.value);const share=total?running/total:1;p.segment=share<=.8?'A':share<=.95?'B':'C';});
  result[year]={summary:{value:total,qty:products.reduce((s,p)=>s+p.qty,0),products:products.length,documents:products.reduce((s,p)=>s+p.documents,0),customers:products.reduce((s,p)=>s+p.customers,0)},products};
}
fs.writeFileSync(path.join(root,'product_analytics.js'),'window.PRODUCT_ANALYTICS='+JSON.stringify(result)+';','utf8');
console.log(`Generated product analytics for ${Object.keys(result).length} years.`);
