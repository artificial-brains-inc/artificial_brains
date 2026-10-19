"""Artificial Brains command-line interface."""
from __future__ import annotations
import sys
from ab_sdk.__main__ import main as _unified_main
from ab_sdk.primalux_cli import main as _device_main


def main(argv=None) -> int:
    """Dispatch application and device commands."""
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    # argparse normally prints "__main__.py" when invoked with ``python -m``;
    # keep the displayed public command consistently named ``ab``.
    original_prog = sys.argv[0]
    sys.argv[0] = "ab"
    try:
        if argv and argv[0] == "device":
            return _device_main(argv[1:])
        return _unified_main(argv)
    finally:
        sys.argv[0] = original_prog


if __name__ == "__main__":
    raise SystemExit(main())
