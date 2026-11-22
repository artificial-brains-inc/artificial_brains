"""Reusable public circuits. These expand into the ordinary Brain design API."""
from copy import deepcopy
import re
from .model import Brain, DesignError

CATALOG = [
    {'kind':'sensor','title':'Sensor','description':'External input → high-gamma relay. Connect signal to your circuit.'},
    {'kind':'reflex','title':'Reflex / ACK','description':'High-gamma relay → bistable output → reset relay → inhibitory return to the output.'},
    {'kind':'motor','title':'Motor','description':'Theta 3 → Theta 2.01 → Beta 5 → output, with a Beta 3 self-reset pathway.'},
    {'kind':'decision','title':'Decision nucleus','description':'One or more supporting signals, with optional opposing signals, feed a Theta 3 candidate state. Connect the candidate to a motor pathway.'},
    {'kind':'boolean-or','title':'Boolean OR','description':'A + B into Theta T5, one operand unit each.'},
    {'kind':'boolean-and','title':'Boolean AND','description':'Delta T2 operands and a local Delta T2 OR helper: A + B − OR into a separate Delta T2 result.'},
    {'kind':'boolean-xor','title':'Boolean XOR','description':'A Theta T5 OR is relayed into Alpha T5: 2 OR − A − B into a separate Alpha T5 result.'},
    {'kind':'boolean-not','title':'Boolean NOT','description':'Active REF − A into Theta T5. Connect a positive reference as well as the operand.'},
    {'kind':'boolean-nor','title':'Boolean NOR','description':'Active REF − OR into Beta T5. Connect a positive reference.'},
    {'kind':'fuzzy-or','title':'Fuzzy OR','description':'Channel-by-channel Theta T5 OR of two ordered Boolean banks.'},
    {'kind':'fuzzy-and','title':'Fuzzy AND','description':'The complete Delta T2 AND circuit at each matching bank position.'},
    {'kind':'fuzzy-not','title':'Fuzzy NOT','description':'Reverse the channel order, then apply reference-based Theta T5 NOT at every position.'},
]


def _count(value, label, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DesignError(f'{label} must be an integer of at least {minimum}.')
    return value


def template(kind, *, neurons=1, inputs=1, contradictions=0, width=8):
    brain = Brain(kind.replace('-', ' ').title())
    incoming = []
    outgoing = []
    def op(name, rhythm, tnorm):
        brain.opu(name, rhythm, tnorm)
        return name
    def term(source, target, units=1, inhibitory=False):
        brain.connect(source, target, 'inh' if inhibitory else 'exc', role='operand', units=units)
    def logic(operation, suffix=''):
        rhythm, tn = {'or':('theta',5),'and':('delta',2),'xor':('alpha',5),
                      'not':('theta',5),'nor':('beta',5)}[operation]
        a = op('A'+suffix, rhythm, tn); incoming.append(a)
        b = None
        if operation != 'not':
            b = op('B'+suffix, rhythm, tn); incoming.append(b)
        if operation in ('not','nor'):
            ref = op('REF'+suffix, rhythm, tn); incoming.append(ref)
        result = op('RESULT'+suffix, rhythm, tn); outgoing.append(result)
        if operation == 'or': term(a,result); term(b,result)
        elif operation == 'not': term(ref,result); term(a,result,inhibitory=True)
        else:
            helper = op('OR'+suffix, 'theta' if operation=='xor' else rhythm, 5 if operation=='xor' else tn)
            term(a,helper); term(b,helper)
            if operation == 'and':
                term(a,result); term(b,result); term(helper,result,inhibitory=True)
            elif operation == 'xor':
                canonical=op('OR_CAN'+suffix,'alpha',5)
                brain.connect(helper,canonical)
                term(canonical,result,2); term(a,result,inhibitory=True); term(b,result,inhibitory=True)
            else: term(ref,result); term(helper,result,inhibitory=True)
    if kind == 'sensor':
        brain.input('INPUT', neurons).opu('RELAY','high-gamma',10).connect('INPUT','RELAY')
        ports = {'input':'INPUT','signal':'RELAY'}; outgoing=['signal']
    elif kind == 'reflex':
        brain.opu('RELAY','high-gamma',10).output('OUTPUT').opu('RESET','high-gamma',10)
        brain.connect('RELAY','OUTPUT').connect('OUTPUT','RESET').connect('RESET','OUTPUT','inh')
        ports = {'drive':'RELAY','output':'OUTPUT','reset':'RESET'}; incoming=['drive']; outgoing=['output']
    elif kind == 'motor':
        brain.opu('DRIVE','theta',3).opu('STATE','theta',2.01).opu('MOTOR','beta',5)
        brain.output('OUTPUT').opu('RESET','beta',3)
        brain.connect('DRIVE','STATE').connect('STATE','MOTOR').connect('MOTOR','OUTPUT')
        brain.connect('MOTOR','RESET').connect('RESET','STATE','inh',role='feedback')
        brain.connect('RESET','OUTPUT','inh')
        ports = {'drive':'DRIVE','state':'STATE','motor':'MOTOR','output':'OUTPUT','reset':'RESET'}
        incoming=['drive']; outgoing=['output']
    elif kind == 'decision':
        _count(inputs,'Supporting inputs'); _count(contradictions,'Opposing inputs',0)
        op('DECISION','theta',3)
        for i in range(inputs):
            name=op('SUPPORT_'+str(i+1),'theta',3); incoming.append(name); term(name,'DECISION')
        for i in range(contradictions):
            name=op('OPPOSITION_'+str(i+1),'theta',3); incoming.append(name); term(name,'DECISION',inhibitory=True)
        outgoing=['decision']; ports={n:n for n in incoming}; ports['decision']='DECISION'
    elif kind.startswith('boolean-') and kind[8:] in ('and','or','not','xor','nor'):
        logic(kind[8:]); ports={n:n for n in incoming+outgoing}
    elif kind in ('fuzzy-and','fuzzy-or','fuzzy-not'):
        _count(width,'Bank width')
        operation=kind[6:]
        if operation == 'not':
            op('REF','theta',5); incoming.append('REF')
            for i in range(1,width+1):
                incoming.append(op('A_'+str(i),'theta',5))
                outgoing.append(op('RESULT_'+str(i),'theta',5))
            for i in range(1,width+1):
                term('REF','RESULT_'+str(i))
                term('A_'+str(width+1-i),'RESULT_'+str(i),inhibitory=True)
        else:
            for i in range(1,width+1): logic(operation,'_'+str(i))
        ports={n:n for n in incoming+outgoing}
    else:
        raise DesignError('Unknown primitive. Choose a listed primitive or supply a saved template.')
    data=brain.to_dict()
    for i,n in enumerate(data['nodes']): n.update(x=80+(i%3)*280,y=80+(i//3)*190)
    def place(name,x,y):
        next(n for n in data['nodes'] if n['name']==name).update(x=x,y=y)
    if kind=='sensor':place('INPUT',80,100);place('RELAY',360,100)
    elif kind=='reflex':
        for n,x,y in [('RELAY',80,100),('OUTPUT',380,100),('RESET',380,320)]:place(n,x,y)
    elif kind=='motor':
        for n,x,y in [('DRIVE',80,100),('STATE',360,100),('MOTOR',640,100),('OUTPUT',920,100),('RESET',640,340)]:place(n,x,y)
    elif kind=='decision':
        for i,n in enumerate(incoming):place(n,80,100+i*190)
        place('DECISION',440,100+(len(incoming)-1)*95)
    elif kind=='fuzzy-not':
        place('REF',80,100)
        for i in range(1,width+1):place('A_'+str(i),360,100+(i-1)*190);place('RESULT_'+str(i),700,100+(i-1)*190)
    else:
        operation=kind.split('-',1)[1]
        for i in range(width if kind.startswith('fuzzy-') else 1):
            suffix='_'+str(i+1) if kind.startswith('fuzzy-') else '';y=100+i*530
            place('A'+suffix,80,y);place('RESULT'+suffix,700 if operation in ('and','nor') else 980 if operation=='xor' else 440,y+95)
            if operation!='not':place('B'+suffix,80,y+190)
            if operation in ('and','xor','nor'):place('OR'+suffix,380,y+340 if operation!='nor' else y+95)
            if operation=='xor':place('OR_CAN'+suffix,680,y+340)
            if operation=='not':place('REF'+suffix,80,y+190)
            if operation=='nor':place('REF'+suffix,380,y+340)
    return {'schema':'artificial-brains/primitive/v1','kind':kind,'name':brain.name,
            'nodes':data['nodes'],'connections':data['connections'],'ports':ports,
            'input_ports':incoming,'output_ports':outgoing}


def validate_template(value):
    if not isinstance(value,dict) or value.get('schema')!='artificial-brains/primitive/v1':
        raise DesignError('Expected an artificial-brains/primitive/v1 template.')
    brain=Brain.from_dict({'name':value.get('name','Custom'), 'nodes':value.get('nodes'),
                          'connections':value.get('connections')})
    names={n['name'] for n in brain.to_dict()['nodes']}
    ports=value.get('ports',{})
    if not names or not isinstance(ports,dict) or any(not isinstance(k,str) or not k or v not in names for k,v in ports.items()):
        raise DesignError('Primitive ports must name populations inside the template.')
    for key in ('input_ports','output_ports'):
        listed=value.get(key,[])
        if not isinstance(listed,list) or any(not isinstance(p,str) or p not in ports for p in listed) or len(set(listed))!=len(listed):
            raise DesignError('Primitive input and output ports must name distinct entries in ports.')
    return {**brain.to_dict(),'schema':'artificial-brains/primitive/v1','ports':deepcopy(ports),
            'kind':str(value.get('kind','custom')),
            'input_ports':list(value.get('input_ports',[])), 'output_ports':list(value.get('output_ports',[]))}


def add_primitive(brain, kind, name, *, neurons=1, source=None, definition=None,
                  inputs=1, contradictions=0, width=8, sources=None):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',name):
        raise DesignError('Primitive names use letters, numbers and underscores, beginning with a letter.')
    value=validate_template(definition if definition is not None else template(kind,neurons=neurons,inputs=inputs,contradictions=contradictions,width=width))
    data=brain.to_dict()
    mapping={n['name']:name+'_'+n['name'] for n in value['nodes']}
    dy=max((n['y'] for n in data['nodes']),default=-130)+210
    for node in value['nodes']:
        node=deepcopy(node);node.update(name=mapping[node['name']],y=node['y']+dy)
        data['nodes'].append(node)
    for edge in value['connections']:
        edge=deepcopy(edge);edge['from']=mapping[edge['from']];edge['to']=mapping[edge['to']]
        if 'reward' in edge:edge['reward']=mapping[edge['reward']]
        for reward in edge.get('rewards',[]):reward['source']=mapping[reward['source']]
        data['connections'].append(edge)
    ports={k:mapping[v] for k,v in value['ports'].items()}
    if sources is not None and not isinstance(sources,dict):raise DesignError('Sources must map input port names to populations.')
    bindings=dict(sources or {})
    if source is not None:
        key=next(iter(value.get('input_ports') or []),None) or ('drive' if 'drive' in ports else 'support')
        bindings.setdefault(key,source)
    for key,source_name in bindings.items():
        if key not in ports: raise DesignError(f'Unknown primitive input port: {key}.')
        if source_name not in {n['name'] for n in brain.to_dict()['nodes']}:
            raise DesignError(f'Unknown source population: {source_name}.')
        if value.get('input_ports') and key not in value['input_ports']:
            raise DesignError(f'{key} is not an incoming port.')
        data['connections'].append({'from':source_name,'to':ports[key],'type':'exc'})
    data.setdefault('groups',[]).append({'name':name,'kind':kind,'members':list(mapping.values()),'ports':ports})
    return Brain.from_dict(data)


def extract(brain, members, name='Custom primitive', ports=None):
    data=brain.to_dict();selected=set(members)
    if not selected or not selected <= {n['name'] for n in data['nodes']}:
        raise DesignError('Select populations belonging to this design.')
    nodes=[n for n in data['nodes'] if n['name'] in selected]
    left=min(n['x'] for n in nodes);top=min(n['y'] for n in nodes)
    for n in nodes:n['x']-=left;n['y']-=top
    edges=[e for e in data['connections'] if e['from'] in selected and e['to'] in selected]
    for e in edges:
        rewards=[r['source'] for r in e.get('rewards',[])] + ([e['reward']] if 'reward' in e else [])
        if any(r not in selected for r in rewards):
            raise DesignError('Include the selected connections’ reward populations in this primitive.')
    return validate_template({'schema':'artificial-brains/primitive/v1','name':name,'kind':'custom',
                              'nodes':nodes,'connections':edges,'ports':ports or {n['name']:n['name'] for n in nodes}})
