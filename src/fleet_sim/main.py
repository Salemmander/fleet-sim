import argparse
import json
import sys
from typing import Any, cast

from .cli import interactive
from .engine import Engine


def error(message: str) -> dict[str, Any]:
    return {"status": "error", "payload": {"message": message}}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fleet-sim",
        description="Fleet simulation engine. Reads JSON commands from stdin by default.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        nargs="?",
        const="",
        metavar="SCENARIO",
        help="interactive mode with short commands; optionally load init parameters from a JSON file",
    )
    args = parser.parse_args()
    if args.interactive is not None:
        interactive(args.interactive or None)
        return

    engine = Engine()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            response = error("invalid JSON")
        else:
            if isinstance(cmd, dict):
                response = engine.step(cast(dict[str, Any], cmd))
            else:
                response = error("command must be a JSON object")
        print(json.dumps(response, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
