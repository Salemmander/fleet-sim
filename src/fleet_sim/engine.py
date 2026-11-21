from typing import Any, assert_never

from .models import (
    CargoOp,
    CargoOps,
    Dock,
    Docked,
    Idle,
    Intent,
    Load,
    SetDest,
    Swap,
    Transit,
    Undock,
    Unload,
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
    """Validate init parameters.

    Returns (travel_times, dock_throughput, vessel states, dock cargo).

    Checks:
      - travel_times: non-empty N x N list of ints, 0 on the diagonal,
        positive everywhere else (bools rejected)
      - dock_throughput: list of N positive ints
      - vessels: list of {"id": str, "location": int in 0..N-1}, ids unique
      - cargo_manifest: optional, validated by parse_manifest

    Every vessel starts Idle at its location, in input order.
    Raises CommandError on the first violation; never touches engine state.
    """
    travel_times = params.get("travel_times")
    if not isinstance(travel_times, list) or not travel_times:
        raise CommandError("travel_times must be a non-empty NxN matrix")
    n = len(travel_times)

    for i, row in enumerate(travel_times):
        if not isinstance(row, list) or len(row) != n:
            raise CommandError("travel_times must be a non-empty NxN matrix")
        for j, t in enumerate(row):
            # type() not isinstance(): bool is a subclass of int
            if type(t) is not int or (t != 0 if i == j else t <= 0):
                raise CommandError(f"invalid travel time at [{i}][{j}]")

    dock_throughput = params.get("dock_throughput")
    if not isinstance(dock_throughput, list) or len(dock_throughput) != n:
        raise CommandError(f"dock_throughput must be a list of {n} positive integers")
    for t in dock_throughput:
        if type(t) is not int or t <= 0:
            raise CommandError(
                f"dock_throughput must be a list of {n} positive integers"
            )

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


def loads_of(ops: list[CargoOp], d: int) -> int:
    """Count ops that take a unit for destination d off the dock (loads and swaps)."""
    return sum(
        1 for op in ops if op == Load(d) or (isinstance(op, Swap) and op.load == d)
    )


def unloads_of(ops: list[CargoOp], d: int) -> int:
    """Count ops that take a unit for destination d off the vessel (unloads and swaps)."""
    return sum(
        1 for op in ops if op == Unload(d) or (isinstance(op, Swap) and op.unload == d)
    )


def take[K](counts: dict[K, int], key: K) -> None:
    """Remove one unit of key, dropping the entry when it reaches zero."""
    counts[key] -= 1
    if counts[key] == 0:
        del counts[key]


def put[K](counts: dict[K, int], key: K) -> None:
    """Add one unit of key."""
    counts[key] = counts.get(key, 0) + 1


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
            case "load":
                return self._load(params)
            case "unload":
                return self._unload(params)
            case "swap":
                return self._swap(params)
            case _:
                raise CommandError(f"unknown command: {name}")

    def _init(self, params: dict[str, Any]) -> dict[str, Any]:
        travel_times, dock_throughput, states, dock_cargo = parse_init(params)
        self.travel_times = travel_times
        self.dock_throughput = dock_throughput
        self.committed = World(
            tick=0,
            vessels=states,
            vessel_cargo={vid: {} for vid in states},
            dock_cargo=dock_cargo,
        )
        return self.committed.to_dict()

    def _tick(self, params: dict[str, Any]) -> dict[str, Any]:
        manifest = parse_manifest(params.get("cargo_manifest"), len(self.travel_times))
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

        dock_cargo = dict(planned.dock_cargo)
        for key, qty in manifest.items():
            dock_cargo[key] = dock_cargo.get(key, 0) + qty
        self.committed = World(
            tick=new_tick,
            vessels=vessels,
            vessel_cargo=planned.vessel_cargo,
            dock_cargo=dock_cargo,
        )
        self.pending = {}
        return self.committed.to_dict()

    def _set_destination(self, params: dict[str, Any]) -> dict[str, Any]:
        _, vid, current = self._vessel(params)
        dest = params.get("destination")
        if type(dest) is not int or not 0 <= dest < len(self.travel_times):
            raise CommandError(f"destination {dest} is out of range")

        if not isinstance(current, Idle):
            raise CommandError(f"vessel {vid} is not idle")

        if dest == current.location:
            self.pending.pop(vid, None)
        else:
            self.pending[vid] = SetDest(dest)
        return self._planned().to_dict()

    def _dock(self, params: dict[str, Any]) -> dict[str, Any]:
        _, vid, current = self._vessel(params)

        if isinstance(current, Docked) and isinstance(self.pending.get(vid), Undock):
            del self.pending[vid]  # dock after undock: cancel
        elif isinstance(current, Idle):
            self.pending[vid] = Dock()  # overwrites a pending action, including SetDest
        else:
            raise CommandError(f"vessel {vid} is not idle")

        return self._planned().to_dict()

    def _undock(self, params: dict[str, Any]) -> dict[str, Any]:
        _, vid, current = self._vessel(params)

        if isinstance(current, Idle) and isinstance(self.pending.get(vid), Dock):
            del self.pending[vid]  # undock after dock: cancel
        elif isinstance(current, Docked):
            self.pending[vid] = Undock()  # replaces any pending cargo ops
        else:
            raise CommandError(f"vessel {vid} is not docked")

        return self._planned().to_dict()

    def _load(self, params: dict[str, Any]) -> dict[str, Any]:
        committed, vid, current = self._vessel(params)
        d = params.get("destination")
        if type(d) is not int or not 0 <= d < len(self.travel_times):
            raise CommandError(f"destination {d} is out of range")

        if not isinstance(current, Docked):
            raise CommandError(f"vessel {vid} is not docked")

        loc = current.location
        pending = self.pending.get(vid)
        ops = list(pending.ops) if isinstance(pending, CargoOps) else []

        if Unload(d) in ops:
            ops.remove(Unload(d))
        else:
            at_dock = self._ops_at(current)
            if committed.dock_cargo.get((loc, d), 0) - loads_of(at_dock, d) < 1:
                raise CommandError(f"no cargo for destination {d} at location {loc}")
            self._check_throughput(loc, at_dock)
            ops.append(Load(d))

        if ops:
            self.pending[vid] = CargoOps(tuple(ops))
        else:
            del self.pending[vid]
        return self._planned().to_dict()

    def _unload(self, params: dict[str, Any]) -> dict[str, Any]:
        committed, vid, current = self._vessel(params)
        d = params.get("destination")
        if type(d) is not int or not 0 <= d < len(self.travel_times):
            raise CommandError(f"destination {d} is out of range")

        if not isinstance(current, Docked):
            raise CommandError(f"vessel {vid} is not docked")

        loc = current.location
        pending = self.pending.get(vid)
        ops = list(pending.ops) if isinstance(pending, CargoOps) else []

        if Load(d) in ops:
            ops.remove(Load(d))
        else:
            # only this vessel's own pending unloads can claim its cargo
            if committed.vessel_cargo[vid].get(d, 0) - unloads_of(ops, d) < 1:
                raise CommandError(f"vessel {vid} has no cargo for destination {d}")
            self._check_throughput(loc, self._ops_at(current))
            ops.append(Unload(d))

        if ops:
            self.pending[vid] = CargoOps(tuple(ops))
        else:
            del self.pending[vid]
        return self._planned().to_dict()

    def _swap(self, params: dict[str, Any]) -> dict[str, Any]:
        committed, vid, current = self._vessel(params)
        ld, ud = params.get("load_destination"), params.get("unload_destination")
        n = len(self.travel_times)
        if type(ld) is not int or not 0 <= ld < n:
            raise CommandError(f"load_destination {ld} is out of range")
        if type(ud) is not int or not 0 <= ud < n:
            raise CommandError(f"unload_destination {ud} is out of range")

        if not isinstance(current, Docked):
            raise CommandError(f"vessel {vid} is not docked")

        if ld == ud:
            raise CommandError(
                "swap load_destination and unload_destination must differ"
            )

        loc = current.location
        pending = self.pending.get(vid)
        ops = list(pending.ops) if isinstance(pending, CargoOps) else []
        at_dock = self._ops_at(current)

        if committed.dock_cargo.get((loc, ld), 0) - loads_of(at_dock, ld) < 1:
            raise CommandError(f"no cargo for destination {ld} at location {loc}")
        if committed.vessel_cargo[vid].get(ud, 0) - unloads_of(ops, ud) < 1:
            raise CommandError(f"vessel {vid} has no cargo for destination {ud}")
        self._check_throughput(loc, at_dock)

        ops.append(Swap(ld, ud))
        self.pending[vid] = CargoOps(tuple(ops))
        return self._planned().to_dict()

    def _get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._planned().to_dict()

    def _vessel(self, params: dict[str, Any]) -> tuple[World, str, VesselState]:
        """Look up params["vessel_id"]; return (committed world, id, committed state)."""
        committed = self.committed
        assert committed is not None
        vid = params.get("vessel_id")
        if not isinstance(vid, str) or vid not in committed.vessels:
            raise CommandError(f"unknown vessel {vid}")
        return committed, vid, committed.vessels[vid]

    def _ops_at(self, dock: Docked) -> list[CargoOp]:
        committed = self.committed
        assert committed is not None
        return [
            op
            for vid, intent in self.pending.items()
            if isinstance(intent, CargoOps) and committed.vessels[vid] == dock
            for op in intent.ops
        ]

    def _check_throughput(self, loc: int, at_dock: list[CargoOp]) -> None:
        """Raise if one more op at loc would exceed its dock throughput."""
        total = len(at_dock) + 1
        limit = self.dock_throughput[loc]
        if total > limit:
            raise CommandError(
                f"transfer of {total} operations exceeds dock throughput"
                f" of {limit} at location {loc}"
            )

    def _planned(self) -> World:
        committed = self.committed
        assert committed is not None

        vessels = dict(committed.vessels)
        vessel_cargo = dict(committed.vessel_cargo)
        dock_cargo = dict(committed.dock_cargo)
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
                case CargoOps(ops=ops):
                    assert isinstance(current, Docked)
                    loc = current.location
                    cargo = dict(vessel_cargo[vid])
                    for op in ops:
                        match op:
                            case Load(destination=d):
                                take(dock_cargo, (loc, d))
                                put(cargo, d)
                            case Unload(destination=d):
                                take(cargo, d)
                                if d != loc:  # d == loc: delivered, leaves the sim
                                    put(dock_cargo, (loc, d))
                            case Swap(load=ld, unload=ud):
                                take(dock_cargo, (loc, ld))
                                put(cargo, ld)
                                take(cargo, ud)
                                if ud != loc:
                                    put(dock_cargo, (loc, ud))
                            case _:
                                assert_never(op)
                    vessel_cargo[vid] = cargo
                case _:
                    assert_never(intent)

        return World(
            tick=committed.tick,
            vessels=vessels,
            vessel_cargo=vessel_cargo,
            dock_cargo=dock_cargo,
        )
