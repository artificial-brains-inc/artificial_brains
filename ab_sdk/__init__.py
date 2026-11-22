"""Artificial Brains SDK Python interface."""

from .autotune import autotune, AutoTuneResult, AutoTuneError, TuneTrial
from .unified_client import ABClient
from .decoder import GenericSpikeDecoder
from .encoder import SpikeEncoder
from .robot_loop import RewardPayload, RobotLoop
from .session import RealtimeSession
from .primalux import (
    PrimaLuxClient, PrimaLuxConfig, PrimaLuxSession, PrimaLuxError,
    PrimaLuxProtocolError, ActuationChannelDecoder,
)

__all__ = [
    "autotune", "AutoTuneResult", "AutoTuneError", "TuneTrial",
    "ABClient", "RealtimeSession", "SpikeEncoder",
    "GenericSpikeDecoder", "RobotLoop", "RewardPayload", "PrimaLuxClient",
    "PrimaLuxConfig", "PrimaLuxSession", "PrimaLuxError",
    "PrimaLuxProtocolError", "ActuationChannelDecoder",
]
