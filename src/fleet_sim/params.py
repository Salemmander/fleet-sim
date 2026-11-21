from pydantic import BaseModel, ConfigDict, PositiveInt


class Params(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)


class VesselSpec(Params):
    id: str
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
