"""Project files, public validation, code checks, and reproducible ZIP exports."""
from __future__ import annotations
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile

from ab_designer import Brain
from ab_sdk.design_submit import design_sha256
from .providers import AgentCancelled

DENIED_DIRS = {'.git','.venv','.venv-release','venv','node_modules','__pycache__',
               '.ab-agent','dist','build','.ssh','.config'}
DENIED_SUFFIXES = {'.abp','.bit','.bin','.pem','.key','.token','.p12','.pfx','.db','.sqlite','.sqlite3'}
DENIED_NAMES = {'credentials.json','auth.json','secrets.json','credentials.yaml','secrets.yaml',
                'id_rsa','id_ed25519','id_ecdsa'}
TEXT_SUFFIXES = {'.py','.json','.jsonl','.md','.txt','.toml','.yaml','.yml','.ini','.cfg',
                 '.js','.cjs','.mjs','.css','.html','.svg','.xml','.csv','.urdf','.wbt','.proto','.sh'}
REQUIRED_FILES = ('design_brain.py','brain.abdesign.json','run.py','verify_design.py',
                  'requirements.txt','.env.example','README.md')


def safe_path(root, name, *, writing=False):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Use a project-relative POSIX filename.')
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ('..','.') for p in path.parts):
        raise ValueError('File must remain within this project.')
    if any(p in DENIED_DIRS or p.startswith('.env') and p != '.env.example' for p in path.parts):
        raise ValueError('Credentials and generated environments are not agent project files.')
    if any(p.startswith('.') and p not in ('.env.example','.gitignore') for p in path.parts):
        raise ValueError('Hidden configuration is not shared with the agent.')
    if path.suffix.lower() in DENIED_SUFFIXES or path.name.lower() in DENIED_NAMES:
        raise ValueError('Private packages, firmware, keys, and databases are not shared.')
    root = Path(root).resolve()
    candidate = root.joinpath(*path.parts)
    cursor = root
    for part in path.parts:
        cursor = cursor/part
        if cursor.is_symlink(): raise ValueError('Symlinks are not shared or modified by the agent.')
    if not candidate.resolve().is_relative_to(root): raise ValueError('File escapes the project.')
    return candidate


def file_list(root):
    result = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in DENIED_DIRS and not d.startswith('.') and not (Path(current)/d).is_symlink())
        for name in sorted(files):
            relative = (Path(current)/name).relative_to(root).as_posix()
            try: path = safe_path(root, relative)
            except ValueError: continue
            if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name in ('.env.example','.gitignore','LICENSE')):
                stat=path.stat()
                result.append({'path':relative,'bytes':stat.st_size,'updated_ns':stat.st_mtime_ns})
    return result


class Projects:
    def __init__(self, project, root, settings):
        if Path(root).is_symlink() or Path(root).parent.is_symlink():
            raise ValueError('Agent draft directory cannot be a symlink.')
        self.project, self.root, self.settings = Path(project).resolve(), Path(root).resolve(), settings
        self.root.mkdir(parents=True, exist_ok=True)
        self.reports = self.root.parent/'checks'
        self.report = None
        saved=self.reports/'latest.json'
        if self.reports.is_symlink() or saved.is_symlink():
            raise ValueError('Agent check reports cannot be symlinks.')
        if saved.is_file():
            try:
                report=json.loads(saved.read_text())
                if report.get('project_sha256')==self.fingerprint():self.report=report
            except (OSError,ValueError):pass

    def list_files(self, source='draft'):
        return file_list(self.project if source == 'project' else self.root)

    def read_file(self, path, source='draft', start=0, count=300):
        file = safe_path(self.project if source == 'project' else self.root, path)
        if file.suffix.lower() not in TEXT_SUFFIXES and file.name not in ('.env.example','.gitignore','LICENSE'):
            raise ValueError('Only public text project files can be read.')
        lines = file.read_text(encoding='utf-8').splitlines()
        start, count = int(start), int(count)
        if start < 0 or count < 1: raise ValueError('Invalid line range.')
        return {'path':path,'start':start,'total_lines':len(lines),
                'text':self.settings.redact('\n'.join(lines[start:start+count])),
                'has_more':start+count<len(lines)}

    def write_file(self, path, content):
        file = safe_path(self.root, path, writing=True)
        if file.suffix.lower() not in TEXT_SUFFIXES and file.name not in ('.env.example','.gitignore','LICENSE'):
            raise ValueError('Write text project files; credentials, compiled brains, and firmware are excluded.')
        if not isinstance(content,str): raise ValueError('File content must be text.')
        content = self.settings.redact(content)
        if path == '.env.example':
            for line in content.splitlines():
                if line.strip().startswith('#') or '=' not in line: continue
                key, value = line.split('=',1)
                if re.search(r'(KEY|TOKEN|PASSWORD|SECRET)', key, re.I) and value.strip().strip('"\''):
                    raise ValueError('.env.example must leave credential values empty.')
        file.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=file.parent, prefix='.agent-write-')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as out: out.write(content)
            os.replace(temporary,file)
        finally: Path(temporary).unlink(missing_ok=True)
        self.report = None
        return {'written':path,'bytes':file.stat().st_size}

    def fingerprint(self):
        digest=hashlib.sha256()
        for item in self.list_files():
            digest.update(item['path'].encode()); digest.update(b'\0')
            digest.update(safe_path(self.root,item['path']).read_bytes())
        return digest.hexdigest()

    def validate_design(self, path='brain.abdesign.json'):
        file=safe_path(self.root,path)
        brain=Brain.load(file)
        data=brain.to_dict()
        return {'valid':True,'design_sha256':design_sha256(brain),'warnings':brain.validate(),
                'populations':len(data['nodes']),'projections':len(data['connections']),
                'input_neurons':sum(n['neurons'] for n in data['nodes'] if n['kind']=='input'),
                'allocated_neurons':None,'dense_matrix_cells':None,'image_bytes':None,
                'capacity_note':'Private builder confirms allocated neurons and prepared image size at Build.'}

    def check_syntax(self):
        checked=[]
        for item in self.list_files():
            if item['path'].endswith('.py'):
                ast.parse(safe_path(self.root,item['path']).read_text(),filename=item['path'])
                checked.append(item['path'])
        return {'python_syntax':'passed','files':checked,'executed':False}

    def run_checks(self, cancelled, notify, *, allow_local=False):
        mode=self.settings.execution
        if mode=='off' and not allow_local:
            return {'executed':False,'needs_local_execution':True,
                    'message':'Review generated files, then use Run project checks, or configure AB_AGENT_EXECUTION=sandbox/local.'}
        if mode=='off':mode='local'
        for name in ('design_brain.py','verify_design.py'):
            if not safe_path(self.root,name).is_file():raise ValueError('Missing '+name)
        self.check_syntax()
        env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','WINDIR','LANG','LC_ALL')}
        sdk_root=Path(__file__).resolve().parents[2]
        env.update(PYTHONPATH=str(sdk_root),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',
                   AB_TELEMETRY='false',AB_AGENT_CHECK='1')
        logs=[]
        self.report=None
        with tempfile.TemporaryDirectory(prefix='ab-agent-check-') as temp:
            env.update(HOME=temp,USERPROFILE=temp,TMPDIR=temp,TEMP=temp,TMP=temp)
            for script in ('design_brain.py','verify_design.py'):
                command=[sys.executable,'-s','-B',str(self.root/script)]
                cwd=self.root
                if mode=='sandbox':
                    bwrap=shutil.which('bwrap')
                    if not bwrap:raise RuntimeError('Sandbox checks require bubblewrap on Linux. Use local checks explicitly on other systems.')
                    command=[bwrap,'--die-with-parent','--unshare-all','--new-session','--cap-drop','ALL']
                    for base in ('/usr','/bin','/lib','/lib64'):
                        if Path(base).exists():command+=['--ro-bind',base,base]
                    if not Path(sys.prefix).is_relative_to('/usr'):
                        command+=['--ro-bind',sys.prefix,sys.prefix]
                    command+=['--ro-bind',str(sdk_root),'/sdk','--bind',str(self.root),'/project',
                              '--tmpfs','/tmp','--proc','/proc','--dev','/dev','--chdir','/project',
                              '--setenv','HOME','/tmp','--setenv','PYTHONPATH','/sdk',
                              sys.executable,'-s','-B','/project/'+script]
                notify('Running '+script+' ('+mode+' execution).')
                with tempfile.TemporaryFile() as output:
                    process=subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                        stdout=output,stderr=subprocess.STDOUT,start_new_session=os.name!='nt')
                    start=time.monotonic()
                    try:
                        while process.poll() is None:
                            if cancelled.wait(.1):raise AgentCancelled('Project checks stopped.')
                            if time.monotonic()-start>self.settings.check_timeout:
                                raise RuntimeError('Project check timed out: '+script+'. Adjust AB_AGENT_CHECK_TIMEOUT if needed.')
                    finally:
                        if process.poll() is None:
                            if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
                            else:process.kill()
                            process.wait()
                    output.seek(0,2);length=output.tell();output.seek(max(0,length-65536))
                    text=self.settings.redact(output.read().decode('utf-8','replace'))
                logs.append({'script':script,'exit_code':process.returncode,'output':text,
                             'output_truncated':length>65536})
                if process.returncode:break
        succeeded=len(logs)==2 and all(x['exit_code']==0 for x in logs)
        validation=None
        if succeeded: validation=self.validate_design()
        report={'executed':True,'execution':mode,'passed':succeeded,'logs':logs,'validation':validation,
                'project_sha256':self.fingerprint(),'at':time.time()}
        self.report=report
        self.reports.mkdir(exist_ok=True)
        (self.reports/'latest.json').write_text(json.dumps(report,indent=2))
        return report

    def package(self):
        missing=[name for name in REQUIRED_FILES if not safe_path(self.root,name).is_file()]
        if missing:raise ValueError('Project is incomplete: '+', '.join(missing))
        syntax=self.check_syntax()
        validation=self.validate_design()
        digest=self.fingerprint()
        report=self.report
        if report is None and (self.reports/'latest.json').is_file():
            report=json.loads((self.reports/'latest.json').read_text())
        if not report or not report.get('passed') or report.get('project_sha256')!=digest:
            raise ValueError('Run the generator and verifier successfully on the current files before downloading the package.')
        # Inspect the archive from bytes; exclude credentials even if generated code wrote them.
        contents=[]
        for item in self.list_files():
            path=safe_path(self.root,item['path'])
            raw=path.read_text(encoding='utf-8')
            redacted=self.settings.redact(raw)
            if raw!=redacted:raise ValueError('A credential was found in '+item['path']+'. Remove it before packaging.')
            if item['path']=='.env.example':
                for line in raw.splitlines():
                    if line.strip().startswith('#') or '=' not in line:continue
                    key,value=line.split('=',1)
                    if re.search(r'(KEY|TOKEN|PASSWORD|SECRET)',key,re.I) and value.strip().strip('"\''):
                        raise ValueError('Empty credentials in .env.example before packaging.')
            contents.append((item['path'],raw.encode()))
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,raw in contents:archive.writestr('artificial-brain-project/'+name,raw)
            archive.writestr('artificial-brain-project/SDK_VALIDATION.json',json.dumps({
                'syntax':syntax,'design':validation,'checks':report,
                'device_build':'not_run','hardware_behavior':'not_run'},indent=2))
        return buffer.getvalue()
