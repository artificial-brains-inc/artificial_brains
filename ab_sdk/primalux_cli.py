"""Prima-Lux device commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .primalux import PrimaLuxClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prima-Lux device management")
    parser.add_argument("--env", default=".env", help="Application environment file")
    commands = parser.add_subparsers(dest="operation", required=True)
    for command in ("status", "events", "run", "stop", "reset", "reset-dynamics"):
        commands.add_parser(command)
    sense = commands.add_parser("sensors", help="Submit a sensory frame from a JSON file")
    sense.add_argument("json_file")
    args = parser.parse_args(argv)

    try:
        with PrimaLuxClient.from_env(args.env) as device:
            if args.operation == "status":
                result = device.status()
            elif args.operation == "events":
                result = device.events()
            elif args.operation == "run":
                result = device.run()
            elif args.operation == "stop":
                print('Waiting for neural dynamics to finish saving...', file=sys.stderr, flush=True)
                result = device.stop_and_wait()
            elif args.operation == "reset":
                result = device.reset()
            elif args.operation == "reset-dynamics":
                result = device.reset_dynamics()
            elif args.operation == "sensors":
                payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Sensor frame must be a JSON object.")
                result = device.send_sensors(payload)
            else:
                raise ValueError("Unknown device operation.")
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"Prima-Lux device: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
