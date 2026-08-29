const fs=require('fs'),path=require('path'),root=__dirname;
const rows=JSON.parse(fs.readFileSync(path.join(root,'view_exports','export_StockValue.json'),'utf8'));
const clean=v=>String(v??'').replace(/\u00a0/g,' ').trim(),map=new Map();
for(const r of rows){const code=clean(r.stkcod);if(!code)continue;const p=map.get(code)||{code,product:clean(r.stkdes)||code,group:clean(r.typdes)||clean(r.stkgrp)||'ไม่ระบุกลุ่ม',unit:clean(r['หน่วยย่อย']),unitPrice:0,qty:0,value:0};p.qty+=Number(r['ยอดคงเหลือ'])||0;p.value+=Number(r['มูลค่าคงเหลือ'])||0;p.unitPrice=Number(r['ราคาต่อหน่วย'])||p.unitPrice;map.set(code,p);}
const products=[...map.values()].sort((a,b)=>b.value-a.value),groups=new Map();for(const p of products){const g=groups.get(p.group)||{group:p.group,products:0,qty:0,value:0,negative:0};g.products++;g.qty+=p.qty;g.value+=p.value;if(p.qty<0)g.negative++;groups.set(p.group,g);}
const data={summary:{products:products.length,qty:products.reduce((s,p)=>s+p.qty,0),value:products.reduce((s,p)=>s+p.value,0),negative:products.filter(p=>p.qty<0).length},groups:[...groups.values()].sort((a,b)=>b.value-a.value),products};
fs.writeFileSync(path.join(root,'inventory_analytics.js'),'window.INVENTORY_ANALYTICS='+JSON.stringify(data)+';','utf8');console.log(`Generated inventory analytics for ${products.length} products.`);
