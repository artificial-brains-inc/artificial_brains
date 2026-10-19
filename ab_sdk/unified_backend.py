"""Device session adapter for the shared Artificial Brains application API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .primalux import PrimaLuxClient, PrimaLuxConfig, PrimaLuxError

STATE_DIR = '.ab_sdk'
STATE_FILE = 'device_deployment.json'


def _deployment_path(project: Path) -> Path:
    return project / STATE_DIR / STATE_FILE


def _project_root(env_path: str | Path | None) -> Path:
    # Unified runner supplies the project root; independent client usage can
    # default to cwd. No project configuration file is required.
    import os
    raw = os.getenv('AB_UNIFIED_PROJECT', '').strip()
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def installed_brain_name(status: dict[str, Any]) -> str:
    """Use the Device Manager's live native status, not local deployment history.

    An installed brain is accepted by presence alone. Its name is displayed to
    the user; it is not compared against local graph sources or package IDs.
    """
    engine = status.get('engine') or {}
    if not engine.get('has_brain') or engine.get('state') not in ('READY', 'STOPPED', 'RUNNING'):
        raise PrimaLuxError('No runnable brain is currently installed on Prima-Lux.')
    if (status.get('package') or {}).get('pending'):
        raise PrimaLuxError('Prima-Lux is currently installing a brain; run again once installation finishes.')
    name = engine.get('native_brain_name') or ((status.get('package') or {}).get('active') or {}).get('name')
    return name.strip() if isinstance(name, str) and name.strip() else '(unnamed installed brain)'


class _NoRetimingTransport:
    """Guard simulator-scheduled events from the present unscheduled FPGA API."""
    def __init__(self,original):self._original=original
    def __getattr__(self,key):return getattr(self._original,key)
    def send_input(self,*,payload):
        events=payload.get('events') if isinstance(payload,dict) else None
        if isinstance(events,list) and any(isinstance(item,dict) and 'step' in item for item in events):
            raise PrimaLuxError(
                'Live sensory input cannot specify a future neural step. '
                'The brain owns neural execution time. '
                'Inputs were NOT sent, retimed, batched or dropped.'
            )
        return self._original.send_input(payload=payload)
    def send_frame(self,*,events):
        if any(isinstance(item,dict) and 'step' in item for item in events):
            raise PrimaLuxError('Live sensory frames cannot specify neural step numbers; no inputs sent.')
        return self._original.send_frame(events=events)


class PrimaLuxABClient:
    """Match the existing ABClient entry points for deployed device sessions.

    This is a narrowly scoped adapter, not a fake simulator. Project-specific
    cloud-only API operations are deliberately not emulated.
    """
    def __init__(self,device: PrimaLuxClient,project:Path,env_path=None):
        self.device=device
        self.project=project.resolve()
        self.env_path=env_path
        self._source_graph: Path | None=None

    @classmethod
    def from_env(cls,*,env_path=None):
        from .config import load_env_file
        if env_path is not None:load_env_file(Path(env_path))
        return cls(PrimaLuxClient.from_env(env_path),_project_root(env_path),env_path)

    def upload_graph_file(self, *, project_id: str, graph_path: str | Path, **kwargs):
        raise PrimaLuxError('Use a Brain design and ab build brain. Internal neural graphs are not developer files.')

    def start_from_env(self, *, env_path=None, **kwargs):
        """Start the installed brain with this application's available local I/O."""
        config = PrimaLuxConfig.from_env(env_path or self.env_path)
        status = self.device.status()
        installed_brain_name(status)  # No local deployment or graph identity check.
        from .design_submit import find_design, design_contract
        design = find_design(self.project)
        if design is not None:
            contract = design_contract(design)
        elif config.contract_path is not None:
            contract = config.load_contract()
        else:
            raise PrimaLuxError('Brain input/output names are missing. Save a Brain design in this project.')
        engine = status.get('engine') or {}
        session = self.device.start_session(contract, config=config,
                                            start_brain=engine.get('state') != 'RUNNING')
        # Guard only: underlying frame/event transport is unchanged. Do not
        # invent simulation-style step scheduling when hardware lacks it.
        session.input_client = _NoRetimingTransport(session.input_client)
        session.python_client = _NoRetimingTransport(session.python_client)
        return session

    def close(self):self.device.close()
