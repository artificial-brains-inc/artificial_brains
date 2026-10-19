"""Artificial Brains Designer — local, SDK-independent design tooling.

Future SDK integration can re-export: from ab_designer import Brain
"""
from .model import Brain, DesignError, RHYTHMS, SCHEMA
from .code import parse_python

__version__ = "0.3.7"
__all__ = ["Brain", "DesignError", "RHYTHMS", "SCHEMA", "parse_python"]
