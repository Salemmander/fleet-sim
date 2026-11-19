from dataclasses import dataclass


@dataclass(frozen=True)
class Idle:
    location: int


@dataclass(frozen=True)
class Transit:
    frm: int
    to: int
    arrival_tick: int


VesselState = Idle | Transit


@dataclass(frozen=True)
class SetDest:
    destination: int


Intent = SetDest


@dataclass(frozen=True)
class World:
    tick: int
    vessels: dict[str, VesselState]
