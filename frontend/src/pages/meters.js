import {meters} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,date,notify,fmt} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {LineStatus,BatchStatus,ReadingStatus} from '../constants/enums.js';

const canWrite=()=>session.user.role==='admin'||session.user.role==='analyst';
const isAdmin=()=>session.user.role==='admin';

const lineColumns=[
 {key:'reading_date',label:'日期'},{key:'prev_date',label:'上一读数日'},
 {key:'status',label:'状态'},{key:'entry_count',label:'进区增量'},
 {key:'completed_count',label:'完成增量'},{key:'reason',label:'依据/原因'}];
const readingColumns=[
 {key:'reading_date',label:'日期'},{key:'generation',label:'代次'},
 {key:'entry_total',label:'进区累计'},{key:'completed_total',label:'完成累计'},
 {key:'version',label:'版本'},{key:'status',label:'状态'}];

export async function meterPage(routeId){
 workspace.meterGate=await meters.gate();
 workspace.meterDevices=await meters.devices();
 const byId=workspace.meterDevices.find(d=>d.id===Number(routeId));
 const selected=byId||workspace.meterDevices.find(d=>d.id===workspace.selectedMeter)||workspace.meterDevices[0]||null;
 workspace.selectedMeter=selected?selected.id:null;
 if(selected){
   workspace.meterReadings=await meters.readings(selected.id);
   const s=await meters.settlement(selected.id);
   workspace.meterSettlement=s;
   workspace.meterBatches=await meters.batches(selected.id);
   workspace.meterCorrections=await meters.corrections(selected.id);
 }else{
   workspace.meterReadings=[];workspace.meterSettlement={batch:null,lines:[]};
   workspace.meterBatches=[];workspace.meterCorrections=[];
 }
 return heading('06 / METER SETTLEMENT','抄表结算与换机留痕',
   '累计计数器跨日不清零。按相邻确认读数差生成进区与完成人数；缺日补报、代次倒退或完成增量超进区时隔离，上游更正触发整组重算并保留历史归档。',
   '<button class="button" id="refresh-meters">刷新重算进度 ↻</button>')+gateBanner()+deviceLayout(selected);
}

function gateBanner(){
 const g=workspace.meterGate;
 if(!g.device_count)return '<section class="panel empty">尚未登记计数设备。先在左侧登记设备的展区、方案与换机代次。</section>';
 const items=(g.reasons||[]).map(r=>'<li>'+esc(r)+'</li>').join('');
 return '<section class="gate '+(g.blocked?'blocked':'open')+'"><div><p class="eyebrow">'+(g.blocked?'GATE CLOSED / 结算闸门关闭':'GATE OPEN / 结算闸门开放')+'</p>'
   +(g.blocked?'<h2>新分析与简报已被拦截</h2><ul class="questions">'+items+'</ul>':'<h2>口径有效，可发起新分析与简报</h2>')
   +'</div><div class="gate-numbers">'+[['设备',g.device_count],['重算中',(g.recalculating||[]).length],['拦截原因',(g.reasons||[]).length]].map(([k,v])=>'<div><span>'+k+'</span><strong>'+v+'</strong></div>').join('')+'</div></section>';
}

function deviceLayout(selected){
 const g=workspace.meterGate;
 return '<div class="meter-grid"><section class="panel"><div class="section-title"><h2>计数设备</h2><span>'+workspace.meterDevices.length+' 台</span></div>'
   +'<div class="batch-list">'+workspace.meterDevices.map(d=>{
       const st=(g.devices||[]).find(x=>x.device.id===d.id);
       return '<button class="batch-item'+(d.id===workspace.selectedMeter?' selected':'')+'" data-device="'+d.id+'"><strong>'+esc(d.code)+' · 第 '+d.generation+' 代</strong><span>'+esc(d.zone)+' · 方案 '+esc(d.arm)+'</span><small>'+(st?('隔离 '+st.counts.quarantined_days+' · 缺日 '+st.counts.missing_days+' · 断档 '+st.counts.broken_days):'')+'</small></button>';
     }).join('')+'</div>'
   +'<div class="section-title"><h2>登记设备 / 展区方案代次</h2></div>'
   +'<form id="device-form"><label>设备编码<input name="code" required minlength="2" placeholder="例如 CNT-SW-07"></label>'
   +'<div class="parameter-grid"><label>展区<input name="zone" required placeholder="水世界"></label><label>导览方案<input name="arm" required placeholder="A / B"></label></div>'
   +'<label>换机代次<input name="generation" type="number" min="1" value="1" required></label>'
   +'<label>备注<input name="note" placeholder="安装位置/初始读数说明"></label>'
   +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>登记设备</button></form>'
   +(selected?'<div class="section-title" style="margin-top:26px"><h2>换机登记</h2></div><form id="replace-form"><label>换机依据（故障/轮换说明）<input name="reason" required placeholder="例如：主板故障，换新机"></label><button class="button" '+(canWrite()?'':'disabled')+'>登记换机（代次 +1）</button><p class="hint">新代次首日读数作为新基线，不与旧代次取差。</p></form>':'')
   +'</section><section class="panel">'+(selected?deviceDetail(selected):'<div class="empty">选择或登记一台设备后开始抄表。</div>')+'</section></div>';
}

function deviceDetail(d){
 const s=workspace.meterSettlement,batch=s.batch,lines=s.lines||[];
 const batchLabel=batch?(BatchStatus[batch.status]||batch.status):'尚无结算';
 const progress=batch&&batch.total_dates?Math.round(batch.processed_dates/batch.total_dates*100):0;
 return '<div class="section-title"><h2>'+esc(d.code)+' · '+esc(d.zone)+' · 方案 '+esc(d.arm)+'</h2><span>第 '+d.generation+' 代 · '+esc(batchLabel)+'</span></div>'
   +(batch?'<div class="recompute-progress"><div><small>重算进度 '+batch.processed_dates+'/'+batch.total_dates+' · '+esc(batch.progress)+(batch.invalid_reason?(' · '+esc(batch.invalid_reason)):'')+'</small><div class="bar"><i style="width:'+progress+'%"></i></div></div></div>':'')
   +'<div class="parameter-grid" style="margin:18px 0"><form id="reading-form" style="grid-column:1/-1"><div class="parameter-grid">'
   +'<label>读数日期<input name="reading_date" type="date" required></label>'
   +'<label>进区累计<input name="entry_total" type="number" min="0" required></label>'
   +'<label>完成累计<input name="completed_total" type="number" min="0" required></label>'
   +'<label>补报/更正原因<input name="reason" placeholder="缺日补报或上游更正说明"></label></div>'
   +'<button type="submit" class="primary" '+(canWrite()?'':'disabled')+'>补读数 / 更正并触发重算</button><p class="hint">同代次同日重复提交视为更正，保留旧值留痕；缺日补报后受影响日期整组重算。</p></form></div>'
   +settlementTable(lines)
   +'<div class="two-columns" style="margin-top:24px"><div><div class="section-title"><h2>确认读数（版本留痕）</h2><span>'+workspace.meterReadings.length+' 条</span></div>'
   +table(readingColumns,mapReadings(workspace.meterReadings))+'</div><div><div class="section-title"><h2>更正与换机依据</h2><span>'+workspace.meterCorrections.length+' 条</span></div>'
   +correctionTable(workspace.meterCorrections)+'</div></div>'
   +'<div style="margin-top:24px"><div class="section-title"><h2>结算批次 / 失效原因与归档</h2><span>'+workspace.meterBatches.length+' 批</span>'
   +(isAdmin()?'<button class="button small" id="recompute-btn">人工触发整组重算</button>':'')+'</div>'+batchTable(workspace.meterBatches)+'</div>';
}

function mapReadings(rows){return rows.map(r=>({...r,status:ReadingStatus[r.status]||r.status}));}

function settlementTable(lines){
 if(!lines.length)return '<div class="empty">该代次尚无结算行，先补第一条读数建立基线。</div>';
 const rows=lines.map(x=>({...x,cls:x.status,status:LineStatus[x.status]||x.status,
   entry_count:x.entry_count==null?'—':fmt(x.entry_count),completed_count:x.completed_count==null?'—':fmt(x.completed_count),
   prev_date:x.prev_date||'—'}));
 return '<div class="section-title"><h2>当前结算（相邻日读数差）</h2><span>'+lines.length+' 行</span></div>'
   +'<div class="table-wrap"><table><thead><tr>'+lineColumns.map(c=>'<th>'+esc(c.label)+'</th>').join('')+'</tr></thead><tbody>'
   +rows.map(r=>'<tr class="line-'+esc(r.cls)+'">'+lineColumns.map(c=>'<td>'+esc(r[c.key])+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}

function correctionTable(rows){
 if(!rows.length)return '<div class="empty">暂无更正记录。</div>';
 return '<div class="table-wrap"><table><thead><tr><th>日期</th><th>原进区/完成</th><th>新进区/完成</th><th>原因</th><th>批次</th></tr></thead><tbody>'
   +rows.map(c=>'<tr><td>'+esc(c.reading_date)+'</td><td>'+c.old_entry+' / '+c.old_completed+'</td><td>'+c.new_entry+' / '+c.new_completed+'</td><td>'+esc(c.reason||'—')+'</td><td>#'+c.batch_id+'</td></tr>').join('')+'</tbody></table></div>';
}

function batchTable(rows){
 if(!rows.length)return '<div class="empty">尚无结算批次。</div>';
 return '<div class="table-wrap"><table><thead><tr><th>批次</th><th>代次</th><th>状态</th><th>触发</th><th>受影响日期</th><th>进度</th><th>失效原因</th><th>时间</th><th></th></tr></thead><tbody>'
   +rows.map(b=>'<tr><td>#'+b.id+'</td><td>'+b.generation+'</td><td>'+esc(BatchStatus[b.status]||b.status)+'</td><td>'+esc(b.trigger)+'</td><td>'+(b.affected_dates||[]).length+' 天</td><td>'+b.processed_dates+'/'+b.total_dates+'</td><td>'+esc(b.invalid_reason||'—')+'</td><td>'+date(b.created_at)+'</td><td><button class="text-button" data-batch-lines="'+b.id+'">查看归档行</button></td></tr>').join('')
   +'</tbody></table></div><div id="batch-lines-slot"></div>';
}

export function bindMeters(refresh){
 document.querySelector('#refresh-meters')?.addEventListener('click',refresh);
 document.querySelectorAll('[data-device]').forEach(btn=>btn.onclick=()=>{workspace.selectedMeter=Number(btn.dataset.device);location.hash='#/meters/'+btn.dataset.device;});
 const devForm=document.querySelector('#device-form');
 if(devForm)submit(devForm,async f=>{const d=await meters.createDevice({code:f.get('code'),zone:f.get('zone'),arm:f.get('arm'),generation:Number(f.get('generation')),note:f.get('note')});workspace.selectedMeter=d.id;notify('设备已登记');location.hash='#/meters/'+d.id;await refresh();});
 const repForm=document.querySelector('#replace-form');
 if(repForm)submit(repForm,async f=>{const id=workspace.selectedMeter;const d=await meters.replace(id,f.get('reason'));notify('已登记换机，当前为第 '+d.generation+' 代');await refresh();});
 const rdForm=document.querySelector('#reading-form');
 if(rdForm)submit(rdForm,async f=>{const id=workspace.selectedMeter;const out=await meters.addReading({device_id:id,reading_date:f.get('reading_date'),entry_total:Number(f.get('entry_total')),completed_total:Number(f.get('completed_total')),reason:f.get('reason')});notify(out.changed?'读数已接收，受影响日期已重算/排队':'读数未变化');await refresh();});
 document.querySelector('#recompute-btn')?.addEventListener('click',async()=>{try{const id=workspace.selectedMeter;await meters.recompute(id,'人工触发重算');notify('整组重算已入队');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-batch-lines]').forEach(btn=>btn.onclick=async()=>{try{const data=await meters.batchLines(btn.dataset.batchLines);const slot=document.querySelector('#batch-lines-slot');slot.innerHTML='<div class="section-title" style="margin-top:16px"><h2>批次 #'+data.batch.id+' 归档行（历史保留）</h2><span>'+esc(BatchStatus[data.batch.status]||data.batch.status)+'</span></div>'+settlementTable(data.lines);}catch(e){notify(e.message,true);}});
}
