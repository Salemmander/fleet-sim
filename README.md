# fleet-sim

A discrete-time, command-driven simulation of a fleet of vessels moving cargo between docks. It reads newline-delimited JSON commands on stdin and writes one JSON response per line to stdout.

## Setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Run

**JSON mode** (the spec interface): one command per line in, one response per line out.

```bash
uv run fleet-sim < tests/golden/cargo.in     # replay a file
uv run fleet-sim                             # or type commands; Ctrl-D to exit
```

```json
{"command":"init","parameters":{"travel_times":[[0,4],[4,0]],"dock_throughput":[1,1],"vessels":[{"id":"v1","location":0}]}}
{"command":"set_destination","parameters":{"vessel_id":"v1","destination":1}}
{"command":"tick","parameters":{}}
```

**Interactive mode**: the same commands without JSON, with readable output.

```bash
uv run fleet-sim -i scenarios/spec.json
```

```
[0]> set_destination v1 1
[0]> tick 0:2:3            # tick, adding 3 units at location 0 bound for 2
[1]> help                  # all commands and how to read the output
```

Changes waiting for the next tick are shown as `committed → planned`. A scenario file is the `parameters` object of `init` (see `scenarios/spec.json`).

## Test

```bash
uv run pytest      # golden tests + interactive mode tests
uv run pyright     # strict type check
```

Golden tests replay each `tests/golden/<name>.in` and compare every response with `<name>.out`. They contain the spec's three examples, with expected output taken from the spec. To add a case, add an `.in`/`.out` pair.

## Layout

```
src/fleet_sim/
  engine.py    command handling, committed/planned state, history
  models.py    vessel states, pending intents, World snapshot
  params.py    command parameter validation (pydantic)
  cli.py       interactive mode
  main.py      entry point: JSON mode or -i
tests/
  golden/      spec examples as .in/.out pairs
scenarios/     sample init parameters for interactive mode
```

Design decisions and their reasoning are in [DESIGN.md](DESIGN.md).
