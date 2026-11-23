import importlib
import json
from pathlib import Path
from typing import Any

from .engine import Engine

# Positional arguments of each simple command, in order, using the JSON parameter
# names. Every argument except vessel_id is a whole number. init and tick take
# different arguments and are parsed separately.
COMMANDS: dict[str, list[str]] = {
    "set_destination": ["vessel_id", "destination"],
    "dock": ["vessel_id"],
    "undock": ["vessel_id"],
    "load": ["vessel_id", "destination"],
    "unload": ["vessel_id", "destination"],
    "swap": ["vessel_id", "load_destination", "unload_destination"],
    "get_state": [],
    "get_state_at": ["tick"],
}

USAGE = {
    "init": "init <file.json>",
    "tick": "tick [location:destination:quantity ...]",
    **{name: " ".join([name, *(f"<{a}>" for a in args)]) for name, args in COMMANDS.items()},
}

HELP = "\n".join(
    [
        "commands (same names and parameters as the JSON interface, given in order):",
        *(f"  {usage}" for usage in USAGE.values()),
        "  map                show travel times between locations",
        "  help",
        "  quit               (or Ctrl-D)",
        "",
        "reading the output:",
        '  cargo "2 ×3"        three units of cargo whose destination is location 2',
        '  "a → b"             a is the committed value now; b is what it becomes on the next tick',
        '  "planned · N pending"   N vessels have commands waiting for the next tick',
    ]
)


class ParseError(Exception):
    pass


def _int(word: str, name: str) -> int:
    try:
        return int(word)
    except ValueError:
        raise ParseError(f"{name} must be a whole number, got {word!r}") from None


def _manifest_entry(word: str) -> dict[str, int]:
    parts = word.split(":")
    if len(parts) != 3:
        raise ParseError(f"cargo must be location:destination:quantity, got {word!r}")
    loc, dest, qty = (_int(p, "cargo value") for p in parts)
    return {"location": loc, "destination": dest, "quantity": qty}


def _read_init(path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except OSError as e:
        raise ParseError(f"cannot read {path}: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise ParseError(f"{path} is not valid JSON: {e.msg}") from None


def parse_line(line: str) -> dict[str, Any]:
    """Turn one typed line into a JSON-interface command dict.

    Raises ParseError for unknown commands or the wrong number/kind of arguments.
    """
    name, *args = line.split()
    if name not in USAGE:
        raise ParseError(f"unknown command {name!r} (type 'help')")

    if name == "tick":
        params = {"cargo_manifest": [_manifest_entry(a) for a in args]} if args else {}
    elif len(args) != (1 if name == "init" else len(COMMANDS[name])):
        raise ParseError(f"usage: {USAGE[name]}")
    elif name == "init":
        params = _read_init(args[0])
    else:
        params = {
            key: word if key == "vessel_id" else _int(word, key)
            for key, word in zip(COMMANDS[name], args)
        }
    return {"command": name, "parameters": params}


def _vessel_text(v: dict[str, Any]) -> str:
    match v["state"]:
        case "transit":
            return f"transit from {v['from']} to {v['to']}, arrives tick {v['arrives_at_tick']}"
        case "docked":
            return f"docked at {v['location']}"
        case _:
            return f"idle at {v['location']}"


def _cargo_text(entries: list[dict[str, Any]]) -> str:
    return ", ".join(f"{e['destination']} ×{e['quantity']}" for e in entries) or "none"


def _change(before: str, after: str) -> str:
    return before if before == after else f"{before} → {after}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    return [
        "  " + "   ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip()
        for r in [header, *rows]
    ]


def render(
    planned: dict[str, Any],
    committed: dict[str, Any],
    throughput: list[int],
    label: str,
) -> str:
    """Show a world, marking every field that differs between committed and planned."""
    vessel_rows: list[list[str]] = []
    for vid, p in planned["vessels"].items():
        c = committed["vessels"][vid]
        state = _change(_vessel_text(c), _vessel_text(p))
        cargo = _change(_cargo_text(c["cargo"]), _cargo_text(p["cargo"]))
        vessel_rows.append([vid, state, cargo])
    pending = sum(p != committed["vessels"][vid] for vid, p in planned["vessels"].items())

    def waiting(world: dict[str, Any], loc: int) -> str:
        return _cargo_text([e for e in world["cargo"] if e["location"] == loc])

    dock_rows = [
        [str(loc), _change(waiting(committed, loc), waiting(planned, loc)), str(limit)]
        for loc, limit in enumerate(throughput)
    ]

    if pending:
        label = f"planned · {pending} pending (now → after next tick)"
    return "\n".join(
        [
            f"tick {planned['tick']} · {label}",
            "",
            *_table(["vessel", "state", "cargo (destination ×quantity)"], vessel_rows),
            "",
            *_table(
                [
                    "location",
                    "dock cargo (destination ×quantity)",
                    "dock_throughput (ops per tick)",
                ],
                dock_rows,
            ),
        ]
    )


def render_map(travel_times: list[list[int]]) -> str:
    """Travel time in ticks between every pair of locations."""
    n = len(travel_times)
    header = ["", *(f"to {j}" for j in range(n))]
    rows = [
        [f"from {i}", *("-" if i == j else str(t) for j, t in enumerate(row))]
        for i, row in enumerate(travel_times)
    ]
    return "\n".join(
        [f"map · {n} locations, travel time in ticks", "", *_table(header, rows)]
    )


def run(engine: Engine, line: str) -> str:
    """Handle one typed line (help, map, or an engine command) and return what to print."""
    if line == "help":
        return HELP
    if line == "map":
        return render_map(engine.travel_times) if engine.committed else "error: not initialized"
    try:
        cmd = parse_line(line)
    except ParseError as e:
        return f"error: {e}"

    response = engine.step(cmd)
    if response["status"] == "error":
        return f"error: {response['payload']['message']}"
    world = response["payload"]
    current = engine.committed
    assert current is not None  # any successful command means init has happened

    if cmd["command"] == "get_state_at":
        # a stored snapshot is committed by definition: nothing to compare against
        historic = world["tick"] < current.tick
        label = "committed (history)" if historic else "committed"
        return render(world, world, engine.dock_throughput, label)

    shown = render(world, current.to_dict(), engine.dock_throughput, "committed")
    if cmd["command"] == "init":
        return render_map(engine.travel_times) + "\n\n" + shown
    return shown


def interactive(scenario: str | None) -> None:
    try:
        importlib.import_module("readline")  # arrow keys and history for input()
    except ImportError:
        pass

    engine = Engine()
    print("fleet-sim interactive. Type 'help' for commands, Ctrl-D to quit.")
    if scenario:
        print(run(engine, f"init {scenario}"))
    else:
        print("start with: init <file.json>")

    while True:
        tick = engine.committed.tick if engine.committed else "-"
        try:
            line = input(f"\n[{tick}]> ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            return
        if not line:
            continue
        if line in ("quit", "exit"):
            return
        print(run(engine, line))
