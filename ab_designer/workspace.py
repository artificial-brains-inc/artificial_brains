"""One persistent, loopback SDK workspace per project. No device secrets on disk."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import threading
import webbrowser
import httpx
from .model import Brain


def _directory(project, env_path):
    identity=str(project)+'\n'+str(env_path)
    base=Path(os.getenv('AB_WORKSPACE_HOME',str(Path.home()/'.config/artificialbrains/workspaces')))
    root=base/hashlib.sha256(identity.encode()).hexdigest()[:32]
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    return root


def _existing(root):
    try:
        data=json.loads((root/'workspace.json').read_text())
        # The registry is private. Still, never send credentials to an arbitrary URL.
        from urllib.parse import urlsplit
        url=urlsplit(data['url'])
        if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.path!='/':return None
        with httpx.Client(trust_env=False,timeout=1) as client:
            result=client.get(data['url']+'api/workspace/ping',headers={'X-AB-Workspace':data['token']})
            if result.status_code==200 and result.json().get('workspace')==1:
                data['version']=result.json().get('version')
                if not data['version']:
                    health=client.get(data['url']+'api/health')
                    data['version']=health.json().get('version') if health.status_code==200 else None
                return data
    except (OSError,ValueError,KeyError,httpx.HTTPError):pass
    return None


def open_workspace(brain=None, *, project=None, env_path=None, client=None,
                   open_browser=True, explicit=False, port=0):
    project=Path(project or os.getenv('AB_UNIFIED_PROJECT') or Path.cwd()).expanduser().resolve()
    env_path=Path(env_path or os.getenv('AB_ENV') or project/'.env').expanduser().resolve()
    root=_directory(project,env_path)
    config=None
    if client is not None:config={'url':client.url,'api_key':client.api_key}
    else:
        try:
            from ab_sdk.primalux import PrimaLuxConfig
            cfg=PrimaLuxConfig.from_env(env_path)
            config={'url':cfg.url,'api_key':cfg.api_key}
        except ValueError:pass  # Offline design is supported.
    data=_existing(root);created=False
    from . import __version__
    if data and data.get('version')!=__version__:
        # 0.4.4 owned no controller process. Retire its UI to load the new routes.
        # Never kill an unknown daemon or interrupt an external controller.
        if data.get('version')!='0.4.4':
            raise RuntimeError('Close the previous SDK workspace, then run ab design again to load SDK '+__version__+'.')
        with httpx.Client(trust_env=False,timeout=5) as local:
            local.post(data['url']+'api/workspace/close',json={}).raise_for_status()
        until=time.monotonic()+5
        while time.monotonic()<until and _existing(root):time.sleep(.1)
        if _existing(root):raise RuntimeError('The previous workspace is still closing. Run ab design again.')
        data=None
    lock=root/'launch.lock';deadline=time.monotonic()+30
    while data is None:
        try:
            fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        except FileExistsError:
            try:
                if time.time()-lock.stat().st_mtime>60:lock.unlink();continue
            except FileNotFoundError:continue
            if time.monotonic()>=deadline:raise RuntimeError('Workspace startup is still pending. Try opening the designer again.')
            time.sleep(.15);data=_existing(root);continue
        try:
            data=_existing(root)
            if data is not None:break
            token=secrets.token_urlsafe(32)
            initial=brain.to_dict() if isinstance(brain,Brain) else brain
            settings={'token':token,'design':initial,'project':str(project),'env_path':str(env_path),
                      'device':config,'port':port}
            log=open(root/'workspace.log','ab');os.chmod(root/'workspace.log',0o600)
            options={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS}
            environment=os.environ.copy()
            environment['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)+os.pathsep+environment.get('PYTHONPATH','')
            try:
                proc=subprocess.Popen([sys.executable,'-m','ab_designer.workspace','--serve',str(root)],
                    stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,cwd=project,env=environment,**options)
                proc.stdin.write(json.dumps(settings));proc.stdin.close()
                threading.Thread(target=proc.wait,daemon=True).start()
            finally:log.close()
            while time.monotonic()<deadline:
                data=_existing(root)
                if data is not None:created=True;break
                if proc.poll() is not None:raise RuntimeError('Workspace could not start. See '+str(root/'workspace.log'))
                time.sleep(.15)
            if data is None:raise RuntimeError('Workspace startup did not finish. See '+str(root/'workspace.log'))
        finally:lock.unlink(missing_ok=True)
    # An application run attaches its design for telemetry without replacing the
    # editable draft. It refreshes credentials in memory after key rotation.
    attachment={'device':config,'design':brain.to_dict() if isinstance(brain,Brain) else brain}
    with httpx.Client(trust_env=False,timeout=5) as local:
        response=local.post(data['url']+'api/workspace/attach',json=attachment,
                            headers={'X-AB-Workspace':data['token']})
        response.raise_for_status()
    print('[AB] Workspace: '+data['url'],flush=True)
    if open_browser and (created or explicit):
        try:webbrowser.open(data['url']+('#telemetry' if client is not None and created else ''))
        except Exception:pass
    return data['url']


def run(root):
    from .server import DesignerServer
    settings=json.load(sys.stdin)
    data=settings.get('design')
    draft=root/'draft.json'
    if draft.is_file():
        try:data=json.loads(draft.read_text())
        except (OSError,ValueError):pass
    brain=Brain.from_dict(data) if data else Brain('Untitled Brain')
    server=DesignerServer(settings.get('port',0),brain,env_path=settings['env_path'],project=settings['project'])
    server.workspace_root=root;server.workspace_token=settings['token'];server.device_config=settings.get('device')
    server.application.token=settings['token']
    server.attached_design=settings.get('design')
    registry={'url':f'http://127.0.0.1:{server.server_port}/','token':settings['token'],'pid':os.getpid()}
    temporary=root/'workspace.tmp'
    with open(temporary,'w') as stream:json.dump(registry,stream)
    temporary.chmod(0o600);temporary.replace(root/'workspace.json')
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:
        server.server_close()
        try:
            if json.loads((root/'workspace.json').read_text()).get('token')==settings['token']:
                (root/'workspace.json').unlink()
        except (OSError,ValueError):pass


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--serve':raise SystemExit('Use ab design to open the workspace.')
    run(Path(sys.argv[2]))
