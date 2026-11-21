from typing import Any, assert_never

from .models import (
    Dock,
    Docked,
    Idle,
    Intent,
    SetDest,
    Transit,
    Undock,
    VesselState,
    World,
)


class CommandError(Exception):
    pass


def parse_manifest(raw: Any, n: int) -> dict[tuple[int, int], int]:
    """Validate an optional cargo_manifest and return (location, destination) -> qty.

    Checks:
      - missing (None) means no cargo; otherwise it must be a list
      - each entry: {"location": int in 0..N-1, "destination": int in 0..N-1,
        "quantity": positive int}, location != destination (bools rejected)

    Entries with the same (location, destination) are summed.
    Raises CommandError on the first bad entry, rejecting the whole manifest.
    """
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise CommandError("cargo_manifest must be a list")
    cargo: dict[tuple[int, int], int] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise CommandError(f"invalid cargo entry: {entry}")
        loc, dest, qty = (
            entry.get("location"),
            entry.get("destination"),
            entry.get("quantity"),
        )
        if (
            type(loc) is not int
            or type(dest) is not int
            or type(qty) is not int
            or not 0 <= loc < n
            or not 0 <= dest < n
            or loc == dest
            or qty <= 0
        ):
            raise CommandError(f"invalid cargo entry: {entry}")

        cargo[(loc, dest)] = cargo.get((loc, dest), 0) + qty

    return cargo


def parse_init(
    params: dict[str, Any],
) -> tuple[
    list[list[int]], list[int], dict[str, VesselState], dict[tuple[int, int], int]
]:
    """Validate init parameters and return (travel_times, vessel states).

    Checks:
      - travel_times: non-empty N x N list of ints, 0 on the diagonal,
        positive everywhere else (bools rejected)
      - vessels: list of {"id": str, "location": int in 0..N-1}, ids unique

    Every vessel starts Idle at its location, in input order.
    Raises CommandError on the first violation; never touches engine state.
    """
    travel_times = params.get("travel_times")
    if not isinstance(travel_times, list) or not travel_times:
        raise CommandError("travel_times must be a non-empty NxN matrix")
    n = len(travel_times)

    dock_throughput = params.get("dock_throughput")
    if not isinstance(dock_throughput, list) or len(dock_throughput) != n:
        raise CommandError(f"dock_throughput must be a list of {n} positive integers")
    for t in dock_throughput:
        if type(t) is not int or t <= 0:
            raise CommandError(
                f"dock_throughput must be a list of {n} positive integers"
            )

    for i, row in enumerate(travel_times):
        if not isinstance(row, list) or len(row) != n:
            raise CommandError("travel_times must be a non-empty NxN matrix")
        for j, t in enumerate(row):
            # type() not isinstance(): bool is a subclass of int
            if type(t) is not int or (t != 0 if i == j else t <= 0):
                raise CommandError(f"invalid travel time at [{i}][{j}]")

    vessels = params.get("vessels")
    if not isinstance(vessels, list):
        raise CommandError("vessels must be a list")
    states: dict[str, VesselState] = {}
    for vessel in vessels:
        if not isinstance(vessel, dict):
            raise CommandError(f"invalid vessel: {vessel}")
        vid, loc = vessel.get("id"), vessel.get("location")
        if not isinstance(vid, str) or type(loc) is not int or not 0 <= loc < n:
            raise CommandError(f"invalid vessel: {vessel}")
        if vid in states:
            raise CommandError(f"duplicate vessel id {vid}")
        states[vid] = Idle(loc)

    dock_cargo = parse_manifest(params.get("cargo_manifest"), n)

    return travel_times, dock_throughput, states, dock_cargo


class Engine:
    def __init__(self) -> None:
        self.committed: World | None = None
        self.pending: dict[str, Intent] = {}
        self.travel_times: list[list[int]] = []
        self.dock_throughput: list[int] = []

    def step(self, cmd: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = self._dispatch(cmd)
            return {"status": "ok", "payload": payload}
        except CommandError as e:
            return {"status": "error", "payload": {"message": str(e)}}

    def _dispatch(self, cmd: dict[str, Any]) -> dict[str, Any]:
        name = cmd.get("command")
        params = cmd.get("parameters", {})
        if not isinstance(params, dict):
            raise CommandError("parameters must be an object")

        if name == "init":
            if self.committed is not None:
                raise CommandError("already initialized")
            return self._init(params)

        if self.committed is None:
            raise CommandError("not initialized")

        match name:
            case "tick":
                return self._tick(params)
            case "set_destination":
                return self._set_destination(params)
            case "get_state":
                return self._get_state(params)
            case "dock":
                return self._dock(params)
            case "undock":
                return self._undock(params)
            case _:
                raise CommandError(f"unknown command: {name}")

    def _init(self, params: dict[str, Any]) -> dict[str, Any]:
        travel_times, states = parse_init(params)
        self.travel_times = travel_times
        self.committed = World(tick=0, vessels=states)
        return self.committed.to_dict()

    def _tick(self, params: dict[str, Any]) -> dict[str, Any]:
        planned = self._planned()
        new_tick = planned.tick + 1

        vessels: dict[str, VesselState] = {}
        for vid, state in planned.vessels.items():
            match state:
                case Transit(to=to, arrival_tick=arrival) if arrival == new_tick:
                    vessels[vid] = Idle(to)
                case Idle() | Transit() | Docked():
                    vessels[vid] = state
                case _:
                    assert_never(state)

        self.committed = World(tick=new_tick, vessels=vessels)
        self.pending = {}
        return self.committed.to_dict()

    def _set_destination(self, params: dict[str, Any]) -> dict[str, Any]:
        committed = self.committed
        assert committed is not None

        vid = params.get("vessel_id")
        dest = params.get("destination")
        if not isinstance(vid, str) or vid not in committed.vessels:
            raise CommandError(f"unknown vessel {vid}")
        if type(dest) is not int or not 0 <= dest < len(self.travel_times):
            raise CommandError(f"destination {dest} is out of range")

        current = committed.vessels[vid]
        if not isinstance(current, Idle):
            raise CommandError(f"vessel {vid} is not idle")

        if dest == current.location:
            self.pending.pop(vid, None)
        else:
            self.pending[vid] = SetDest(dest)
        return self._planned().to_dict()

    def _dock(self, params: dict[str, Any]) -> dict[str, Any]:
        committed = self.committed
        assert committed is not None
        vid = params.get("vessel_id")
        if not isinstance(vid, str) or vid not in committed.vessels:
            raise CommandError(f"unknown vessel {vid}")

        current = committed.vessels[vid]

        if isinstance(current, Docked) and isinstance(self.pending.get(vid), Undock):
            del self.pending[vid]  # dock after undock: cancel
        elif isinstance(current, Idle):
            self.pending[vid] = Dock()  # overwrites a pending action, including SetDest
        else:
            raise CommandError(f"vessel {vid} is not idle")

        return self._planned().to_dict()

    def _undock(self, params: dict[str, Any]) -> dict[str, Any]:
        committed = self.committed
        assert committed is not None
        vid = params.get("vessel_id")
        if not isinstance(vid, str) or vid not in committed.vessels:
            raise CommandError(f"unknown vessel {vid}")

        current = committed.vessels[vid]

        if isinstance(current, Idle) and isinstance(self.pending.get(vid), Dock):
            del self.pending[vid]  # undock after dock: cancel
        elif isinstance(current, Docked):
            self.pending[vid] = Undock()  # idempotent if already undocking
        else:
            raise CommandError(f"vessel {vid} is not docked")

        return self._planned().to_dict()

    def _get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._planned().to_dict()

    def _planned(self) -> World:
        committed = self.committed
        assert committed is not None

        vessels = dict(committed.vessels)
        for vid, intent in self.pending.items():
            current = committed.vessels[vid]
            match intent:
                case SetDest(destination=dest):
                    assert isinstance(current, Idle)
                    vessels[vid] = Transit(
                        frm=current.location,
                        to=dest,
                        arrival_tick=committed.tick
                        + self.travel_times[current.location][dest],
                    )
                case Dock():
                    assert isinstance(current, Idle)
                    vessels[vid] = Docked(current.location)
                case Undock():
                    assert isinstance(current, Docked)
                    vessels[vid] = Idle(current.location)
                case _:
                    assert_never(intent)

        return World(tick=committed.tick, vessels=vessels)
