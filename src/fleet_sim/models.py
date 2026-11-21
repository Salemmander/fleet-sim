from dataclasses import dataclass
from typing import Any, assert_never


@dataclass(frozen=True)
class Idle:
    location: int


@dataclass(frozen=True)
class Transit:
    frm: int
    to: int
    arrival_tick: int


@dataclass(frozen=True)
class Docked:
    location: int


VesselState = Idle | Transit | Docked


@dataclass(frozen=True)
class Dock:
    pass


@dataclass(frozen=True)
class Undock:
    pass


@dataclass(frozen=True)
class SetDest:
    destination: int


Intent = SetDest | Dock | Undock


@dataclass(frozen=True)
class World:
    tick: int
    vessels: dict[str, VesselState]

    def to_dict(self) -> dict[str, Any]:
        vessels: dict[str, dict[str, Any]] = {}
        for vid, state in self.vessels.items():
            match state:
                case Idle(location=loc):
                    vessels[vid] = {"state": "idle", "location": loc}
                case Transit(frm=frm, to=to, arrival_tick=arrival):
                    vessels[vid] = {
                        "state": "transit",
                        "from": frm,
                        "to": to,
                        "arrives_at_tick": arrival,
                    }
                case Docked(location=loc):
                    vessels[vid] = {"state": "docked", "location": loc}
                case _:
                    assert_never(state)
        return {"tick": self.tick, "vessels": vessels}
