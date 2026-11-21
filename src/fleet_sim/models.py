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
    vessel_cargo: dict[str, dict[int, int]]  # vessel_id -> {destination: qty}
    dock_cargo: dict[tuple[int, int], int]  # (location, destination) -> qty

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
            vessels[vid]["cargo"] = [
                {"destination": d, "quantity": q}
                for d, q in self.vessel_cargo[vid].items()
            ]
        return {
            "tick": self.tick,
            "vessels": vessels,
            "cargo": [
                {"location": loc, "destination": d, "quantity": q}
                for (loc, d), q in self.dock_cargo.items()
            ],
        }
