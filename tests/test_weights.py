import base64
from copy import deepcopy
import json
from pathlib import Path
import struct
from unittest.mock import patch
import pytest
from ab_sdk import weights
from ab_sdk.primalux import PrimaLuxClient,_DeviceSessionTransport

@pytest.fixture
def snapshot():
    return {'schema':weights.SCHEMA,'encoding':weights.ENCODING,'layout_sha256':'a'*64,
            'package_id':'b'*64,'next_step':40,
            'binding':{'package_id':'b'*64,'session_id':'c'*32,'revision':0,'tuning_revision':0},
            'layers':[{'id':'0','source':'A','target':'B','type':'inh','rule':'STDP3/STDP3_SLOW',
                       'shape':[2,3],'data_b64':base64.b64encode(struct.pack('<6f',0,.1,.2,.3,-.4,1e10)).decode()}]}

def test_float32_bit_exact_roundtrip_and_orientation(snapshot,tmp_path):
    decoded=weights.decoded(snapshot)
    assert len(decoded['layers'][0]['values'])==2 and len(decoded['layers'][0]['values'][0])==3
    assert decoded['layers'][0]['values'][1][1]<0
    assert weights.packed(decoded)==snapshot
    path=tmp_path/'learned.abweights.json';weights.write_file(path,snapshot)
    assert weights.read_file(path)==snapshot
    weights.write_file(path,snapshot);assert list(tmp_path.iterdir())==[path]

@pytest.mark.parametrize('bad',[float('nan'),float('inf'),1e39,True,'1'])
def test_invalid_numbers_rejected(snapshot,bad):
    data=weights.decoded(snapshot);data['layers'][0]['values'][0][0]=bad
    with pytest.raises(ValueError):weights.packed(data)

def test_shapes_encoding_and_ambiguous_matrix_rejected(snapshot):
    for change in ('shape','length','base64','both'):
        data=deepcopy(snapshot);layer=data['layers'][0]
        if change=='shape':layer['shape']=[True,6]
        elif change=='length':layer['shape']=[3,3]
        elif change=='base64':layer['data_b64']='!'+layer['data_b64'][1:]
        else:layer['values']=[[1,2,3],[4,5,6]]
        with pytest.raises(ValueError):weights.packed(data)

def test_sdk_reads_current_binding_before_import_and_preserves_provenance(snapshot,tmp_path):
    device=object.__new__(PrimaLuxClient);calls=[]
    metadata={**snapshot,'binding':{**snapshot['binding'],'session_id':'d'*32}}
    metadata['layers']=[{k:v for k,v in snapshot['layers'][0].items() if k!='data_b64'}]
    def request(method,path,**kw):
        calls.append((method,path,kw))
        if method=='POST':return {'ok':True,'persisted':False}
        return deepcopy(metadata if kw['params']['metadata'] else snapshot)
    device._request=request
    assert device.get_weights(source='A')['layers'][0]['values'][1][2]==1e10
    assert calls[-1][2]['params']['source']=='A'
    path=device.export_weights(tmp_path/'weights.abweights.json');assert weights.read_file(path)==snapshot
    assert device.load_weights(path)=={'ok':True,'persisted':False}
    data=calls[-1][2]['json'];assert data['binding']==metadata['binding']
    assert data['package_id']==snapshot['package_id']
    assert snapshot['binding']['session_id']=='c'*32
    count=len(calls);device.load_weights(snapshot,state=metadata);assert len(calls)==count+1
    with pytest.raises(ValueError,match='layout'):
        device.load_weights({**snapshot,'layout_sha256':'e'*64})
    assert calls[-1][0]=='GET'

def test_legacy_checkpoint_adapter_does_not_export_to_cloud():
    assert _DeviceSessionTransport.get_weights(None,compile_id='test')=={'weights':[]}

def test_cli_export_import_use_environment_and_only_print_summary(snapshot,tmp_path,capsys):
    from ab_sdk import primalux_cli
    class Device:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def export_weights(self,path,**selection):
            assert selection=={'source':'A','target':None};return weights.write_file(path,snapshot)
        def load_weights(self,path):
            assert weights.read_file(path)==snapshot;return {'ok':True,'persisted':False}
    path=str(tmp_path/'weights.abweights.json')
    with patch.object(PrimaLuxClient,'from_env',return_value=Device()) as create:
        assert primalux_cli.main(['--env','custom.env','weights','export',path,'--source','A'])==0
        create.assert_called_with('custom.env')
        assert primalux_cli.main(['weights','import',path])==0
    captured=capsys.readouterr();assert 'data_b64' not in captured.out and 'Use Stop' in captured.err
