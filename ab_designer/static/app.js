'use strict';

// Standalone UI for a PUBLIC design graph. Never constructs the internal neural graph.
const $ = (id) => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const STAGE_W = 2600;
const STAGE_H = 1800;
const NODE_W = 210;
const NODE_H = 128;
const RHYTHMS = ['high-gamma', 'gamma', 'beta', 'alpha', 'theta', 'delta'];
let design = null;
let generatedCode = '';
let warnings = [];
let selection = null;
let connectMode = false;
let connectStart = null;
let zoom = 1;
let undoStack = [];
let redoStack = [];
let codeDirty = false;
let designDirty = false;
let activeView = "canvas";
let dragging = null;
let portDrag = null;
let panning = null;
let ignoreBackgroundClick = false;
let ignoreNodeClick = false;
let pending = Promise.resolve();
let toastTimer;
let deviceBusy=false;
let deviceInfo={connected:false,state:'DISCONNECTED',ready_for_run:false};

function clone(v) { return JSON.parse(JSON.stringify(v)); }
function num(v) { return Number(v).toFixed(2); }
function isNameSelected(name) { return selection?.kind === 'node' && selection.name === name; }
function isEdgeSelected(edge) { return selection?.kind === 'edge' && selection.from === edge.from && selection.to === edge.to; }
function getNode(name) { return design.nodes.find(n => n.name === name); }
function fmtRhythm(r) { return r==='high-gamma'?'High-gamma':r[0].toUpperCase() + r.slice(1); }
function opuLabel(node){return `${fmtRhythm(node.rhythm)} · ${node.tnorm==null?'TNorm 10 (default)':`TNorm ${num(node.tnorm)}`}`;}
function toast(message, error=false) {
  const el = $('toast'); el.textContent = message; el.classList.toggle('error', error); el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 3800);
}
async function api(path, payload=null) {
  const options = payload === null ? {} : { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) };
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('Cannot reach the local designer. Check that it is still running.'); }
  let body;
  try { body = await response.json(); } catch { throw new Error('Local designer returned an invalid response.'); }
  if (!response.ok) throw new Error(body.error || 'Request failed');
  return body;
}
function syncState(result, {codeReset=true}={}) {
  design = result.design;
  generatedCode = result.code;
  warnings = result.warnings || [];
  if (codeReset || !codeDirty) { $('codeEditor').value = generatedCode; codeDirty=false; }
  $('codeError').hidden=true;
  updatePythonStatus();
  render();
  deviceInfo.ready_for_run=false;updateDeviceControls();
}
function enqueue(task) {
  pending = pending.then(task).catch(e => { toast(e.message, true); render(); return false; });
  return pending;
}
function commit(draft, {record=true, preserveCode=false, undoSnapshot=null}={}) {
  const snapshot = undoSnapshot || (design ? clone(design) : null);
  $('draftStatus').textContent = 'Saving…';
  return enqueue(async () => {
    const response = await api('/api/design', {design:draft});
    if (record && snapshot) { undoStack.push(snapshot); if (undoStack.length > 60) undoStack.shift(); redoStack=[]; }
    syncState(response, {codeReset:!preserveCode});
    designDirty = true;
    $('draftStatus').textContent = 'Not exported';
    return true;
  });
}
async function applyPython() {
  const code = $('codeEditor').value;
  const snapshot = clone(design);
  $('codeError').hidden=true;
  $('draftStatus').textContent='Applying Python…';
  return enqueue(async () => {
    try {
      const response = await api('/api/code/apply',{code});
      undoStack.push(snapshot); if (undoStack.length>60) undoStack.shift(); redoStack=[];
      selection = null; syncState(response); toast('Python applied to the brain canvas.');
      designDirty = true;
      $('draftStatus').textContent='Not exported';
      updatePythonStatus();
      return true;
    } catch (e) {
      $('codeError').textContent=e.message; $('codeError').hidden=false;
      $('draftStatus').textContent='Python needs attention';
      throw e;
    }
  });
}
function stageCoords(clientX,clientY) {
  const rect=$('stage').getBoundingClientRect();
  return {x:(clientX-rect.left)/zoom, y:(clientY-rect.top)/zoom};
}
function placement() {
  const view=$('viewport').getBoundingClientRect();
  const p=stageCoords(view.left+view.width*.5,view.top+view.height*.45);
  const idx=design.nodes.length;
  const offsetX=(idx%3-1)*255;
  const offsetY=Math.floor(idx%9/3)*142;
  return {x:Math.min(STAGE_W-NODE_W-35,Math.max(36,Math.round(p.x-NODE_W/2+offsetX))), y:Math.min(STAGE_H-NODE_H-35,Math.max(36,Math.round(p.y-NODE_H/2+offsetY)))};
}
function nextName(prefix) {
  // Generated names must satisfy the public Brain population-name contract.
  // Spaces made Add Input/OPU/Output fail validation in v0.3.1.
  let i=1; while(getNode(`${prefix}_${i}`)) i++;
  return `${prefix}_${i}`;
}
function add(kind) {
  const node={name:nextName(kind==='opu'?'OPU':kind==='input'?'INPUT':'OUTPUT'),kind,...placement()};
  if(kind==='input') node.neurons=64;
  if(kind==='output') node.neurons=1;
  if(kind==='opu'){ node.rhythm='theta'; node.tnorm=3; }
  const next=clone(design);next.nodes.push(node);selection={kind:'node',name:node.name};
  commit(next); 
}
function newBrain() {
  selection=null;connectMode=false;connectStart=null;updateConnectStatus();
  commit({schema:'artificial-brains/design/v1',name:'Untitled Brain',nodes:[],connections:[]});
}
function starterBrain() {
  const nodes=[
    {name:'SENSE',kind:'input',neurons:64,x:160,y:350},
    {name:'THETA1',kind:'opu',rhythm:'theta',tnorm:3,x:450,y:350},
    {name:'THETA2',kind:'opu',rhythm:'theta',tnorm:2.01,x:740,y:350},
    {name:'BETA',kind:'opu',rhythm:'beta',tnorm:5,x:1030,y:350},
    {name:'RESPONSE',kind:'output',x:1320,y:350},
  ];
  const names=nodes.map(n=>n.name);const connections=names.slice(0,-1).map((s,i)=>({from:s,to:names[i+1],type:'exc'}));
  selection=null;
  commit({schema:'artificial-brains/design/v1',name:'My First Brain',nodes,connections}).then(()=>centerView());
}
function toggleConnectMode(enabled=!connectMode) {
  connectMode=enabled;connectStart=null;
  $('viewport').classList.toggle('connecting',connectMode);
  $('connectBtn').classList.toggle('active',connectMode);
  updateConnectStatus();renderNodes();
}
function updateConnectStatus() {
  $('connectingStatus').textContent = !connectMode?'':connectStart?`${connectStart} → choose target`:'Choose source, then target';
}
function nodeClick(name) {
  if(ignoreNodeClick) return;
  if(connectMode){
    if(!connectStart){
      connectStart=name; updateConnectStatus();renderNodes();
      return;
    }
    finishConnect(connectStart,name);
    connectStart=null;updateConnectStatus();renderNodes();
    return;
  }
  selection={kind:'node',name};render();
}
function finishConnect(source,target){
  const existing=design.connections.some(e=>e.from===source&&e.to===target);
  if(existing){toast('Those populations are already connected.',true);return;}
  const a=getNode(source),b=getNode(target);
  if(!a||!b||b.kind==='input'||(a.kind==='input'&&b.kind==='output')||(a.kind==='output'&&b.kind!=='opu')){
    toast('Connect an input or OPU to a receiving OPU or output.',true);return;
  }
  const next=clone(design);next.connections.push({from:source,to:target,type:'exc'});
  selection={kind:'edge',from:source,to:target};
  commit(next).then(ok=>{if(ok)toast(`${source} → ${target} connected.`);});
}
function makeSvg(tag,attributes={}){
  const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attributes)) e.setAttribute(k,String(v));return e;
}
// Public circuit geometry only. Shapes reflect population roles, never implementation data.
const RHYTHM_COLORS = {'high-gamma':'#5eead4',gamma:'#67e8f9',beta:'#fbbf24',alpha:'#a5b4fc',theta:'#c4a1ff',delta:'#f0abfc'};
function nodeColor(n){return n.kind==='input'?'#a78bfa':n.kind==='output'?'#fb923c':RHYTHM_COLORS[n.rhythm];}
function nodeSymbol(n){return n.kind==='input'?'IN':n.kind==='output'?'OUT':({'high-gamma':'Hγ',gamma:'γ',beta:'β',alpha:'α',theta:'θ',delta:'δ'}[n.rhythm]);}
function shapePoints(n){
  if(n.kind==='input')return [[-29,-27],[29,-27],[29,27],[-29,27]];
  if(n.kind==='output')return [[0,-34],[37,0],[0,34],[-37,0]];
  return [[-36,0],[-18,-30],[18,-30],[36,0],[18,30],[-18,30]];
}
function labelAbove(n){
  return design.connections.some(e=>{
    const peer=e.from===n.name?getNode(e.to):e.to===n.name?getNode(e.from):null;
    if(!peer||peer.name===n.name)return false;
    const dx=peer.x-n.x,dy=peer.y-n.y;
    return dy>40&&Math.abs(dx)<2*dy;
  });
}
function nodeShape(n){
  const attrs={fill:nodeColor(n)+'22',stroke:nodeColor(n),'stroke-width':2};
  if(n.kind==='opu'&&n.rhythm==='high-gamma')return makeSvg('circle',{...attrs,cx:0,cy:0,r:30});
  if(n.kind==='input')return makeSvg('rect',{...attrs,x:-29,y:-27,width:58,height:54,rx:7});
  return makeSvg('polygon',{...attrs,points:shapePoints(n).map(p=>p.join(',')).join(' ')});
}
function nodeCenter(n){return {x:n.x+NODE_W/2,y:n.y+38};}
function boundary(n,ux,uy,offset){
  const c=nodeCenter(n), ox=-uy*offset,oy=ux*offset;
  let t;
  if(n.kind==='opu'&&n.rhythm==='high-gamma') t=Math.sqrt(30*30-offset*offset);
  else {
    const pts=shapePoints(n);let best=Infinity;
    for(let i=0;i<pts.length;i++){
      const [ax,ay]=pts[i], [bx,by]=pts[(i+1)%pts.length],vx=bx-ax,vy=by-ay;
      const den=ux*vy-uy*vx;if(Math.abs(den)<1e-9)continue;
      const qx=ax-ox,qy=ay-oy;
      const ray=(qx*vy-qy*vx)/den,seg=(qx*uy-qy*ux)/den;
      if(ray>=0&&seg>=0&&seg<=1)best=Math.min(best,ray);
    }
    t=Number.isFinite(best)?best:30;
  }
  return {x:c.x+ox+ux*(t+4),y:c.y+oy+uy*(t+4)};
}
function edgeGeometry(a,b){
  const ac=nodeCenter(a),bc=nodeCenter(b);
  if(a.name===b.name){
    const sx=ac.x+34,sy=ac.y,tx=ac.x,ty=ac.y-34,high=ac.y-70;
    return {d:`M ${sx} ${sy} L ${sx+34} ${sy} L ${sx+34} ${high} L ${tx} ${high} L ${tx} ${ty}`,mx:sx+32,my:high-8,tx,ty};
  }
  const dx=bc.x-ac.x,dy=bc.y-ac.y,len=Math.hypot(dx,dy)||1,ux=dx/len,uy=dy/len;
  const offset=design.connections.some(e=>e.from===b.name&&e.to===a.name)?9:0;
  const start=boundary(a,ux,uy,offset),end=boundary(b,-ux,-uy,-offset);
  return {d:`M ${start.x} ${start.y} L ${end.x} ${end.y}`,mx:(start.x+end.x)/2-uy*12,my:(start.y+end.y)/2+ux*12-5,tx:end.x,ty:end.y};
}
function renderEdges(){
  const svg=$('edgeLayer');
  while(svg.lastChild && svg.lastChild.tagName.toLowerCase()!=='defs') svg.removeChild(svg.lastChild);
  if(!svg.querySelector('#inhBar')){
    const def=svg.querySelector('defs');
    const marker=makeSvg('marker',{id:'inhBar',markerWidth:10,markerHeight:14,refX:5,refY:7,orient:'auto',markerUnits:'userSpaceOnUse'});
    marker.appendChild(makeSvg('path',{d:'M5 0 V14',stroke:'#f39ba9','stroke-width':2.5}));def.appendChild(marker);
  }
  for(const edge of design.connections){
    const a=getNode(edge.from),b=getNode(edge.to);if(!a||!b)continue;
    const geom=edgeGeometry(a,b);const selected=isEdgeSelected(edge);
    const g=makeSvg('g');
    const visible=makeSvg('path',{d:geom.d,fill:'none',stroke:edge.type==='inh'?'#ef99a5':'#2ea8c8','stroke-width':selected?3.2:2.2,'stroke-linecap':'round','stroke-linejoin':'round','marker-end':edge.type==='inh'?'url(#inhBar)':'url(#excArrow)',class:`edge-visible${selected?' selected':''}`});
    const hit=makeSvg('path',{d:geom.d,fill:'none',stroke:'#ffffff','stroke-opacity':0.025,'stroke-width':20,class:'edge-hit',tabindex:0,role:'button','aria-label':`${edge.from} to ${edge.to} ${edge.type==='inh'?'inhibitory':'excitatory'}`});
    hit.addEventListener('click',(ev)=>{ev.stopPropagation();selection={kind:'edge',from:edge.from,to:edge.to};render();});
    hit.addEventListener('keydown',(ev)=>{if(ev.key==='Enter'){selection={kind:'edge',from:edge.from,to:edge.to};render();}});
    g.appendChild(visible);g.appendChild(hit);
    if(edge.type==='inh'){
      const text=makeSvg('text',{x:geom.mx,y:geom.my,fill:'#eab2bc',class:'edge-label','text-anchor':'middle'});
      text.textContent=`INH ×${edge.strength??1}`;g.appendChild(text);
    }
    svg.appendChild(g);
  }
}
function createNode(node){
  const el=document.createElement('div');
  el.className=`node ${node.kind} rhythm-${node.rhythm||'none'}${labelAbove(node)?' label-above':''}${isNameSelected(node.name)?' selected':''}${connectStart===node.name?' connect-source':''}`;
  el.style.left=node.x+'px';el.style.top=node.y+'px';
  el.dataset.name=node.name;el.setAttribute('role','button');el.tabIndex=0;el.setAttribute('aria-label',`${node.kind} ${node.name}`);
  el.style.setProperty('--node-color',nodeColor(node));
  const glyph=makeSvg('svg',{class:'node-glyph',viewBox:`0 0 ${NODE_W} 78`,'aria-hidden':'true'});
  const group=makeSvg('g',{transform:`translate(${NODE_W/2} 38)`});group.appendChild(nodeShape(node));
  const symbol=makeSvg('text',{x:0,y:5,'text-anchor':'middle',fill:nodeColor(node),class:'node-symbol'});symbol.textContent=nodeSymbol(node);group.appendChild(symbol);glyph.appendChild(group);
  const top=document.createElement('div');top.className='node-top';
  const dot=document.createElement('span');dot.className='node-type-dot';
  const type=document.createElement('span');type.textContent=node.kind.toUpperCase();top.append(dot,type);
  const title=document.createElement('div');title.className='node-name';title.textContent=node.name;title.title=node.name;
  const meta=document.createElement('div');meta.className='node-meta';
  meta.textContent=node.kind==='opu'?opuLabel(node):node.kind==='input'?`${node.neurons} input neurons`:`${node.neurons||1} output neuron${(node.neurons||1)===1?'':'s'}`;
  el.append(glyph,title,meta);
  for(const port of ['in','out']){
    if(port==='in'&&node.kind==='input')continue;
    const p=document.createElement('button');p.type='button';p.className=`node-port ${port}`;p.title=port==='out'?'Drag to another population to connect':'Receiving connection';p.setAttribute('aria-label',p.title);
    if(port==='out')p.addEventListener('pointerdown',(ev)=>startPortDrag(ev,node.name));
    else p.addEventListener('pointerdown',ev=>ev.stopPropagation());
    p.addEventListener('click',ev=>ev.stopPropagation());el.appendChild(p);
  }
  el.addEventListener('pointerdown',ev=>{
    if(ev.button!==0||ev.target.closest('.node-port'))return;
    const px=ev.clientX,py=ev.clientY;dragging={name:node.name,startX:px,startY:py,x:node.x,y:node.y,moved:false,undoSnapshot:clone(design)};
    el.setPointerCapture(ev.pointerId);
    selection={kind:'node',name:node.name}; renderInspector();
  });
  el.addEventListener('pointermove',ev=>{
    if(!dragging||dragging.name!==node.name)return;
    const dx=(ev.clientX-dragging.startX)/zoom,dy=(ev.clientY-dragging.startY)/zoom;
    if(Math.abs(dx)+Math.abs(dy)>3)dragging.moved=true;
    if(!dragging.moved)return;
    const updated=getNode(node.name);updated.x=Math.round(Math.min(STAGE_W-NODE_W-10,Math.max(10,dragging.x+dx)));
    updated.y=Math.round(Math.min(STAGE_H-NODE_H-10,Math.max(10,dragging.y+dy)));
    el.style.left=updated.x+'px';el.style.top=updated.y+'px';renderEdges();
  });
  el.addEventListener('pointerup',ev=>{
    if(!dragging||dragging.name!==node.name)return;
    const wasMoved=dragging.moved;const snapshot=dragging.undoSnapshot;dragging=null;
    if(wasMoved){ignoreNodeClick=true;setTimeout(()=>{ignoreNodeClick=false;},30);commit(clone(design),{undoSnapshot:snapshot});}
    else nodeClick(node.name);
  });
  el.addEventListener('pointercancel',()=>{dragging=null;});
  el.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();nodeClick(node.name);}});
  return el;
}
function renderNodes(){
  const layer=$('nodesLayer');layer.replaceChildren();
  for(const node of design.nodes)layer.appendChild(createNode(node));
}
function startPortDrag(ev,name){
  if(ev.button!==0)return;
  ev.preventDefault();ev.stopPropagation();
  portDrag={source:name};
  const p=stageCoords(ev.clientX,ev.clientY);
  updatePreview(name,p);
  document.addEventListener('pointermove',portMove);
  document.addEventListener('pointerup',portUp,{once:true});
}
function updatePreview(name,p){
  const node=getNode(name);if(!node)return;
  const sx=node.x+NODE_W/2+38,sy=node.y+38;
  const dx=p.x-sx,dy=p.y-sy;const distance=Math.hypot(dx,dy);
  const w=$('wirePreview');w.hidden=false;w.style.left=sx+'px';w.style.top=sy+'px';w.style.width=distance+'px';w.style.transform=`rotate(${Math.atan2(dy,dx)*180/Math.PI}deg)`;
}
function portMove(ev){if(portDrag)updatePreview(portDrag.source,stageCoords(ev.clientX,ev.clientY));}
function portUp(ev){
  document.removeEventListener('pointermove',portMove);
  const source=portDrag?.source;portDrag=null;$('wirePreview').hidden=true;
  if(!source)return;
  const targetEl=document.elementFromPoint(ev.clientX,ev.clientY)?.closest('.node');
  if(targetEl)finishConnect(source,targetEl.dataset.name);
  else {connectMode=true;connectStart=source;$('connectBtn').classList.add('active');$('viewport').classList.add('connecting');updateConnectStatus();renderNodes();toast('Click the target population to finish the connection.');}
}
function renderOverview(){
  $('inspectorTitle').textContent='Brain overview';
  $('inspectorSubtitle').textContent='Select a population or connection to edit it.';
  const box=$('inspectorFields');box.replaceChildren();
  const summary=document.createElement('div');summary.className='inspector-summary';summary.textContent='Create your brain from inputs, OPUs and outputs. Choose rhythms, TNorm and connections to define how they work together.';box.appendChild(summary);
  for(const [label,count] of [['Input populations',design.nodes.filter(n=>n.kind==='input').length],['OPUs',design.nodes.filter(n=>n.kind==='opu').length],['Output populations',design.nodes.filter(n=>n.kind==='output').length],['Connections',design.connections.length]]){
    const row=document.createElement('div');row.className='metric-row';const l=document.createElement('span');l.textContent=label;const r=document.createElement('strong');r.textContent=count;row.append(l,r);box.appendChild(row);
  }
}
function addField(box,label,value,control='text',options=null,onCommit=()=>{}){
  const wrapper=document.createElement('div');wrapper.className='field';
  const labelNode=document.createElement('label');labelNode.textContent=label;
  const input=control==='select'?document.createElement('select'):document.createElement('input');
  if(control==='select'){
    for(const [name,v] of options){const opt=document.createElement('option');opt.value=v;opt.textContent=name;input.appendChild(opt);}
  }else{input.type=control;if(control==='number'){input.step='1';input.min='1';}input.spellcheck=false;}
  input.value=String(value);input.addEventListener('change',()=>onCommit(input.value));
  wrapper.append(labelNode,input);box.appendChild(wrapper);
  return input;
}
function deleteButton(box,action,label){const b=document.createElement('button');b.className='field-delete';b.textContent=label;b.addEventListener('click',action);box.appendChild(b);}
function renderInspector(){
  if(!design)return;
  $('warningsList').replaceChildren();
  if(warnings.length){
    for(const message of warnings){const w=document.createElement('div');w.className='warning';w.textContent=message;$('warningsList').appendChild(w);}
  }else{const okay=document.createElement('div');okay.className='all-clear';okay.textContent='No structural warnings.';$('warningsList').appendChild(okay);}
  if(selection?.kind==='node' && !getNode(selection.name))selection=null;
  if(selection?.kind==='edge' && !design.connections.some(isEdgeSelected))selection=null;
  if(!selection){renderOverview();return;}
  const box=$('inspectorFields');box.replaceChildren();
  if(selection.kind==='node'){
    const node=getNode(selection.name);
    $('inspectorTitle').textContent=node.name;
    $('inspectorSubtitle').textContent=node.kind==='opu'?'Choose the operating rhythm and TNorm.':node.kind==='input'?'Define the neurons carrying your input.':'A named neural output from the brain.';
    addField(box,'Name',node.name,'text',null,value=>{
      if(value===node.name)return;
      const d=clone(design);const item=d.nodes.find(n=>n.name===node.name);item.name=value;
      d.connections.forEach(e=>{if(e.from===node.name)e.from=value;if(e.to===node.name)e.to=value;});
      selection={kind:'node',name:value};commit(d);
    });
    if(node.kind==='output')addField(box,'Output neurons',node.neurons||1,'number',null,value=>{
      const d=clone(design);d.nodes.find(n=>n.name===node.name).neurons=Number(value);commit(d);
    });
    if(node.kind==='input')addField(box,'Input neurons',node.neurons,'number',null,value=>{
      const d=clone(design);d.nodes.find(n=>n.name===node.name).neurons=Number(value);commit(d);
    });
    if(node.kind==='opu'){
      addField(box,'Rhythm',node.rhythm,'select',RHYTHMS.map(r=>[fmtRhythm(r),r]),value=>{
        const d=clone(design);const target=d.nodes.find(n=>n.name===node.name);target.rhythm=value;
        if(target.tnorm===undefined)target.tnorm=value==='high-gamma'?10:5;
        commit(d);
      });
      const field=addField(box,'TNorm',node.tnorm==null?'':String(node.tnorm),'number',null,value=>{
        const d=clone(design),target=d.nodes.find(n=>n.name===node.name);
        if(value.trim()===''&&node.rhythm==='high-gamma')delete target.tnorm;
        else target.tnorm=Number(value);
        commit(d);
      });field.step='0.01';field.min='0';field.max='10';
      field.placeholder=node.rhythm==='high-gamma'?'10 (default)':'0.00 to 10.00';
      const hint=document.createElement('p');hint.className='field-note';
      hint.textContent=node.rhythm==='high-gamma'?'High-gamma can receive and send excitatory or inhibitory connections throughout the circuit. Choose TNorm 0–10. The default is 10; leaving it blank preserves the saved default-reference design.':'0.00 to 10.00. This selects the neural operating point.';
      box.appendChild(hint);
    }
    deleteButton(box,()=>{
      const d=clone(design);d.nodes=d.nodes.filter(n=>n.name!==node.name);d.connections=d.connections.filter(e=>e.from!==node.name&&e.to!==node.name);
      selection=null;commit(d);
    },'Delete population');
  }else{
    const edge=design.connections.find(isEdgeSelected);
    $('inspectorTitle').textContent='Connection';
    $('inspectorSubtitle').textContent=`${edge.from} → ${edge.to}`;
    addField(box,'Connection type',edge.type,'select',[['Excitatory (default)','exc'],['Inhibitory','inh']],value=>{
      const d=clone(design);const link=d.connections.find(e=>e.from===edge.from&&e.to===edge.to);
      link.type=value;if(value!=='inh')delete link.strength;
      commit(d);
    });
    const targetNode=design.nodes.find(n=>n.name===edge.to);
    const sourceNode=design.nodes.find(n=>n.name===edge.from);
    if(targetNode.kind==='opu'){
      const roles=[['Automatic',''],['Arithmetic operand','operand']];
      if(sourceNode.kind!=='input')roles.push(['Feedback return','feedback']);
      addField(box,'Connection role',edge.role??'','select',roles,value=>{
        const d=clone(design);const link=d.connections.find(e=>e.from===edge.from&&e.to===edge.to);
        if(value)link.role=value;else delete link.role;
        if(value!=='operand')delete link.units;
        commit(d);
      });
      if(edge.role==='operand'){
        const field=addField(box,'Operand units',String(edge.units??1),'number',null,value=>{
          const d=clone(design);const link=d.connections.find(e=>e.from===edge.from&&e.to===edge.to);
          link.units=Number(value);commit(d);
        });field.min='0.000001';field.step='any';
      }
      const roleNote=document.createElement('p');roleNote.className='field-note';
      roleNote.textContent='Automatic recognizes ordinary pathways and unambiguous loops. Use Feedback return to identify an ambiguous loop. For arithmetic, mark every positive operand and each signed operand; units set their relative contributions.';
      box.appendChild(roleNote);
    }
    if(edge.type==='inh'){
      const field=addField(box,'Inhibitory strength (minimum 0.1)',String(edge.strength??1),'number',null,value=>{
        const d=clone(design);const link=d.connections.find(e=>e.from===edge.from&&e.to===edge.to);
        link.strength=Number(value);commit(d);
      });
      field.min='0.1';field.step='any';
    }
    const note=document.createElement('p');note.className='field-note';note.textContent='Strength multiplies the calculated inhibitory reference: 0.9 uses 90%, 1 is the default, and values above 1 increase it. Minimum 0.1; no upper cap. This is not a percentage of spikes suppressed. Delete the connection for no inhibition. TNorm is independent.';box.appendChild(note);
    deleteButton(box,()=>{
      const d=clone(design);d.connections=d.connections.filter(e=>!(e.from===edge.from&&e.to===edge.to));selection=null;commit(d);
    },'Delete connection');
  }
}
function render(){
  if(!design)return;
  $('brainName').value=design.name;
  $('emptyState').hidden=design.nodes.length>0;
  $('stats').textContent=`${design.nodes.length} populations · ${design.connections.length} connections`;
  $('undoBtn').disabled=undoStack.length===0;$('redoBtn').disabled=redoStack.length===0;
  $('zoomLabel').textContent=`${Math.round(zoom*100)}%`;
  renderEdges();renderNodes();renderInspector();
}
function setZoom(v){
  zoom=Math.min(1.5,Math.max(.5,Math.round(v*10)/10));
  $('stage').style.transform=`scale(${zoom})`;
  $('stageShell').style.width=(STAGE_W*zoom)+'px';$('stageShell').style.height=(STAGE_H*zoom)+'px';
  $('zoomLabel').textContent=`${Math.round(zoom*100)}%`;
}
function centerView(){
  if(!design||!design.nodes.length){$('viewport').scrollTo({left:350,top:210,behavior:'smooth'});return;}
  const minX=Math.min(...design.nodes.map(n=>n.x)),maxX=Math.max(...design.nodes.map(n=>n.x+NODE_W));
  const minY=Math.min(...design.nodes.map(n=>n.y)),maxY=Math.max(...design.nodes.map(n=>n.y+NODE_H));
  const view=$('viewport');
  const fit=Math.min(1,(view.clientWidth-65)/(maxX-minX),(view.clientHeight-90)/(maxY-minY));
  setZoom(Math.max(.5,Math.floor(fit*10)/10));
  view.scrollTo({left:Math.max(0,(minX+maxX)*zoom/2-view.clientWidth/2),top:Math.max(0,(minY+maxY)*zoom/2-view.clientHeight/2),behavior:'smooth'});
}
function arrange(){
  if(!design.nodes.length)return;
  const d=clone(design),map=Object.fromEntries(d.nodes.map(n=>[n.name,n]));
  const depths={};const queue=[];
  for(const n of d.nodes.filter(n=>n.kind==='input')){depths[n.name]=0;queue.push(n.name);}
  if(!queue.length){for(const n of d.nodes.filter(n=>n.kind==='opu').slice(0,1)){depths[n.name]=0;queue.push(n.name);}}
  while(queue.length){
    const name=queue.shift();for(const e of d.connections.filter(e=>e.from===name)){
      if(!(e.to in depths)){depths[e.to]=depths[name]+1;queue.push(e.to);}
    }
  }
  const maxDepth=Math.max(0,...Object.values(depths));
  for(const n of d.nodes){if(!(n.name in depths))depths[n.name]=n.kind==='output'?maxDepth+1:0;}
  const groups={};for(const n of d.nodes){const k=depths[n.name];(groups[k]||(groups[k]=[])).push(n);}
  for(const [k,group] of Object.entries(groups))group.forEach((n,i)=>{n.x=90+Number(k)*276;n.y=130+i*159;});
  commit(d).then(()=>centerView());
}
function saveFile(content,filename,type){
  const blob=new Blob([content],{type});const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),500);
}
function fileStem(){return design.name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9_-]+/g,'-').replace(/^-+|-+$/g,'')||'brain';}
function exportDesign(){saveFile(JSON.stringify(design,null,2)+'\n',fileStem()+'.abdesign.json','application/json');designDirty=false;$('draftStatus').textContent='Exported to file';toast('Brain design downloaded.');}
async function exportCurrentDesign(){
  // Python mode shares the same design. Apply pending code before saving.
  if(activeView==='python'&&codeDirty){
    const applied=await applyPython();
    if(applied!==true)return false;
  }
  await pending;
  exportDesign();
  return true;
}
function exportPython(){saveFile($('codeEditor').value,fileStem()+'.py','text/x-python');toast('Python downloaded.');}
function escapeXml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');}
function exportSVG(){
  if(!design.nodes.length){toast('Add a population before exporting.',true);return;}
  const minX=Math.min(...design.nodes.map(n=>n.x))-34,minY=Math.min(...design.nodes.map(n=>n.y))-96;
  const maxX=Math.max(...design.nodes.map(n=>n.x+NODE_W))+54,maxY=Math.max(...design.nodes.map(n=>n.y+NODE_H))+52;
  const lines=[`<svg xmlns="${NS}" viewBox="${minX} ${minY} ${maxX-minX} ${maxY-minY}" width="${maxX-minX}" height="${maxY-minY}">`,`<rect x="${minX}" y="${minY}" width="${maxX-minX}" height="${maxY-minY}" fill="#0e1726"/>`,'<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#2ea8c8"/></marker><marker id="bar" markerWidth="10" markerHeight="14" refX="5" refY="7" orient="auto"><path d="M5 0 V14" stroke="#f39ba9" stroke-width="2.5"/></marker></defs>'];
  for(const edge of design.connections){
    const a=getNode(edge.from),b=getNode(edge.to);const g=edgeGeometry(a,b);
    lines.push(`<path d="${g.d}" stroke="${edge.type==='inh'?'#f39ba9':'#2ea8c8'}" stroke-width="2.5" fill="none" marker-end="url(#${edge.type==='inh'?'bar':'arrow'})"/>`);
    if(edge.type==='inh')lines.push(`<text x="${g.mx}" y="${g.my}" text-anchor="middle" fill="#f39ba9" font-family="sans-serif" font-weight="bold" font-size="11">INH ×${edge.strength??1}</text>`);
  }
  for(const n of design.nodes){
    const c=nodeCenter(n);
    const sub=n.kind==='opu'?opuLabel(n):`${n.neurons||1} ${n.kind} neuron${(n.neurons||1)===1?'':'s'}`;
    lines.push(`<g transform="translate(${c.x} ${c.y})">${nodeShape(n).outerHTML}<text x="0" y="5" text-anchor="middle" fill="${nodeColor(n)}" font-family="sans-serif" font-size="16" font-weight="bold">${escapeXml(nodeSymbol(n))}</text></g>`);
    lines.push(`<text x="${c.x}" y="${n.y+(labelAbove(n)?-22:91)}" text-anchor="middle" font-family="sans-serif" font-size="13" font-weight="bold" fill="#f1f5fb">${escapeXml(n.name)}</text>`);
    lines.push(`<text x="${c.x}" y="${n.y+(labelAbove(n)?-2:111)}" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#aebed3">${escapeXml(sub)}</text>`);
  }
  lines.push('</svg>');saveFile(lines.join('\n'),fileStem()+'.svg','image/svg+xml');toast('Diagram SVG downloaded.');
}
async function undo(){if(!undoStack.length)return;const next=undoStack.pop();redoStack.push(clone(design));await commit(next,{record:false});}
async function redo(){if(!redoStack.length)return;const next=redoStack.pop();undoStack.push(clone(design));await commit(next,{record:false});}
function updatePythonStatus(){
  const status=$('pythonStatus');
  if(!status)return;
  status.textContent=codeDirty?'Unapplied Python edits':'Synced with Canvas';
  status.classList.toggle('dirty',codeDirty);
}
async function setView(view){
  if(view===activeView)return;
  await pending;
  // Never discard edited Python when returning to the canvas.
  // Invalid Python leaves the developer in Python view with the error visible.
  if(view==='canvas'&&codeDirty){
    const applied=await applyPython();
    if(applied!==true){$('codeEditor').focus();return;}
  }
  activeView=view;
  const python=view==='python';
  $('workspace').classList.toggle('python-mode',python);
  $('workspace').classList.toggle('canvas-mode',!python);
  for(const id of ['editorCanvas'])$(id).hidden=python;
  document.querySelector('.leftbar').hidden=python;
  document.querySelector('.rightbar').hidden=python;
  $('pythonWorkspace').hidden=!python;
  $('viewCanvas').classList.toggle('active',!python);
  $('viewPython').classList.toggle('active',python);
  $('viewCanvas').setAttribute('aria-selected',String(!python));
  $('viewPython').setAttribute('aria-selected',String(python));
  $('brainName').disabled=python;
  if(python&&!codeDirty)$('codeEditor').value=generatedCode;
  updatePythonStatus();
  if(python)$('codeEditor').focus();
  else $('viewport').focus({preventScroll:true});
}

// Device operations are invoked by the local Python SDK. Nothing is compiled,
// executed or uploaded by the browser itself; no device secret reaches the UI.
function updateDeviceControls(){
  const state=(deviceInfo.state||'DISCONNECTED').toUpperCase();
  const status=$('deviceStatus');
  status.textContent=deviceBusy?'Prima-Lux · Working…':`Prima-Lux · ${deviceInfo.connected?state.replace(/_/g,' '):'Not connected'}`;
  $('deviceIndicator').classList.toggle('connected',Boolean(deviceInfo.connected));
  const hasDesign=design && design.nodes.some(n=>n.kind==='input') && design.nodes.some(n=>n.kind==='output') && design.connections.length>0;
  $('deviceBuild').disabled=deviceBusy||!hasDesign||state==='RUNNING';
  $('deviceRun').disabled=deviceBusy||!deviceInfo.ready_for_run;
  $('deviceStop').disabled=deviceBusy||state!=='RUNNING';
  $('deviceRefresh').disabled=deviceBusy;
}
async function refreshDevice(){
  if(deviceBusy)return;
  try{deviceInfo=await api('/api/device/status');}
  catch(e){deviceInfo={connected:false,state:'DISCONNECTED',ready_for_run:false};}
  updateDeviceControls();
}
async function deviceAction(action){
  if(deviceBusy)return;
  // The full-width Python editor must apply its current Brain design first.
  if(codeDirty){const applied=await applyPython();if(applied!==true)return;}
  await pending;
  deviceBusy=true;
  deviceInfo.ready_for_run=false;
  updateDeviceControls();
  const label={build:'Building this brain on Prima-Lux',run:'Starting brain',stop:'Stopping and saving brain'}[action];
  $('deviceStatus').textContent=label+'…';
  try{
    const result=await api('/api/device/'+action,{});
    if(action==='build')toast('Prima-Lux reported READY.');
    else if(action==='run')toast('Prima-Lux confirmed RUNNING. Your application supplies live inputs.');
    else toast('Prima-Lux confirmed STOPPED and saved.');
  }catch(e){toast(e.message,true);}
  finally{deviceBusy=false;await refreshDevice();}
}

function wireEvents(){
  $('addInput').onclick=()=>add('input');$('addOpu').onclick=()=>add('opu');$('addOutput').onclick=()=>add('output');
  $('emptyAddBtn').onclick=()=>add('opu');$('starterBtn').onclick=starterBrain;
  $('connectBtn').onclick=()=>toggleConnectMode();$('arrangeBtn').onclick=arrange;$('centerBtn').onclick=centerView;
  $('zoomOut').onclick=()=>setZoom(zoom-.1);$('zoomIn').onclick=()=>setZoom(zoom+.1);
  $('undoBtn').onclick=undo;$('redoBtn').onclick=redo;
  $('viewCanvas').onclick=()=>setView('canvas');$('viewPython').onclick=()=>setView('python');
  $('deviceRefresh').onclick=refreshDevice;
  $('deviceBuild').onclick=()=>deviceAction('build');
  $('deviceRun').onclick=()=>deviceAction('run');
  $('deviceStop').onclick=()=>deviceAction('stop');
  $('applyCode').onclick=applyPython;
  $('revertCode').onclick=()=>{$('codeEditor').value=generatedCode;codeDirty=false;$('codeError').hidden=true;updatePythonStatus();toast('Unapplied code edits cleared.');};
  $('codeEditor').addEventListener('input',()=>{codeDirty=true;updatePythonStatus();});
  $('brainName').addEventListener('change',()=>{if(activeView!=='canvas')return;const d=clone(design);d.name=$('brainName').value;commit(d);});
  $('saveBtn').onclick=exportCurrentDesign;$('downloadPython').onclick=exportPython;$('exportSvg').onclick=exportSVG;
  $('newBtn').onclick=()=>{if((!designDirty&&!codeDirty)||confirm('Start a new brain? Download your current design and Python edits first if you need them.'))newBrain();};
  $('openBtn').onclick=()=>{if((designDirty||codeDirty)&&!confirm('Open another brain? Save your current design and Python edits first if you need them.'))return;$('openFile').click();};
  $('openFile').addEventListener('change',async event=>{
    const file=event.target.files?.[0];event.target.value='';if(!file)return;
    if(file.size>1024*1024){toast('Design file is too large.',true);return;}
    try{const next=JSON.parse(await file.text());selection=null;const ok=await commit(next);if(ok){designDirty=false;$('draftStatus').textContent='Opened from file';centerView();toast('Brain design opened.');}}catch(e){toast(`Cannot open design: ${e.message}`,true);}
  });
  const viewport=$('viewport');
  viewport.addEventListener('pointerdown',event=>{
    if(event.button!==0 || connectMode || event.target.closest('.node,.edge-hit,button,input,select,textarea'))return;
    event.preventDefault();
    selection=null;renderInspector();renderNodes();renderEdges();
    panning={id:event.pointerId,x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop,moved:false};
    viewport.setPointerCapture(event.pointerId);viewport.classList.add('panning');
  });
  viewport.addEventListener('pointermove',event=>{
    if(!panning || event.pointerId!==panning.id)return;
    const dx=event.clientX-panning.x,dy=event.clientY-panning.y;
    if(Math.abs(dx)+Math.abs(dy)>3)panning.moved=true;
    viewport.scrollLeft=panning.left-dx;viewport.scrollTop=panning.top-dy;
  });
  function endPan(event){
    if(!panning || event.pointerId!==panning.id)return;
    ignoreBackgroundClick=panning.moved;panning=null;viewport.classList.remove('panning');
    if(viewport.hasPointerCapture(event.pointerId))viewport.releasePointerCapture(event.pointerId);
    setTimeout(()=>{ignoreBackgroundClick=false;},0);
  }
  viewport.addEventListener('pointerup',endPan);
  viewport.addEventListener('pointercancel',endPan);
  viewport.addEventListener('lostpointercapture',endPan);
  $('viewport').addEventListener('click',event=>{
    if(ignoreBackgroundClick)return;
    if(event.target===$('viewport') || event.target===$('stage') || event.target===$('edgeLayer') || event.target===$('nodesLayer')){
      if(connectMode){connectStart=null;updateConnectStatus();renderNodes();}else{selection=null;render();}
    }
  });
  document.addEventListener('keydown',ev=>{
    const target=ev.target;const inField=target?.closest('input,textarea,select');
    if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='s'){ev.preventDefault();exportCurrentDesign();return;}
    if((ev.ctrlKey||ev.metaKey)&&ev.key==='Enter'&&target===$('codeEditor')){ev.preventDefault();applyPython();return;}
    if(!inField && (ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='z'){
      ev.preventDefault();ev.shiftKey?redo():undo();return;
    }
    if(!inField&&(ev.key==='Delete'||ev.key==='Backspace')&&selection){
      ev.preventDefault();const d=clone(design);
      if(selection.kind==='node'){d.nodes=d.nodes.filter(n=>n.name!==selection.name);d.connections=d.connections.filter(e=>e.from!==selection.name&&e.to!==selection.name);}
      else d.connections=d.connections.filter(e=>!(e.from===selection.from&&e.to===selection.to));
      selection=null;commit(d);return;
    }
    if(ev.key==='Escape' && (connectMode||connectStart)){toggleConnectMode(false);}
  });
  window.addEventListener('beforeunload',(ev)=>{
    if(codeDirty||designDirty){ev.preventDefault();ev.returnValue='';}
  });
}
async function start(){
  wireEvents();setZoom(1);
  try{const response=await api('/api/design');syncState(response);setTimeout(()=>{$('viewport').scrollLeft=200;$('viewport').scrollTop=170;},60);await refreshDevice();}
  catch(e){$('draftStatus').textContent='Unavailable';toast(e.message,true);}
}
start();
