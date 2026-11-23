from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class Params(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class VesselSpec(Params):
    id: str = Field(min_length=1)
    location: int


class ManifestEntry(Params):
    location: int
    destination: int
    quantity: PositiveInt


class InitParams(Params):
    travel_times: list[list[int]]
    dock_throughput: list[PositiveInt]
    vessels: list[VesselSpec]
    cargo_manifest: list[ManifestEntry] = []


class NoParams(Params):
    """get_state takes no parameters."""


class TickParams(Params):
    cargo_manifest: list[ManifestEntry] = []


class VesselParams(Params):
    """dock, undock"""

    vessel_id: str


class DestinationParams(Params):
    """set_destination, load, unload"""

    vessel_id: str
    destination: int


class SwapParams(Params):
    vessel_id: str
    load_destination: int
    unload_destination: int


class TickQueryParams(Params):
    """get_state_at"""

    tick: int
