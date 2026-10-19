"""Prima-Lux application session. Simulation services are not a developer SDK target."""
from __future__ import annotations
import os
from .unified_backend import PrimaLuxABClient

class ABClient:
    @classmethod
    def from_env(cls, *, env_path=None, **kwargs):
        target = (os.getenv('AB_UNIFIED_TARGET') or os.getenv('AB_BACKEND') or 'primalux').strip().lower()
        if target not in ('primalux', 'ab', 'device'):
            raise ValueError('This SDK supports Prima-Lux only. Connect your device and use ab run.')
        return PrimaLuxABClient.from_env(env_path=env_path)
