from typing import Any, assert_never

from .models import Idle, Intent, SetDest, Transit, VesselState, World


class CommandError(Exception):
    pass


def parse_init(
    params: dict[str, Any],
) -> tuple[list[list[int]], dict[str, VesselState]]:
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

    return travel_times, states


class Engine:
    def __init__(self) -> None:
        self.committed: World | None = None
        self.pending: dict[str, Intent] = {}
        self.travel_times: list[list[int]] = []

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
            case _:
                raise CommandError(f"unknown command: {name}")

    def _init(self, params: dict[str, Any]) -> dict[str, Any]:
        travel_times, states = parse_init(params)
        self.travel_times = travel_times
        self.committed = World(tick=0, vessels=states)
        return self.committed.to_dict()

    def _tick(self, params: dict[str, Any]) -> dict[str, Any]:
        pass

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
                case _:
                    assert_never(intent)

        return World(tick=committed.tick, vessels=vessels)
