"""Bounded, opt-in capture of the existing per-neuron stream."""
from __future__ import annotations
import base64
from collections import deque
from copy import deepcopy
import threading
import time

import httpx


class Capture:
    def __init__(self, device_factory, capacity=2000):
        self.device_factory=device_factory
        self.frames=deque(maxlen=capacity)
        self.lock=threading.Lock()
        self.stop_event=threading.Event()
        self.thread=None
        self.layout={}
        self.epoch=None
        self.sequence=None
        self.dropped=0
        self.gaps=0
        self.error=None
        self.status={}
        self.started=None

    def running(self):return bool(self.thread and self.thread.is_alive())

    def start(self):
        if self.running():return
        if self.device_factory is None:raise ValueError('Live capture is available from the SDK workspace.')
        with self.lock:
            self.frames.clear();self.layout={};self.epoch=None;self.sequence=None
            self.dropped=self.gaps=0;self.error=None;self.started=time.time()
        self.stop_event.clear()
        self.thread=threading.Thread(target=self._run,daemon=True,name='ab-agent-telemetry')
        self.thread.start()

    def stop(self):self.stop_event.set()

    def accept(self,event,data):
        with self.lock:
            if event=='hello':
                if self.epoch is not None and self.epoch!=data.get('epoch'):
                    self.frames.clear();self.gaps+=1
                self.epoch=data.get('epoch');self.layout=deepcopy(data.get('layout') or {})
                try:self.sequence=int(str(data.get('cursor','')).split(':')[-1])
                except ValueError:self.sequence=None
                if data.get('error'):self.error=str(data['error'])
            elif event=='frame':
                if data.get('epoch')!=self.epoch:
                    self.gaps+=1;self.error='Telemetry epoch changed. Start a new capture.';return
                sequence=data.get('sequence')
                if self.sequence is not None and sequence!=self.sequence+1:self.gaps+=1
                self.sequence=sequence
                telemetry=data.get('telemetry') or {}
                if telemetry.get('error'):self.error=str(telemetry['error']);return
                if not telemetry.get('bits'):return
                neurons=self.layout.get('neurons',[])
                inputs=self.layout.get('inputs',[])
                try:
                    bits=base64.b64decode(telemetry['bits'],validate=True)
                    ibits=base64.b64decode(telemetry.get('input_bits',''),validate=True)
                    if len(bits)!=(len(neurons)+7)//8 or len(ibits)!=(sum(x['n'] for x in inputs)+7)//8:
                        raise ValueError('Incomplete neuron frame')
                except (ValueError,TypeError,KeyError):
                    self.gaps+=1;self.error='Invalid neuron telemetry frame.';return
                if len(self.frames)==self.frames.maxlen:self.dropped+=1
                self.frames.append({'epoch':self.epoch,'sequence':sequence,
                    'step':(data.get('item') or {}).get('step'),
                    'received_at':time.time(),'bits':telemetry['bits'],
                    'input_bits':telemetry.get('input_bits','')})
            elif event in ('gap','reset','error'):
                self.gaps+=1;self.error=str(data.get('message') or event)

    def _run(self):
        import json
        try:
            with self.device_factory() as device:
                status=device.status()
                with self.lock:
                    self.status={'device_id':status.get('device_id'),
                        'engine_state':(status.get('engine') or {}).get('state'),
                        'active_design_sha256':((status.get('package') or {}).get('active') or {}).get('design_sha256')}
                with device.http.stream('GET','/api/brain/stream',params={'telemetry':1},
                    timeout=httpx.Timeout(10,read=5)) as response:
                    if response.status_code!=200:raise RuntimeError('Telemetry HTTP '+str(response.status_code))
                    event='message';lines=[]
                    for line in response.iter_lines():
                        if self.stop_event.is_set():break
                        if line.startswith('event:'):event=line[6:].strip()
                        elif line.startswith('data:'):lines.append(line[5:].lstrip())
                        elif not line:
                            if lines:self.accept(event,json.loads('\n'.join(lines)))
                            event='message';lines=[]
        except Exception as exc:
            if not self.stop_event.is_set():
                with self.lock:self.error='Telemetry capture ended: '+str(exc)[:250]

    def snapshot(self):
        with self.lock:
            return {'schema':'artificial-brains/agent-telemetry/v1','recorded_at':self.started,
                'layout':deepcopy(self.layout),'frames':list(deepcopy(self.frames)),
                'epoch':self.epoch,'discarded_frames':self.dropped,'gaps':self.gaps,
                'error':self.error,'device':dict(self.status)}

    def public(self):
        with self.lock:return {'recording':self.running(),'frames':len(self.frames),
            'capacity':self.frames.maxlen,'discarded_frames':self.dropped,'gaps':self.gaps,'error':self.error}


def summary(snapshot,population='',start=0,count=200):
    if not snapshot:return {'available':False,'message':'No telemetry attached to this conversation turn.'}
    frames=snapshot.get('frames',[]);layout=snapshot.get('layout') or {}
    neurons=layout.get('neurons',[])
    rows=[];output_names=set(layout.get('output_populations',[]))
    decoded=[base64.b64decode(f['bits']) for f in frames]
    for slot,uid in enumerate(neurons):
        pop=uid.rsplit(':',1)[0]
        if population and pop!=population:continue
        active=[f['step'] for f,bits in zip(frames,decoded) if bits[slot//8] & (1<<(slot%8))]
        rows.append({'neuron':uid,'kind':'bistable_output_state' if pop in output_names else 'neuron_firing',
            'active_frames':len(active),'observed_frames':len(frames),
            'first_active_step':active[0] if active else None,'last_active_step':active[-1] if active else None})
    return {'available':bool(frames),'epoch':snapshot.get('epoch'),'device':snapshot.get('device'),
        'frames':len(frames),'discarded_frames':snapshot.get('discarded_frames',0),
        'gaps':snapshot.get('gaps',0),'error':snapshot.get('error'),
        'first_step':frames[0]['step'] if frames else None,'last_step':frames[-1]['step'] if frames else None,
        'neurons':rows[int(start):int(start)+int(count)],'total_matching_neurons':len(rows),
        'note':'Counts describe recorded timesteps, not a measured firing rate. Output bits are latched states. Use telemetry_frames for timing.'}


def frame_slice(snapshot,start=0,count=30,population=''):
    if not snapshot:return {'available':False}
    neurons=(snapshot.get('layout') or {}).get('neurons',[])
    result=[]
    for frame in snapshot.get('frames',[])[int(start):int(start)+int(count)]:
        bits=base64.b64decode(frame['bits'])
        result.append({'sequence':frame['sequence'],'step':frame['step'],
            'active_neurons':[uid for slot,uid in enumerate(neurons)
                              if (not population or uid.rsplit(':',1)[0]==population)
                              and bits[slot//8] & (1<<(slot%8))],
            'input_bits':frame.get('input_bits','')})
    return {'available':True,'epoch':snapshot.get('epoch'),'frames':result,
            'input_layout':(snapshot.get('layout') or {}).get('inputs',[]),
            'total_frames':len(snapshot.get('frames',[])),'gaps':snapshot.get('gaps',0)}
