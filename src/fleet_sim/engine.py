from typing import Any, assert_never, cast

from .models import Idle, Intent, SetDest, Transit, World


class CommandError(Exception):
    pass


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
            case _:
                raise CommandError(f"unknown command: {name}")

    def _init(self, params: dict[str, Any]) -> dict[str, Any]:
        pass

    def _tick(self, params: dict[str, Any]) -> dict[str, Any]:
        pass

    def _set_destination(self, params: dict[str, Any]) -> dict[str, Any]:
        pass

    def _get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        pass

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
