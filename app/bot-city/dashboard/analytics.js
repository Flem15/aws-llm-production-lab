/* Read-only local artifact viewer. Never interpolate ledger text as HTML. */
'use strict';
let report, visible = 50;
const $ = id => document.getElementById(id);
const money = n => n == null ? '—' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n);
const pct = n => n == null ? '—' : (n*100).toFixed(1)+'%';
function stats(rows){
 let total=0,peak=0,dd=0,gain=0,loss=0,wins=0;
 for(const r of rows){total+=r.pnl;peak=Math.max(peak,total);dd=Math.max(dd,peak-total);if(r.pnl>0){gain+=r.pnl;wins++;}if(r.pnl<0)loss-=r.pnl;}
 return [rows.length,money(total),rows.length?pct(wins/rows.length):'—',rows.length?money(total/rows.length):'—',loss?(gain/loss).toFixed(2):rows.length?'N/A · no losses':'N/A · no trades',money(dd)];
}
function cell(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
function table(id,groups){$(id).replaceChildren();for(const [key,rows] of Object.entries(groups).sort()){const tr=document.createElement('tr');for(const value of [key,...stats(rows)])tr.append(cell('td',value));$(id).append(tr);}}
function group(rows,key){const out=Object.create(null);for(const row of rows){const name=row[key]||'unknown (legacy)';(out[name]??=[]).push(row);}return out;}
function render(){
 const rows=report.trades.filter(r=>(!$('strategy').value||(r.strategy||'unknown (legacy)')===$('strategy').value)&&(!$('from').value||r.day>=$('from').value)&&(!$('through').value||r.day<=$('through').value));
 const values=stats(rows),labels=['Closed trades','Realized P&L','Win rate','Expectancy / trade','Profit factor','Realized drawdown'];$('metrics').replaceChildren();
 labels.forEach((label,i)=>{const div=document.createElement('div');div.className='metric';div.append(cell('span',label),cell('strong',values[i]));$('metrics').append(div);});
 table('comparison',group(rows,'strategy'));table('daily',group(rows,'day'));
 let total=0;const points=[0,...rows.map(r=>total+=r.pnl)],lo=points.reduce((a,b)=>Math.min(a,b),0),hi=points.reduce((a,b)=>Math.max(a,b),0),span=hi-lo||1;
 $('curve').replaceChildren();const path=document.createElementNS('http://www.w3.org/2000/svg','polyline');path.setAttribute('points',points.map((p,i)=>`${10+i*880/Math.max(1,points.length-1)},${180-(p-lo)*160/span}`).join(' '));path.setAttribute('fill','none');path.setAttribute('stroke','#7ce2c2');path.setAttribute('stroke-width','2');$('curve').append(path);
 for(const [x,y,text] of [[10,15,money(hi)],[10,198,money(lo)],[650,15,rows.length?rows[0].day+' → '+rows[rows.length-1].day:'No trades']]){const label=document.createElementNS('http://www.w3.org/2000/svg','text');label.setAttribute('x',x);label.setAttribute('y',y);label.setAttribute('fill','#a9bbc4');label.setAttribute('font-size','12');label.textContent=text;$('curve').append(label);}
 $('journal').replaceChildren();if(!rows.length)$('journal').append(cell('p','No closed trades in this selection.'));
 for(const row of rows.slice().reverse().slice(0,visible)){const d=document.createElement('details');d.append(cell('summary',`${row.day} · ${row.worker} · ${row.contract} · ${money(row.pnl)} · ${row.reconciliation_status||'unknown reconciliation'}`),cell('pre',JSON.stringify(row,null,2)));$('journal').append(d);}$('more').hidden=rows.length<=visible;
}
async function load(){try{const response=await fetch('analytics.json',{cache:'no-store'});if(!response.ok)throw Error('report unavailable');report=await response.json();if(report.schema_version!==1||!Array.isArray(report.trades))throw Error('unsupported report');
 $('status').textContent=`${report.source.mode === "backtest" ? "SYNTHETIC BACKTEST · " : ""}Read-only report generated ${report.generated_at}. ${report.warnings.join(' ')}`;
 $('source').textContent=`${report.basis} Source: ${JSON.stringify(report.source)}. Historical performance does not establish future returns.`;
 for(const strategy of Object.keys(report.strategies).sort()){const o=cell('option',strategy);o.value=strategy;$('strategy').append(o);}render();
 }catch(e){$('status').textContent='No validated report available. Generate analytics.json from the complete local ledger or a reconciled durable export. No network or trading actions are required.';}}
for(const id of ['strategy','from','through'])$(id).addEventListener('change',()=>{visible=50;if(report)render();});
$('reset').addEventListener('click',()=>{for(const id of ['strategy','from','through'])$(id).value='';visible=50;if(report)render();});$('more').addEventListener('click',()=>{visible+=50;render();});load();
