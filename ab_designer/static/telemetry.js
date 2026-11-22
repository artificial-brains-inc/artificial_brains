
'use strict';
const $=id=>document.getElementById(id), canvas=$('graph'),ctx=canvas.getContext('2d');
let expectedBits=0,expectedInputs=0,design=null,pops=[],byName=new Map(),edges=[],selected=null,liveFrame=null,shown=null,history=[],frozenHistory=[],frozen=false,epoch=null,seq=null,connected=false,stale=true,eventSource=null;
let pan={x:20,y:20,z:1},drag=null,dirty=true,viewW=1,viewH=1,lastReceive=0,detailGeometry=null;
const HISTORY=160;
function decode(value){const s=atob(value||'');return Uint8Array.from(s,c=>c.charCodeAt(0));}
function bit(array,i){return array&&((array[i>>3]>>(i&7))&1)||0;}
function on(p,i,f=shown){return f?bit(p.kind==='input'?f.inputs:f.bits,p.slots[i]):0;}
function label(text){$('connection').textContent=text;$('connection').className='status'+(connected&&!stale?' live':'');}
function markStale(message){stale=true;label(message);dirty=true;}
function populationLayout(layout){
 expectedBits=Math.ceil((layout.neurons||[]).length/8);expectedInputs=Math.ceil((layout.inputs||[]).reduce((sum,x)=>sum+x.n,0)/8);
 pops=[];byName=new Map();
 const source=new Map((design?.nodes||[]).map(n=>[n.name,n]));
 function get(name,kind){if(!byName.has(name)){const n=source.get(name);const p={name,kind:n?.kind||kind,slots:[],indices:[],rhythm:n?.rhythm||'',x:0,y:0,w:0,h:0};byName.set(name,p);pops.push(p);}return byName.get(name);}
 (layout.neurons||[]).forEach((uid,slot)=>{const at=uid.lastIndexOf(':');if(at<0)return;const name=uid.slice(0,at),p=get(name,(layout.output_populations||[]).includes(name)?'output':'opu');p.slots.push(slot);p.indices.push(Number(uid.slice(at+1)));});
 let offset=0;for(const input of layout.inputs||[]){const p=get(input.name,'input');for(let i=0;i<input.n;i++){p.slots.push(offset++);p.indices.push(i);}}
 const columns=Math.max(1,Math.ceil(Math.sqrt(pops.length)));
 let saved=pops.length>1&&pops.every(p=>{const n=source.get(p.name);return Number.isFinite(n?.x)&&Number.isFinite(n?.y);});
 const xs=pops.map(p=>source.get(p.name)?.x||0),ys=pops.map(p=>source.get(p.name)?.y||0);
 function spacing(a){const u=[...new Set(a)].sort((x,y)=>x-y),d=u.slice(1).map((v,i)=>v-u[i]).filter(v=>v>0);return d.length?Math.min(...d):1;}
 if(new Set(xs).size===1&&new Set(ys).size===1)saved=false;
 for(const p of pops){p.cols=Math.max(1,Math.ceil(Math.sqrt(p.slots.length)));p.w=Math.max(228,32+p.cols*7);p.h=74+Math.ceil(p.slots.length/p.cols)*7;}
 const gapX=Math.max(240,...pops.map(p=>p.w))+70,gapY=Math.max(180,...pops.map(p=>p.h))+45;
 for(let k=0;k<pops.length;k++){const p=pops[k];p.x=saved?(xs[k]-Math.min(...xs))/spacing(xs)*gapX:(k%columns)*gapX;p.y=saved?(ys[k]-Math.min(...ys))/spacing(ys)*gapY:Math.floor(k/columns)*gapY;}
 edges=(design?.connections||[]).filter(e=>byName.has(e.from)&&byName.has(e.to));
 selected=byName.get(selected?.name)||pops[0]||null;
 $('empty').hidden=pops.length>0;$('scope').textContent=design?'Connections and labels come from your public brain design.':'Showing neuron populations from the installed brain. No matching local design was found.';
 renderPopulationList();fit();dirty=true;
}
function fit(){if(!pops.length)return;const left=Math.min(...pops.map(p=>p.x)),top=Math.min(...pops.map(p=>p.y)),right=Math.max(...pops.map(p=>p.x+p.w)),bottom=Math.max(...pops.map(p=>p.y+p.h));pan.z=Math.min((viewW-50)/Math.max(1,right-left),(viewH-50)/Math.max(1,bottom-top));pan.x=(viewW-(right-left)*pan.z)/2-left*pan.z;pan.y=(viewH-(bottom-top)*pan.z)/2-top*pan.z;dirty=true;}
function zoom(f,x=viewW/2,y=viewH/2){const z=Math.max(.025,Math.min(8,pan.z*f));pan.x=x-(x-pan.x)*z/pan.z;pan.y=y-(y-pan.y)*z/pan.z;pan.z=z;dirty=true;}
function resize(){const b=canvas.getBoundingClientRect(),d=devicePixelRatio||1;viewW=b.width;viewH=b.height;canvas.width=Math.round(viewW*d);canvas.height=Math.round(viewH*d);ctx.setTransform(d,0,0,d,0,0);dirty=true;}
new ResizeObserver(resize).observe(canvas);
function draw(){requestAnimationFrame(draw);if(!dirty)return;dirty=false;ctx.clearRect(0,0,viewW,viewH);ctx.save();ctx.translate(pan.x,pan.y);ctx.scale(pan.z,pan.z);
 for(const e of edges){const a=byName.get(e.from),b=byName.get(e.to);ctx.strokeStyle=e.type==='inh'?'#bb7791':'#476780';ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=1.2;const ax=a.x+a.w/2,ay=a.y+a.h/2,bx=b.x+b.w/2,by=b.y+b.h/2,dx=bx-ax,dy=by-ay,l=Math.hypot(dx,dy);if(!l)continue;const exit=Math.min(a.w/2/Math.max(.001,Math.abs(dx)),a.h/2/Math.max(.001,Math.abs(dy))),entry=Math.min(b.w/2/Math.max(.001,Math.abs(dx)),b.h/2/Math.max(.001,Math.abs(dy))),x=bx-dx*entry-dx/l*5,y=by-dy*entry-dy/l*5;ctx.beginPath();ctx.moveTo(ax+dx*exit,ay+dy*exit);ctx.lineTo(x,y);ctx.stroke();ctx.beginPath();if(e.type==='inh'){ctx.moveTo(x-dy/l*5,y+dx/l*5);ctx.lineTo(x+dy/l*5,y-dx/l*5);ctx.stroke();}else{ctx.moveTo(x,y);ctx.lineTo(x-dx/l*7-dy/l*4,y-dy/l*7+dx/l*4);ctx.lineTo(x-dx/l*7+dy/l*4,y-dy/l*7-dx/l*4);ctx.closePath();ctx.fill();}}
 for(const p of pops){ctx.fillStyle='#111c29';ctx.strokeStyle=p===selected?'#70f2d0':'#2c3b4e';ctx.lineWidth=p===selected?2:1;ctx.beginPath();ctx.roundRect(p.x,p.y,p.w,p.h,9);ctx.fill();ctx.stroke();ctx.fillStyle='#e6eef8';ctx.font='600 13px system-ui';ctx.fillText(p.name,p.x+14,p.y+22,p.w-28);ctx.fillStyle='#9aaabe';ctx.font='10px system-ui';ctx.fillText((p.kind==='input'?'INPUT STIMULUS':p.kind==='output'?'BISTABLE OUTPUT':p.rhythm.toUpperCase()||'OPU')+' · '+p.slots.length,p.x+14,p.y+41);
 for(let i=0;i<p.slots.length;i++){ctx.fillStyle=on(p,i)?(stale?'#637d79':p.kind==='input'?'#ffca75':'#70f2d0'):'#304052';ctx.beginPath();ctx.arc(p.x+17+(i%p.cols)*7,p.y+59+Math.floor(i/p.cols)*7,2.05,0,Math.PI*2);ctx.fill();}}
 ctx.restore();drawDetail();}
function drawDetail(){const p=selected;if(!p)return;$('selected').textContent=p.name;$('kind').textContent=p.kind==='input'?'Applied input stimulus; these dots are not measured neural spikes.':p.kind==='output'?'Bistable output neurons retain their active state until switched off.':'One dot per neuron. Bright = firing; dark = inactive.';
 const count=p.slots.reduce((s,_,i)=>s+on(p,i),0);$('active').textContent=shown?count:'—';$('total').textContent='of '+p.slots.length+(p.kind==='input'?' stimulated':' firing')+(stale?' · last received frame':'');
 const c=$('detail'),d=devicePixelRatio||1,w=c.getBoundingClientRect().width;
 if(w<1)return;
 // Fixed readable cells; a large population scrolls instead of shrinking its neurons.
 const cell=32,cols=Math.max(1,Math.floor((w-28)/cell)),h=Math.max(80,28+Math.ceil(p.slots.length/cols)*cell);
 c.style.height=h+'px';c.width=Math.round(w*d);c.height=Math.round(h*d);const g=c.getContext('2d');g.scale(d,d);detailGeometry={cols,cell};
 for(let i=0;i<p.slots.length;i++){
   const active=on(p,i),x=14+(i%cols+.5)*cell,y=14+(Math.floor(i/cols)+.5)*cell;
   g.fillStyle=active?(stale?'#637d79':p.kind==='input'?'#ffca75':'#70f2d0'):'#293d50';
   g.beginPath();g.arc(x,y,10,0,Math.PI*2);g.fill();
   g.fillStyle=active?'#0c2525':'#91a9bc';g.font='8px system-ui';g.textAlign='center';g.textBaseline='middle';g.fillText(String(p.indices[i]),x,y);
 }
 const r=$('raster'),historyNow=shown?(frozen?frozenHistory:history).filter(f=>f.sequence<=shown.sequence):[];r.width=HISTORY;r.height=Math.max(1,p.slots.length);r.style.height=Math.max(130,p.slots.length*5)+'px';const rg=r.getContext('2d'),im=rg.createImageData(HISTORY,r.height);for(let x=0;x<historyNow.length;x++){const f=historyNow[x];for(let i=0;i<p.slots.length;i++){const b=(i*HISTORY+HISTORY-historyNow.length+x)*4;const firing=on(p,i,f);im.data[b]=firing?(p.kind==='input'?255:112):17;im.data[b+1]=firing?(p.kind==='input'?202:242):27;im.data[b+2]=firing?(p.kind==='input'?117:208):40;im.data[b+3]=255;}}rg.putImageData(im,0,0);
 $('history').textContent=historyNow.length?'Rows: neuron indices · Steps '+historyNow[0].step+'–'+historyNow.at(-1).step+' · '+historyNow.length+' recorded frames':'Waiting for recorded neuron activity.';
 $('step').textContent=shown?'Step '+shown.step+(frozen?' · view frozen':''):'Waiting for a completed timestep';}
$('detail').addEventListener('mousemove',e=>{if(!selected||!detailGeometry)return;const b=e.currentTarget.getBoundingClientRect(),{cols,cell}=detailGeometry,x=Math.floor((e.clientX-b.left-14)/cell),y=Math.floor((e.clientY-b.top-14)/cell),i=y*cols+x;if(x>=0&&x<cols&&y>=0&&i<selected.slots.length)$('cell').textContent=selected.name+':'+selected.indices[i]+' · '+(on(selected,i)?(selected.kind==='input'?'stimulated':'firing'):'inactive')+(stale?' (last received frame)':'');});
canvas.addEventListener('pointerdown',e=>{canvas.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,px:pan.x,py:pan.y,moved:false};canvas.style.cursor='grabbing';});
canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.moved||=Math.hypot(dx,dy)>3;pan.x=drag.px+dx;pan.y=drag.py+dy;dirty=true;});
canvas.addEventListener('pointerup',e=>{if(drag&&!drag.moved){const b=canvas.getBoundingClientRect(),x=(e.clientX-b.left-pan.x)/pan.z,y=(e.clientY-b.top-pan.y)/pan.z;selected=[...pops].reverse().find(p=>x>=p.x&&x<=p.x+p.w&&y>=p.y&&y<=p.y+p.h)||selected;renderPopulationList();dirty=true;}drag=null;canvas.style.cursor='grab';});
canvas.addEventListener('wheel',e=>{e.preventDefault();const b=canvas.getBoundingClientRect();zoom(Math.exp(-e.deltaY*.001),e.clientX-b.left,e.clientY-b.top);},{passive:false});
$('fit').onclick=fit;$('plus').onclick=()=>zoom(1.25);$('minus').onclick=()=>zoom(.8);$('freeze').onclick=()=>{frozen=!frozen;if(frozen)frozenHistory=history.slice();$('freeze').textContent=frozen?'Resume view':'Freeze view';if(!frozen)shown=liveFrame;dirty=true;};
function fail(message){eventSource?.close();connected=false;markStale(message);$('reconnect').hidden=false;}
function connect(){eventSource?.close();$('reconnect').hidden=true;connected=false;markStale('Connecting to Prima-Lux…');eventSource=new EventSource('events');
 eventSource.addEventListener('hello',e=>{const m=JSON.parse(e.data);if(epoch!==m.epoch){history=[];frozenHistory=[];shown=liveFrame=null;epoch=m.epoch;populationLayout(m.layout||{});}seq=Number(m.cursor.split(':').at(-1));connected=true;lastReceive=performance.now();if(m.error)fail(m.error);else label('Connected · waiting for neuron activity');});
 eventSource.addEventListener('frame',e=>{try{const m=JSON.parse(e.data);if(m.epoch!==epoch||m.sequence!==seq+1){fail('A frame gap was detected. Reconnect to start a fresh view.');return;}seq=m.sequence;lastReceive=performance.now();if(m.telemetry?.error){fail(m.telemetry.error);return;}if(!m.telemetry)return;const f={sequence:m.sequence,step:m.item.step,bits:decode(m.telemetry.bits),inputs:decode(m.telemetry.input_bits)};if(f.bits.length!==expectedBits||f.inputs.length!==expectedInputs){fail('Incomplete neuron frame received.');return;}history.push(f);if(history.length>HISTORY)history.shift();liveFrame=f;if(!frozen)shown=f;connected=true;stale=false;label('Live · every completed timestep');dirty=true;}catch{fail('Invalid neuron telemetry received.');}});
 eventSource.addEventListener('state',e=>{lastReceive=performance.now();const state=JSON.parse(e.data).state;markStale(state==='RUNNING'?'Connected · waiting for the next completed timestep':state+' · showing last received activity');});
 for(const kind of ['gap','reset'])eventSource.addEventListener(kind,e=>{fail(JSON.parse(e.data).message+' Starting a fresh view.');setTimeout(reconnect,800);});
 eventSource.addEventListener('error',e=>{if(e.data){fail(JSON.parse(e.data).message);return;}connected=false;markStale('Connection interrupted · reconnecting. Last activity remains visible.');$('reconnect').hidden=false;});
}
$('reconnect').onclick=()=>{epoch=null;connect();};
setInterval(()=>{if(connected&&performance.now()-lastReceive>10000)markStale('No recent telemetry · showing last received activity');},2000);
fetch('design').then(r=>r.json()).then(d=>{design=d;connect();}).catch(()=>connect());resize();draw();

async function reconnect(){try{design=await (await fetch('design')).json();}catch{}connect();}
window.addEventListener('message',e=>{if(e.origin===location.origin&&e.data?.type==='ab-reconnect')reconnect();});

function renderPopulationList(){
 const query=$('populationSearch').value.trim().toLowerCase();$('populationList').replaceChildren();
 for(const p of pops){if(query&&!`${p.name} ${p.rhythm} ${p.kind}`.toLowerCase().includes(query))continue;
   const button=document.createElement('button');button.className='population-item'+(p===selected?' selected':'');button.textContent=p.name;
   const sub=document.createElement('small');sub.textContent=(p.rhythm||p.kind)+' · '+p.slots.length+' neurons';button.append(sub);
   button.onclick=()=>{selected=p;renderPopulationList();dirty=true;};$('populationList').append(button);
 }
}
$('populationSearch').oninput=renderPopulationList;
function activityMode(graph){$('graphPane').hidden=!graph;$('graphTools').hidden=!graph;$('activityLayout').classList.toggle('graph-mode',graph);$('focusMode').classList.toggle('active',!graph);$('graphMode').classList.toggle('active',graph);resize();if(graph)fit();dirty=true;}
$('focusMode').onclick=()=>activityMode(false);$('graphMode').onclick=()=>activityMode(true);
new ResizeObserver(()=>{dirty=true;}).observe($('neuronPane'));
