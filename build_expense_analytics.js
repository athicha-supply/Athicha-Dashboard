const fs=require('fs'),path=require('path'),root=__dirname;
const rows=JSON.parse(fs.readFileSync(path.join(root,'view_exports','export_Expense.json'),'utf8'));
const clean=v=>String(v??'').replace(/\u00a0/g,' ').trim();
const years={};
for(const r of rows){const year=clean(r.Years),month=Number(r.MonthNo),group=clean(r.group),account=clean(r.accnam)||clean(r.accnum);if(!year||!month||group!=='5'||account==='ซื้อ')continue;
 const y=years[year]||={monthly:{},accounts:{}};const value=Number(r.amount)||0;
 const m=y.monthly[month]||={month,value:0,transactions:0};m.value+=value;m.transactions++;y.monthly[month]=m;
 const a=y.accounts[account]||={account,value:0,transactions:0,months:new Set(),monthly:{}};a.value+=value;a.transactions++;a.months.add(month);a.monthly[month]=(a.monthly[month]||0)+value;y.accounts[account]=a;}
const result={};
for(const [year,y] of Object.entries(years)){const monthly=Object.values(y.monthly).sort((a,b)=>a.month-b.month),accounts=Object.values(y.accounts).map(a=>({account:a.account,value:a.value,transactions:a.transactions,activeMonths:a.months.size,monthly:a.monthly,avgTransaction:a.transactions?a.value/a.transactions:0})).sort((a,b)=>b.value-a.value);const total=monthly.reduce((s,m)=>s+m.value,0),avg=monthly.length?total/monthly.length:0;
 const prev=years[String(Number(year)-1)]?.accounts||{};accounts.forEach(a=>{const pv=prev[a.account]?.value||0;a.previousValue=pv;a.growth=pv?((a.value-pv)/Math.abs(pv))*100:null;});
 result[year]={summary:{value:total,transactions:monthly.reduce((s,m)=>s+m.transactions,0),accounts:accounts.length,avgMonth:avg},monthly:monthly.map(m=>({...m,deviation:avg?((m.value-avg)/avg)*100:0})),accounts};}
fs.writeFileSync(path.join(root,'expense_analytics.js'),'window.EXPENSE_ANALYTICS='+JSON.stringify(result)+';','utf8');console.log(`Generated expense analytics for ${Object.keys(result).length} years.`);
