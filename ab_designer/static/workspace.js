'use strict';
let tuningState=null, primitiveTemplate=null, primitiveItems=[], applicationInfo=null, workspaceClosed=false, tuningError='';
let popupName=null;
function option(value,label){const o=document.createElement('option');o.value=value;o.textContent=label;return o;}
const tuner=new LatestTNorm({
 prepare:async()=>{await pending;}, send:(values,state)=>api('/api/device/tuning',{values,state}),
 refresh:()=>api('/api/device/tuning'),
 applied:state=>{tuningError='';tuningState=state;applyTuningToDraft(state);},
 failed:error=>{tuningError=error.message;toast('TNorm was not confirmed: '+error.message,true);},
 changed:()=>updateTNormPopup()
});
function applyTuningToDraft(state){
 if(!design||!state?.matches_design)return;
 const values=new Map((state.opus||[]).map(n=>[n.name,n.tnorm]));
 for(const node of design.nodes)if(node.kind==='opu'&&values.has(node.name)){
   const v=values.get(node.name);if('tnorm' in node||v!==10)node.tnorm=v;
 }
 if(state.code){generatedCode=state.code;if(!codeDirty)$('codeEditor').value=generatedCode;}
 designDirty=true;$('draftStatus').textContent='TNorm applied · save to export';renderNodes();
}
function receiveTuning(state){if(!state)return;tuner.adopt(state);tuningState=tuner.state;updateTNormPopup();}
async function refreshTuning(){try{receiveTuning(await api('/api/device/tuning'));}catch(error){tuningError=error.message;updateTNormPopup();}}
function openTNorm(name){
 if(deviceInfo.state!=='RUNNING'||getNode(name)?.kind!=='opu')return;
 popupName=name;tuningError='';$('tnormPopup').hidden=false;$('tnormTitle').textContent=name;
 positionTNorm();updateTNormPopup();refreshTuning();
}
function closeTNorm(){popupName=null;$('tnormPopup').hidden=true;}
function positionTNorm(){
 if(!popupName)return;
 const node=[...$('nodesLayer').children].find(n=>n.dataset.name===popupName),r=node?.getBoundingClientRect();
 const popup=$('tnormPopup');const w=popup.offsetWidth||320,h=popup.offsetHeight||220;
 let x=r?r.left+r.width/2-w/2:innerWidth/2-w/2,y=r?r.bottom+8:innerHeight/2-h/2;
 if(y+h>innerHeight-12)y=(r?.top||innerHeight)-h-12;
 popup.style.left=Math.max(12,Math.min(innerWidth-w-12,x))+'px';popup.style.top=Math.max(12,Math.min(innerHeight-h-12,y))+'px';
}
function updateTNormPopup(){
 if(!popupName)return;
 const state=tuner.state,current=state?.opus?.find(n=>n.name===popupName);
 const enabled=deviceInfo.state==='RUNNING'&&!deviceBusy&&state?.available&&state.matches_design&&current;
 $('tnormSlider').disabled=$('tnormNumber').disabled=!enabled;
 const value=tuner.desired(popupName)??getNode(popupName)?.tnorm??10;
 if(document.activeElement!==$('tnormNumber'))$('tnormNumber').value=value;
 $('tnormSlider').value=value;
 let message=tuningError;
 if(!message)message=!enabled?(state?.message||'Reading live TNorm from Prima-Lux…'):
   (tuner.flight||Object.keys(tuner.pending).length)?'Sending the latest value…':
   state.effective_from_step!=null?`Applied from timestep ${state.effective_from_step}. Dynamics retained.`:'Changes apply automatically. Dynamics are retained.';
 $('tnormMessage').textContent=message;$('tnormMessage').classList.toggle('error',Boolean(tuningError));
 positionTNorm();
}
function editTNorm(value){try{tuningError='';tuner.queue(popupName,Number(value));}catch(error){tuningError=error.message;updateTNormPopup();}}

let primitiveRequest=0;
async function primitiveChanged(){
 const serial=++primitiveRequest,kind=$('primitiveKind').value,item=primitiveItems.find(x=>x.kind===kind);
 $('primitiveDescription').textContent=primitiveTemplate?'Your saved circuit, including its internal connections and learning rules.':item?.description||'';
 $('primitiveWidthLabel').hidden=kind!=='sensor'||Boolean(primitiveTemplate);
 $('decisionOptions').hidden=kind!=='decision'||Boolean(primitiveTemplate);
 $('fuzzyOptions').hidden=!kind.startsWith('fuzzy-')||Boolean(primitiveTemplate);
 const old=Object.fromEntries([...$('primitiveSources').querySelectorAll('select')].map(x=>[x.dataset.port,x.value]));
 try{
 const value=primitiveTemplate||await api('/api/primitives/template?'+new URLSearchParams({kind,inputs:$('primitiveInputs').value,contradictions:$('primitiveOpposition').value,width:$('primitiveBank').value}));
 if(serial!==primitiveRequest)return;
 $('primitiveSources').replaceChildren();
 for(const port of value.input_ports||Object.keys(value.ports||{})){
   const label=document.createElement('label');label.textContent=port;const select=document.createElement('select');select.dataset.port=port;
   select.append(option('','Connect later'));for(const n of design.nodes)select.append(option(n.name,n.name));
   select.value=old[port]||'';label.append(select);$('primitiveSources').append(label);
 }
 $('primitiveError').textContent='';
 }catch(error){if(serial===primitiveRequest)$('primitiveError').textContent=error.message;}
}
async function showPrimitive(template=null){
 primitiveTemplate=template;
 if(!primitiveItems.length)primitiveItems=(await api('/api/primitives')).items;
 $('primitiveKind').replaceChildren();for(const item of template?[{kind:'custom',title:template.name}]:primitiveItems)$('primitiveKind').append(option(item.kind,item.title));
 if(!template)$('primitiveKind').value='motor';
 $('primitiveName').value=nextName(template?'CIRCUIT':'MOTOR');$('primitiveSources').replaceChildren();
 $('primitiveError').textContent='';await primitiveChanged();$('primitiveDialog').showModal();
}
const PRIMITIVE_STYLE={sensor:['#75cfc7','sensor'],reflex:['#f2c46c','reflex'],motor:['#f29a77','motor'],decision:['#c5a3ed','decision'],boolean:['#7bbaf4','boolean'],fuzzy:['#b8d983','fuzzy'],custom:['#9badc0','custom']};
function primitiveBadge(family){
 const svg=makeSvg('svg',{viewBox:'0 0 30 30',class:'primitive-badge','aria-hidden':'true'});
 const attrs={fill:'none',stroke:'currentColor','stroke-width':1.7};
 const paths={sensor:'M15 4 A11 11 0 1 1 14.99 4 M15 10 A5 5 0 1 1 14.99 10',reflex:'M17 2 L6 17 H13 L11 28 L25 11 H17 Z',motor:'M5 5 H17 L26 15 L17 25 H5 Z M10 10 L15 15 L10 20',decision:'M15 2 L28 15 L15 28 L2 15 Z M15 9 V15 L10 20 M15 15 L20 20',boolean:'M4 4 H15 C30 4 30 26 15 26 H4 Z M0 9 H4 M0 21 H4 M26 15 H30',fuzzy:'M3 5 H27 V10 H3 Z M3 13 H22 V18 H3 Z M3 21 H17 V26 H3 Z',custom:'M5 5 H25 V25 H5 Z'};
 svg.append(makeSvg('path',{...attrs,d:paths[family]}));return svg;
}
function renderGroups(){
 const layer=$('groupsLayer');layer.replaceChildren();
 for(const g of design.groups||[]){const nodes=g.members.map(getNode).filter(Boolean);if(!nodes.length)continue;
 const family=g.kind.startsWith('boolean-')?'boolean':g.kind.startsWith('fuzzy-')?'fuzzy':(PRIMITIVE_STYLE[g.kind]?g.kind:'custom');
 const [color]=PRIMITIVE_STYLE[family],x=Math.min(...nodes.map(n=>n.x))-24,y=Math.min(...nodes.map(n=>n.y))-58;
 const box=document.createElement('div');box.className='primitive-group';box.dataset.family=family;box.style.setProperty('--primitive-color',color);
 box.style.left=x+'px';box.style.top=y+'px';box.style.width=(Math.max(...nodes.map(n=>n.x))+NODE_W+24-x)+'px';box.style.height=(Math.max(...nodes.map(n=>n.y))+NODE_H+20-y)+'px';
 const title=document.createElement('button');title.className='primitive-group-header';title.append(primitiveBadge(family),document.createTextNode(g.name));const type=document.createElement('small');type.textContent=g.kind.replaceAll('-',' ').toUpperCase();title.append(type);title.title=Object.entries(g.ports).map(([k,v])=>k+': '+v).join('\n');box.append(title);layer.append(box);
 let drag=null;title.onpointerdown=e=>{if(e.button!==0)return;e.stopPropagation();title.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,original:clone(design),moved:false};};
 title.onpointermove=e=>{if(!drag)return;e.stopPropagation();let dx=(e.clientX-drag.x)/zoom,dy=(e.clientY-drag.y)/zoom;const members=drag.original.nodes.filter(n=>g.members.includes(n.name));dx=Math.max(dx,24-Math.min(...members.map(n=>n.x)));dy=Math.max(dy,80-Math.min(...members.map(n=>n.y)));if(Math.abs(dx)+Math.abs(dy)>3)drag.moved=true;for(const n of members){const target=getNode(n.name);target.x=Math.round(n.x+dx);target.y=Math.round(n.y+dy);}box.style.left=(x+dx)+'px';box.style.top=(y+dy)+'px';renderNodes();renderEdges();};
 title.onpointerup=e=>{e.stopPropagation();if(!drag)return;const old=drag;drag=null;if(old.moved)commit(clone(design),{undoSnapshot:old.original});else{selection={kind:'node',name:g.ports.drive||g.ports.signal||g.ports.decision||g.ports.RESULT||g.members[0]};render();}};
 title.onpointercancel=()=>{if(drag){design=drag.original;drag=null;render();}};
 }
}
function growStage(){
 STAGE_W=Math.max(2600,...design.nodes.map(n=>n.x+NODE_W+200));STAGE_H=Math.max(1800,...design.nodes.map(n=>n.y+NODE_H+200));
 $('stage').style.width=STAGE_W+'px';$('stage').style.height=STAGE_H+'px';$('edgeLayer').setAttribute('viewBox',`0 0 ${STAGE_W} ${STAGE_H}`);
 $('stageShell').style.width=(STAGE_W*zoom)+'px';$('stageShell').style.height=(STAGE_H*zoom)+'px';
}
async function refreshApplication(){
 try{applicationInfo=await api('/api/application/status');renderApplication();}catch(error){$('applicationStatus').textContent=error.message;}
}
let renderedButtons='';
function renderApplication(){
 const a=applicationInfo;if(!a)return;const panel=a.panel||{},buttons=panel.buttons||[],values=panel.values||{};
 $('applicationTitle').textContent=panel.title||'Your application';
 $('applicationStatus').textContent=a.error|| (a.running?'Controller running':a.configured?(a.exit_code!=null?'Controller stopped · exit '+a.exit_code:'Ready · press Run to start '+a.config.entry):'Choose an entry point to launch your controller with Run.');
 $('applicationEmpty').hidden=Boolean(a.configured||buttons.length||panel.url);
 $('applicationPanel').hidden=!buttons.length&&!Object.keys(values).length;
 const identity=JSON.stringify([a.session,buttons]);
 if(identity!==renderedButtons){renderedButtons=identity;$('applicationButtons').replaceChildren();
   for(const item of buttons){const b=document.createElement('button');b.className='button-outline';b.textContent=item.label;b.onclick=async()=>{b.disabled=true;try{await api('/api/application/action',{action:item.id,session:a.session});}catch(error){toast(error.message,true);}finally{b.disabled=false;}};$('applicationButtons').append(b);}
 }
 $('applicationValues').replaceChildren();for(const [name,value] of Object.entries(values)){const card=document.createElement('div');card.className='application-value';const l=document.createElement('small');l.textContent=name;const v=document.createElement('strong');v.textContent=typeof value==='object'?JSON.stringify(value):String(value);card.append(l,v);$('applicationValues').append(card);}
 $('applicationWeb').hidden=!panel.url;
 if(panel.url){if($('applicationFrame').getAttribute('src')!==panel.url)$('applicationFrame').src=panel.url;$('applicationLink').href=panel.url;}
 else $('applicationFrame').removeAttribute('src');
 const logs=(a.logs||[]).join('\n');if($('applicationLog').textContent!==logs){$('applicationLog').textContent=logs||'No controller output yet.';$('applicationLog').scrollTop=$('applicationLog').scrollHeight;}
}
async function showApplicationSettings(){
 await refreshApplication();const c=applicationInfo?.config||{};
 for(const [id,key] of [['applicationEntry','entry'],['applicationUI','ui'],['applicationPython','python']])$(id).value=c[key]||'';
 $('applicationArgs').value=c.arguments||'';$('applicationError').textContent='';$('applicationDialog').showModal();
}
function wireWorkspace(){
 $('viewTelemetry').onclick=()=>setView('telemetry');$('viewApplication').onclick=()=>setView('application');
 $('tnormClose').onclick=closeTNorm;$('tnormSlider').oninput=e=>editTNorm(e.target.value);
 $('tnormNumber').onchange=e=>{if(!e.target.checkValidity()){tuningError='TNorm must be 0–10 with up to two decimals.';updateTNormPopup();return;}editTNorm(e.target.value);};
 document.addEventListener('keydown',e=>{if(e.key==='Escape')closeTNorm();});window.addEventListener('resize',positionTNorm);$('viewport').addEventListener('scroll',positionTNorm);
 $('addPrimitive').onclick=()=>showPrimitive().catch(e=>toast(e.message,true));$('cancelPrimitive').onclick=()=>$('primitiveDialog').close();
 $('primitiveKind').onchange=()=>{$('primitiveName').value=nextName($('primitiveKind').value.replaceAll('-','_').toUpperCase());primitiveChanged();};
 for(const id of ['primitiveInputs','primitiveOpposition','primitiveBank'])$(id).onchange=primitiveChanged;
 $('primitiveForm').onsubmit=async e=>{e.preventDefault();await pending;const snapshot=clone(design);try{
   const sources=Object.fromEntries([...$('primitiveSources').querySelectorAll('select')].filter(x=>x.value).map(x=>[x.dataset.port,x.value]));
   const data=await api('/api/primitives/add',{kind:$('primitiveKind').value,name:$('primitiveName').value,neurons:Number($('primitiveWidth').value),inputs:Number($('primitiveInputs').value),contradictions:Number($('primitiveOpposition').value),width:Number($('primitiveBank').value),sources,definition:primitiveTemplate});
   undoStack.push(snapshot);redoStack=[];syncState(data);designDirty=true;$('primitiveDialog').close();centerView();toast('Primitive added. Its complete circuit is editable.');
 }catch(error){$('primitiveError').textContent=error.message;}};
 $('importPrimitive').onclick=()=>$('primitiveFile').click();$('primitiveFile').onchange=async e=>{try{const file=e.target.files[0];if(file)await showPrimitive(JSON.parse(await file.text()));}catch(error){toast('Could not read the primitive: '+error.message,true);}e.target.value='';};
 $('savePrimitive').onclick=()=>{if(!design.nodes.length){toast('Add a circuit first.');return;}$('extractMembers').replaceChildren();const group=(design.groups||[]).find(g=>g.members.includes(selection?.name));for(const n of design.nodes){const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.value=n.name;check.checked=group?group.members.includes(n.name):true;label.append(check,document.createTextNode(n.name));$('extractMembers').append(label);}$('extractError').textContent='';$('extractDialog').showModal();};
 $('cancelExtract').onclick=()=>$('extractDialog').close();$('extractForm').onsubmit=async e=>{e.preventDefault();try{const value=await api('/api/primitives/extract',{name:$('extractName').value,members:[...$('extractMembers').querySelectorAll('input:checked')].map(x=>x.value)});saveFile(JSON.stringify(value,null,2)+'\n','brain.abprimitive.json','application/json');$('extractDialog').close();}catch(error){$('extractError').textContent=error.message;}};
 $('deviceReset').onclick=async()=>{if(confirm('Reset accumulated neural dynamics and learned weights, and restore the compiled TNorm values?'))await deviceAction('reset');};
 $('applicationSettings').onclick=$('applicationSetup').onclick=showApplicationSettings;$('cancelApplication').onclick=()=>$('applicationDialog').close();
 $('applicationForm').onsubmit=async e=>{e.preventDefault();try{applicationInfo=await api('/api/application/configure',{entry:$('applicationEntry').value,arguments:$('applicationArgs').value,ui:$('applicationUI').value,python:$('applicationPython').value});renderApplication();$('applicationDialog').close();toast('Application settings saved in this project.');}catch(error){$('applicationError').textContent=error.message;}};
 $('closeWorkspace').onclick=async()=>{if(codeDirty&&!confirm('Unapplied Python edits will be lost. Close the workspace?'))return;await pending;await tuner.idle();try{await api('/api/workspace/close',{});workspaceClosed=true;codeDirty=false;designDirty=false;$('deviceStatus').textContent='Workspace closed';for(const id of ['deviceBuild','deviceRun','deviceStop'])$(id).disabled=true;toast('Workspace closed. Reopen it with ab design.');}catch(e){toast(e.message,true);}};
 setInterval(()=>{if(!document.hidden&&!workspaceClosed){refreshDevice();if(activeView==='application')refreshApplication();}},2000);
}
start();
