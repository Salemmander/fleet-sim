"""Replay each golden/<name>.in through the engine and compare every response
with the matching line of golden/<name>.out. Cases come from the spec examples."""

import json
from pathlib import Path

import pytest

from fleet_sim.engine import Engine

GOLDEN = Path(__file__).parent / "golden"


@pytest.mark.parametrize("case", sorted(GOLDEN.glob("*.in")), ids=lambda p: p.stem)
def test_golden(case: Path) -> None:
    inputs = case.read_text().splitlines()
    expected = case.with_suffix(".out").read_text().splitlines()

    engine = Engine()
    for line, (cmd, want) in enumerate(zip(inputs, expected, strict=True), start=1):
        assert engine.step(json.loads(cmd)) == json.loads(want), f"line {line}: {cmd}"
