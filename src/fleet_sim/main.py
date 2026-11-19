import json
import sys
from typing import Any, cast

from .engine import Engine


def error(message: str) -> dict[str, Any]:
    return {"status": "error", "payload": {"message": message}}


def main() -> None:
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
