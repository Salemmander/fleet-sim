from pathlib import Path

import pytest

from fleet_sim.cli import ParseError, parse_line, run
from fleet_sim.engine import Engine


def test_parse_maps_to_json_commands() -> None:
    assert parse_line("set_destination v1 2") == {
        "command": "set_destination",
        "parameters": {"vessel_id": "v1", "destination": 2},
    }
    assert parse_line("swap v1 1 2")["parameters"] == {
        "vessel_id": "v1",
        "load_destination": 1,
        "unload_destination": 2,
    }
    assert parse_line("tick") == {"command": "tick", "parameters": {}}
    assert parse_line("tick 0:2:3 1:0:1")["parameters"] == {
        "cargo_manifest": [
            {"location": 0, "destination": 2, "quantity": 3},
            {"location": 1, "destination": 0, "quantity": 1},
        ]
    }


@pytest.mark.parametrize(
    "line", ["go v1 2", "load v1", "load v1 x", "tick 0:2", "dock", "get_state extra"]
)
def test_parse_rejects_bad_lines(line: str) -> None:
    with pytest.raises(ParseError):
        parse_line(line)


def test_changes_shown_as_committed_to_planned(tmp_path: Path) -> None:
    scenario = tmp_path / "s.json"
    scenario.write_text(
        '{"travel_times": [[0, 4], [4, 0]], "dock_throughput": [1, 1],'
        ' "vessels": [{"id": "v1", "location": 0}]}'
    )
    engine = Engine()
    start = run(engine, f"init {scenario}")
    assert "map · 2 locations" in start and "tick 0 · committed" in start
    out = run(engine, "set_destination v1 1")
    assert "planned · 1 pending" in out
    assert "idle at 0 → transit from 0 to 1, arrives tick 4" in out
    assert "tick 1 · committed" in run(engine, "tick")
    assert run(engine, "dock v1") == "error: vessel v1 is not idle"
    assert "from 0" in run(engine, "map")
    assert "get_state_at <tick>" in run(engine, "help")


def test_map_before_init() -> None:
    assert run(Engine(), "map") == "error: not initialized"
