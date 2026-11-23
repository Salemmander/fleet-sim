from typing import Any, assert_never, cast

from pydantic import ValidationError

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
from .params import (
    DestinationParams,
    InitParams,
    ManifestEntry,
    NoParams,
    SwapParams,
    TickParams,
    TickQueryParams,
    VesselParams,
)


class CommandError(Exception):
    pass


def parse_manifest(
    entries: list[ManifestEntry], n: int
) -> dict[tuple[int, int], int]:
    """Check manifest entries against the map; return (location, destination) -> qty.

    Types and positive quantities are already checked by ManifestEntry. Here:
    location and destination in 0..N-1, and location != destination.
    Entries with the same (location, destination) are summed.
    Raises CommandError on the first bad entry, rejecting the whole manifest.
    """
    cargo: dict[tuple[int, int], int] = {}
    for e in entries:
        if not (0 <= e.location < n and 0 <= e.destination < n) or e.location == e.destination:
            raise CommandError(f"invalid cargo entry: {e.model_dump()}")
        key = (e.location, e.destination)
        cargo[key] = cargo.get(key, 0) + e.quantity
    return cargo


def parse_init(
    p: InitParams,
) -> tuple[dict[str, VesselState], dict[tuple[int, int], int]]:
    """Check init parameters against each other; return (vessel states, dock cargo).

    Types and positive throughputs are already checked by InitParams. Here:
      - travel_times: non-empty N x N, 0 on the diagonal, positive elsewhere
      - dock_throughput: N entries
      - vessels: location in 0..N-1, ids unique
      - cargo_manifest: checked by parse_manifest

    Every vessel starts Idle at its location, in input order.
    Raises CommandError on the first violation; never touches engine state.
    """
    n = len(p.travel_times)
    if n == 0 or any(len(row) != n for row in p.travel_times):
        raise CommandError("travel_times must be a non-empty NxN matrix")
    for i, row in enumerate(p.travel_times):
        for j, t in enumerate(row):
            if t != 0 if i == j else t <= 0:
                raise CommandError(f"invalid travel time at [{i}][{j}]")

    if len(p.dock_throughput) != n:
        raise CommandError(f"dock_throughput must be a list of {n} positive integers")

    states: dict[str, VesselState] = {}
    for v in p.vessels:
        if not 0 <= v.location < n:
            raise CommandError(f"invalid vessel: {v.model_dump()}")
        if v.id in states:
            raise CommandError(f"duplicate vessel id {v.id}")
        states[v.id] = Idle(v.location)

    return states, parse_manifest(p.cargo_manifest, n)


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
        self.history: list[World] = []  # committed state at each tick; index = tick
        self.pending: dict[str, Intent] = {}
        self.travel_times: list[list[int]] = []
        self.dock_throughput: list[int] = []

    @property
    def committed(self) -> World | None:
        """The current committed state: the latest snapshot, None before init."""
        return self.history[-1] if self.history else None

    def step(self, cmd: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = self._dispatch(cmd)
            return {"status": "ok", "payload": payload}
        except CommandError as e:
            return {"status": "error", "payload": {"message": str(e)}}
        except ValidationError as e:
            err = e.errors()[0]
            where = ".".join(str(part) for part in err["loc"])
            message = f"invalid parameter {where}: {err['msg']}"
            return {"status": "error", "payload": {"message": message}}

    def _dispatch(self, cmd: dict[str, Any]) -> dict[str, Any]:
        name = cmd.get("command")
        raw = cmd.get("parameters", {})
        if not isinstance(raw, dict):
            raise CommandError("parameters must be an object")
        params = cast(dict[str, Any], raw)

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
            case "get_state_at":
                return self._get_state_at(params)
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
        p = InitParams.model_validate(params)
        states, dock_cargo = parse_init(p)
        self.travel_times = p.travel_times
        self.dock_throughput = p.dock_throughput
        world = World(
            tick=0,
            vessels=states,
            vessel_cargo={vid: {} for vid in states},
            dock_cargo=dock_cargo,
        )
        self.history.append(world)
        return world.to_dict()

    def _tick(self, params: dict[str, Any]) -> dict[str, Any]:
        p = TickParams.model_validate(params)
        manifest = parse_manifest(p.cargo_manifest, len(self.travel_times))
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
        world = World(
            tick=new_tick,
            vessels=vessels,
            vessel_cargo=planned.vessel_cargo,
            dock_cargo=dock_cargo,
        )
        self.history.append(world)
        self.pending = {}
        return world.to_dict()

    def _set_destination(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DestinationParams.model_validate(params)
        _, vid, current = self._vessel(p.vessel_id)
        dest = p.destination
        if not 0 <= dest < len(self.travel_times):
            raise CommandError(f"destination {dest} is out of range")

        if not isinstance(current, Idle):
            raise CommandError(f"vessel {vid} is not idle")

        if dest == current.location:
            self.pending.pop(vid, None)
        else:
            self.pending[vid] = SetDest(dest)
        return self._planned().to_dict()

    def _dock(self, params: dict[str, Any]) -> dict[str, Any]:
        p = VesselParams.model_validate(params)
        _, vid, current = self._vessel(p.vessel_id)

        if isinstance(current, Docked) and isinstance(self.pending.get(vid), Undock):
            del self.pending[vid]  # dock after undock: cancel
        elif isinstance(current, Idle):
            self.pending[vid] = Dock()  # overwrites a pending action, including SetDest
        else:
            raise CommandError(f"vessel {vid} is not idle")

        return self._planned().to_dict()

    def _undock(self, params: dict[str, Any]) -> dict[str, Any]:
        p = VesselParams.model_validate(params)
        _, vid, current = self._vessel(p.vessel_id)

        if isinstance(current, Idle) and isinstance(self.pending.get(vid), Dock):
            del self.pending[vid]  # undock after dock: cancel
        elif isinstance(current, Docked):
            self.pending[vid] = Undock()  # replaces any pending cargo ops
        else:
            raise CommandError(f"vessel {vid} is not docked")

        return self._planned().to_dict()

    def _load(self, params: dict[str, Any]) -> dict[str, Any]:
        p = DestinationParams.model_validate(params)
        committed, vid, current = self._vessel(p.vessel_id)
        d = p.destination
        if not 0 <= d < len(self.travel_times):
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
        p = DestinationParams.model_validate(params)
        committed, vid, current = self._vessel(p.vessel_id)
        d = p.destination
        if not 0 <= d < len(self.travel_times):
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
        p = SwapParams.model_validate(params)
        committed, vid, current = self._vessel(p.vessel_id)
        ld, ud = p.load_destination, p.unload_destination
        n = len(self.travel_times)
        if not 0 <= ld < n:
            raise CommandError(f"load_destination {ld} is out of range")
        if not 0 <= ud < n:
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
        NoParams.model_validate(params)
        return self._planned().to_dict()

    def _get_state_at(self, params: dict[str, Any]) -> dict[str, Any]:
        tick = TickQueryParams.model_validate(params).tick
        current = len(self.history) - 1
        if tick > current:
            raise CommandError(f"tick {tick} is in the future (current tick: {current})")
        if tick < 0:
            raise CommandError(f"tick {tick} is out of range (0 to {current})")
        return self.history[tick].to_dict()

    def _vessel(self, vid: str) -> tuple[World, str, VesselState]:
        """Look up a vessel; return (committed world, id, committed state)."""
        committed = self.committed
        assert committed is not None
        if vid not in committed.vessels:
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
