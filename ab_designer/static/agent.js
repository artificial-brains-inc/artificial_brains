'use strict';
let agentInfo=null,agentRefreshPending=false,agentRenderedMessages='',agentRenderedFiles='',agentSource='draft',agentFileStart=0,agentLocalError='';
function agentFailure(error){agentLocalError=error.message||String(error);$('agentError').hidden=false;$('agentError').textContent=agentLocalError;}
async function refreshAgent(){
 if(agentRefreshPending)return;agentRefreshPending=true;
 try{
  const info=await api('/api/agent/status');agentInfo=info;
  const cfg=info.configuration;
  $('agentProvider').textContent=cfg.provider==='codex'?'Local Codex · '+(cfg.model||'CLI model'):(cfg.model?cfg.provider+' · '+cfg.model:'Add your API URL, key, and model to .env');
  const identity=JSON.stringify(info.messages);
  if(identity!==agentRenderedMessages){agentRenderedMessages=identity;
   if(info.messages.length){const nearBottom=$('agentMessages').scrollHeight-$('agentMessages').scrollTop-$('agentMessages').clientHeight<80;
    $('agentMessages').replaceChildren();for(const item of info.messages){
     const bubble=document.createElement('div');bubble.className='agent-message';bubble.dataset.role=item.role;
     const label=document.createElement('span');label.className='agent-message-label';label.textContent=item.role==='user'?'You'+(item.telemetry_attached?' · neuron activity attached':''):item.role==='tool'?'Project checks':'Brain agent';
     bubble.append(label,document.createTextNode(item.content));$('agentMessages').append(bubble);
    }if(nearBottom)$('agentMessages').scrollTop=$('agentMessages').scrollHeight;
   }
  }
  const files=JSON.stringify(info.files);
  if(files!==agentRenderedFiles){agentRenderedFiles=files;const previous=$('agentFiles').value;$('agentFiles').replaceChildren();
   for(const f of info.files)$('agentFiles').append(option(f.path,f.path));
   if(info.files.some(f=>f.path===previous))$('agentFiles').value=previous;
   if(info.files.length)await agentPreview(false);else $('agentFiles').append(option('','Files appear here'));
  }
  $('agentFileCount').textContent=info.files.length+' files';
  const names=info.files.map(f=>f.path),hasBrain=names.includes('brain.abdesign.json');
  $('agentSend').disabled=info.busy;$('agentStop').disabled=!info.busy;
  $('agentPhase').textContent=info.busy?(info.phase==='stopping'?'Stopping after the current operation…':info.events.at(-1)?.message||'Working…'):(info.phase==='error'?'Agent needs attention':info.phase==='stopped'?'Agent stopped':'Ready');
  $('agentEvents').textContent=info.events.map(e=>new Date(e.at*1000).toLocaleTimeString()+'  '+e.message).join('\n')||'No operations yet.';
  const error=info.error||agentLocalError;$('agentError').hidden=!error;if(error)$('agentError').textContent=error;
  $('agentChecks').disabled=info.busy||!names.includes('design_brain.py')||!names.includes('verify_design.py');
  $('agentApply').disabled=info.busy||!hasBrain||!info.base_design_sha256;
  $('agentDownload').disabled=info.busy||!info.checks?.passed;
  $('agentLocalPermission').hidden=cfg.execution!=='off';
  $('agentCheckStatus').textContent=info.checks?(info.checks.passed?'Generator and verifier passed. Device build and robot behavior have not been checked.':'Project checks failed. Review the results in chat and ask the agent to fix them.'):'Checks run the generator, reload the brain, and run the project verifier.';
  const capture=info.capture;
  $('agentCapture').textContent=capture.recording?'Stop recording':'Record neurons';
  $('agentCaptureStatus').textContent=(capture.recording?'Recording · ':'')+capture.frames+' frames retained'+(capture.discarded_frames?' · '+capture.discarded_frames+' older frames discarded':'')+(capture.gaps?' · '+capture.gaps+' gaps':'')+(capture.error?' · '+capture.error:'');
  $('agentAttachTelemetry').disabled=!capture.frames;
 }catch(error){agentFailure(error);}finally{agentRefreshPending=false;}
}
async function agentPreview(append){
 const name=$('agentFiles').value;if(!name)return;
 if(!append)agentFileStart=0;
 try{const file=await api('/api/agent/file?'+new URLSearchParams({path:name,source:agentSource,start:agentFileStart,count:500}));
  $('agentFileContent').textContent=(append?$('agentFileContent').textContent+'\n':'')+file.text;
  $('agentFileLabel').textContent=(agentSource==='draft'?'Draft · ':'Original · ')+name;
  $('agentOriginal').disabled=false;$('agentOriginal').textContent=agentSource==='draft'?'View original':'View draft';
  agentFileStart=file.start+500;$('agentMoreFile').hidden=!file.has_more;
 }catch(error){$('agentFileContent').textContent=error.message;$('agentMoreFile').hidden=true;$('agentOriginal').disabled=false;$('agentOriginal').textContent='View draft';}
}
$('viewAgent').onclick=()=>setView('agent');
$('agentSetup').onclick=()=>$('agentSettings').showModal();
$('agentSettingsClose').onclick=()=>$('agentSettings').close();
$('agentFiles').onchange=()=>{agentSource='draft';agentPreview(false);};
$('agentOriginal').onclick=()=>{agentSource=agentSource==='draft'?'project':'draft';agentPreview(false);};
$('agentMoreFile').onclick=()=>agentPreview(true);
$('agentForm').onsubmit=async event=>{event.preventDefault();const message=$('agentMessage').value.trim();if(!message)return;
 agentLocalError='';$('agentSend').disabled=true;try{await pending;await api('/api/agent/message',{message,attach_telemetry:$('agentAttachTelemetry').checked});$('agentMessage').value='';$('agentAttachTelemetry').checked=false;await refreshAgent();}catch(error){agentFailure(error);$('agentSend').disabled=false;}
};
$('agentMessage').onkeydown=event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){$('agentForm').requestSubmit();event.preventDefault();}};
$('agentStop').onclick=async()=>{agentLocalError='';try{await api('/api/agent/stop',{});await refreshAgent();}catch(error){agentFailure(error);}};
$('agentChecks').onclick=async()=>{
 agentLocalError='';
 if(agentInfo?.configuration.execution==='off'&&!$('agentAllowLocal').checked){agentFailure(new Error('Review the generated files and select permission to run their generator and verifier, or configure sandbox execution in .env.'));return;}
 try{await api('/api/agent/checks',{allow_local_execution:$('agentAllowLocal').checked});await refreshAgent();}catch(error){agentFailure(error);}
};
$('agentCapture').onclick=async()=>{agentLocalError='';try{await api('/api/agent/capture/'+(agentInfo?.capture.recording?'stop':'start'),{});await refreshAgent();}catch(error){agentFailure(error);}};
$('agentApply').onclick=async()=>{agentLocalError='';try{await pending;const before=clone(design);const result=await api('/api/agent/apply',{expected_design_sha256:agentInfo.base_design_sha256});undoStack.push(before);redoStack=[];syncState(result);designDirty=true;await setView('canvas');centerView();toast('Agent brain loaded into Canvas. Build installs it on Prima-Lux.');}catch(error){agentFailure(error);}};
$('agentDownload').onclick=async()=>{agentLocalError='';try{const response=await fetch('/api/agent/package');if(!response.ok)throw new Error((await response.json()).error);const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download='artificial-brain-project.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}catch(error){agentFailure(error);}};
setInterval(()=>{if(activeView==='agent')refreshAgent();},1000);
