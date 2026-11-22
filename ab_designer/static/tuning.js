'use strict';
// One request at a time. Pending edits replace older unsent values for that OPU.
class LatestTNorm {
  constructor({send, refresh, prepare=async()=>{}, applied=()=>{}, failed=()=>{}, changed=()=>{}, delay=80}) {
    Object.assign(this,{send,refresh,prepare,applied,failed,changed,delay});
    this.state=null; this.pending={}; this.flight=null; this.timer=null; this.retry=0; this.waiters=[];
  }
  identity(s){return s ? `${s.package_id}/${s.session_id}` : null;}
  adopt(state){
    if(this.flight || Object.keys(this.pending).length)return;
    if(this.identity(state)===this.identity(this.state)&&state?.revision<this.state?.revision)return;
    this.state=state;
  }
  desired(name){return this.pending[name] ?? this.flight?.[name] ?? this.state?.opus?.find(n=>n.name===name)?.tnorm;}
  queue(name,value){
    if(!Number.isFinite(value)||value<0||value>10||Math.abs(value*100-Math.round(value*100))>1e-7)throw new Error('TNorm must be 0–10 with up to two decimals.');
    if(!this.state?.available||!this.state.matches_design)throw new Error(this.state?.message||'Live TNorm is not available.');
    this.pending[name]=value; this.changed(); this.schedule();
  }
  schedule(){if(!this.timer&&!this.flight)this.timer=setTimeout(()=>{this.timer=null;this.flush();},this.delay);}
  async flush(){
    if(this.flight || !Object.keys(this.pending).length)return;
    // Claim the request slot before awaiting any queued design save.
    this.flight={}; const identity=this.identity(this.state);
    try {
      await this.prepare();
      const values=this.pending;this.pending={};this.flight=values;
      const next=await this.send(values,this.state);
      if(this.identity(next)!==identity)throw new Error('The installed brain changed. Select its OPU again.');
      this.state=next;this.retry=0;this.applied(next);
    } catch(error){
      const values=this.flight;
      try {
        const fresh=await this.refresh();
        if(!fresh?.available||!fresh.matches_design||this.identity(fresh)!==identity||this.retry>=1)throw error;
        this.state=fresh;this.retry++;
        // A newer slider position always wins over the uncertain earlier request.
        this.pending={...values,...this.pending};
      } catch(refreshError){this.pending={};this.retry=0;this.failed(error);}
    } finally {
      this.flight=null;this.changed();
      if(Object.keys(this.pending).length)this.schedule();
      else {for(const resolve of this.waiters.splice(0))resolve();}
    }
  }
  idle(){return !this.flight&&!Object.keys(this.pending).length?Promise.resolve():new Promise(resolve=>this.waiters.push(resolve));}
}
if(typeof module!=='undefined')module.exports={LatestTNorm};
