"""Compatibility route for ``python -m ab.primalux_cli``."""
from ab_sdk.primalux_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
