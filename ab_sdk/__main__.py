"""Create, install, and run artificial brains on Prima-Lux."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .design_submit import load_design, find_design, build_on_device, design_sha256
from .primalux import PrimaLuxClient
from .unified_backend import installed_brain_name


def _project(directory: str) -> Path:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'Project directory not found: {root}')
    return root


def _env(project: Path, explicit: str | None) -> Path:
    if explicit: return Path(explicit).expanduser().resolve()
    if (project/'.env').is_file(): return project/'.env'
    if (project.parent/'.env').is_file(): return project.parent/'.env'
    return project/'.env'


def _select_design(project:Path, chosen: str | None):
    if chosen is not None:
        path=Path(chosen).expanduser()
        if not path.is_absolute():path=project/path
        return load_design(path)
    design=find_design(project)
    if design is None: raise ValueError('No Brain design found. Save brain.abdesign.json or specify --brain PATH.')
    return design


def _build(args, project: Path, env: Path):
    brain=_select_design(project,args.brain)
    with PrimaLuxClient.from_env(env) as device:
        result=build_on_device(device,brain,timeout=args.timeout)
    return {'brain':brain.name,'status':result['status'],
            'design_sha256':design_sha256(brain),'device_ready':result['device_ready']}


def _run(args, project: Path, env: Path):
    choices=[p for p in (project/'run.py',project/'main.py',project/'app.py') if p.is_file()]
    if len(choices)==1:
        cmd=[sys.executable,str(choices[0])]
    elif len(choices)>1:
        raise ValueError('Select one application entry point: run.py, main.py or app.py.')
    elif (project/'run.sh').is_file():
        # Existing applications remain compatible; new projects use Python.
        cmd=['bash',str(project/'run.sh'),'ui']
    else:
        raise ValueError('Add an application entry point: run.py, main.py or app.py.')
    with PrimaLuxClient.from_env(env) as device:
        name=installed_brain_name(device.status())
    print(f'[AB] Installed brain: {name}',file=sys.stderr)
    environment=os.environ.copy()
    environment.update(AB_BACKEND='primalux',AB_UNIFIED_TARGET='primalux',
                       AB_UNIFIED_PROJECT=str(project),AB_ENV=str(env))
    # Retains the existing application, robot loop and immediate sensory path.
    return subprocess.call([*cmd,*args.app_args],cwd=project,env=environment)


def _design(args,project:Path):
    from ab_designer.server import serve
    from ab_designer import Brain
    brain=_select_design(project,args.brain) if args.brain else (find_design(project) or Brain('Untitled Brain'))
    serve(brain,port=args.port,open_browser=not args.no_browser,env_path=_env(project, args.env))
    return 0


def main(argv=None) -> int:
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='run' and len(argv)>1 and argv[1]=='-ab': argv.pop(1)
    parser=argparse.ArgumentParser(prog='ab',description='Artificial Brains: design and run on Prima-Lux')
    subs=parser.add_subparsers(dest='command',required=True)
    design=subs.add_parser('design',help='Open the Canvas and Python editor')
    design.add_argument('--project',default='.')
    design.add_argument('--brain')
    design.add_argument('--port',type=int,default=0)
    design.add_argument('--no-browser',action='store_true')
    design.add_argument('--env',help='Local Prima-Lux configuration')
    build=subs.add_parser('build',help='Prepare a Brain design on your Prima-Lux')
    build.add_argument('target',choices=['brain'])
    build.add_argument('--project',default='.')
    build.add_argument('--brain',help='Python or Brain-design file')
    build.add_argument('--env')
    build.add_argument('--timeout',type=float,default=240.0)
    run=subs.add_parser('run',help='Start the installed brain and your application')
    run.add_argument('--project',default='.')
    run.add_argument('--env')
    run.add_argument('app_args',nargs='*')
    args=parser.parse_args(argv)
    try:
        project=_project(args.project)
        env=_env(project,getattr(args,'env',None))
        if args.command=='design':return _design(args,project)
        if args.command=='build':
            print(json.dumps(_build(args,project,env),indent=2))
            return 0
        return _run(args,project,env)
    except Exception as exc:
        print(f'Artificial Brains {args.command}: {exc}',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
