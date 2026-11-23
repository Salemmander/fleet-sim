# Design

## Overview and Architecture

`fleet-sim` is a discrete-time, command-driven simulation for a fleet of vessels that takes newline-delimited JSON in and returns one JSON response per line out.

The project splits the engine and the I/O. `main.py` only reads user input, parses the JSON, and prints the responses. `Engine.step(dict)` handles all of the simulation's logic. This allows the engine logic to be tested directly and new front ends can be added without affecting the simulation itself.

### Request Flow

```
user input (one line of stdin)
   │
   │  main.py
   ├─ json.loads ................ not JSON        → error response
   ├─ check object is valid ..... not an object   → error response
   ▼
Engine.step()
   ├─ Command model ............. bad envelope    → error response
   ├─ _dispatch ................. not initialized / unknown command → error response
   ├─ handler function .......... invalid parameters or state      → error response
   ▼
response (one JSON line on stdout)
```

### Layout

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

### Interactive Mode

There is an optional interactive mode that can be activated with `uv run fleet-sim -i scenarios/<file.json>`

Interactive mode turns each typed command into the same JSON command `Engine.step()` expects, and turns the Engine's responses into a more readable `committed → planned` output for the user to see all of the pending intents.

## Core Model

### Committed vs. Planned

The committed state is the current state of the sim, while the planned state is derived from the committed state and the pending intents.

The sim stores the current committed state and the pending intents per vessel. Using these two the planned state is derived on demand rather than stored. This is done because certain commands will override others (ex: `dock` after a `set_destination` removes the `set_destination`).

The committed state is what's checked against before an action is accepted into the pending intents, as required by the project specification.

After a user queues an action, the planned state is derived and shown. These actions are not committed until the user sends a tick command. This allows a user to change their mind about certain actions before they are committed (ex: `set_destination 2` then `set_destination 1` before a tick will override the destination to 1).

### Tick

1. Derive the planned state
2. Increment the current tick
3. Vessels in transit that arrive at their destination become idle
4. Add the cargo manifest
5. Set as committed state by appending to the history (`self.history[-1]` is the current committed state)
6. Clear pending intents

### Movement and Cargo Operations

- Movement operations: `set_destination`, `dock`, and `undock`
- Cargo operations: `load`, `unload`, and `swap`

Each vessel can have at most one pending intent: either a single movement operation or a `CargoOps` list, never both. For `set_destination` and `dock` the spec already prevents overlap, since they need an idle vessel and cargo operations need a docked one. The only real conflict is `undock` vs cargo operations, which is handled by overriding (ex: an `undock` after a `load` will just overwrite the `load` command with no need for more complex logic).

### History

History is a list of committed states. Each saved state is protected two ways: frozen dataclasses stop its fields from being reassigned, and since frozen doesn't protect the dicts inside a state (like cargo), `_planned` and `_tick` copy those dicts before editing them when building the next state. A saved state is never edited after it's stored.

## Decisions on Ambiguities

The spec leaves these cases to the implementer:

| Situation | Decision | Why |
| --- | --- | --- |
| `set_destination` to the vessel's current location | Cancels any pending move and the vessel stays idle | Gives users a way to undo a pending move before the next tick |
| `dock` after `dock` | Idempotent and the vessel stays planned as docked | Repeating commands that don't change the result aren't treated as errors |
| `undock` after `dock` | Cancels the pending dock and the vessel stays idle | Latest command wins |
| `undock` after `undock` | Idempotent and the vessel stays planned as undocking | Repeating commands that don't change the result aren't treated as errors |
| `dock` after `undock` | Cancels the pending undock and the vessel stays docked | Latest command wins |
| `undock` after `load` / `unload` / `swap` | The undock replaces the pending cargo operations, which are dropped | Latest command wins and a vessel holds one pending intent (see Core Model) |
| `load` / `unload` / `swap` after `undock` | The cargo operation replaces the pending undock so the vessel stays docked | Latest command wins and a vessel holds one pending intent (see Core Model) |
| `load` and `unload` of the same cargo (how many ops?) | The earlier one is removed from the pending list, and the pair counts as 0 ops | Only operations that still change something at the tick count and the cargo ends up where it started |

Other edge cases the spec doesn't cover:

| Situation | Decision | Why |
| --- | --- | --- |
| Cargo unloaded this tick: can another vessel load it in the same tick? | No, loads are checked against committed dock cargo, so it can be loaded from the next tick | Operations within a tick happen simultaneously |
| Two vessels loading the last unit at a dock | The first load claims it and the second is rejected | Pending loads from every vessel at the dock count as claims, so the same unit can't be promised twice |
| `load 1` + `unload 2` by the same vessel: merge into a `swap`? | No, they stay two separate ops (2 toward throughput) | Preserves the user's intent. A swap happens only when the user asks for one |
| `swap` with the same load and unload destination | Rejected with an error | Swapping items must be between different destinations |
| Manifest entry where `location` == `destination` | Rejected and the whole command fails | Cargo that is already at its destination is invalid as it's already 'arrived' |
| `get_state_at` with a negative tick (spec gives no message) | Error: `tick -1 is out of range (0 to N)` | Time cannot be negative |

## Validation, Errors, and Tradeoffs

### Validation

Pydantic is used to check the shape and types of all parameters while the engine itself checks two kinds of domain rules.

Input rules (is the data itself valid?):

- `travel_times` is a non-empty square matrix, 0 on the diagonal and positive elsewhere
- `dock_throughput` has one entry per location
- vessel ids are unique
- every location and destination index is in range
- a manifest entry's `location` differs from its `destination`

State rules (does the command fit the vessel's committed state?):

- `set_destination` and `dock` need an idle vessel
- `undock`, `load`, `unload`, and `swap` need a docked vessel
- `load` needs dock cargo that isn't already claimed by a pending operation
- `unload` needs the cargo to be on the vessel
- every cargo operation must fit within the dock's throughput

No changes are made to the committed state or pending intents before the commands are validated.

### Errors

All user errors become error responses with a `{"status": "error", "payload": {"message": "<reason>"}}` format as defined by the spec.

Unknown fields are rejected by pydantic using `extra="forbid"` for both the `Command` envelope and the params models inside the `parameters` dict to avoid silently ignoring a misspelled field.

### Tradeoffs

The choice to use pydantic models for validation instead of hand-written `isinstance` checks kept the handlers focused on the simulation rules at the cost of adding a dependency.

## Testing

Run with `uv run pytest`

### Golden Tests

The spec's three examples (base, cargo, and history) are stored as `tests/golden/<name>.in` and `<name>.out` pairs. The test replays each `.in` file through a fresh engine and compares every response with the matching `.out` line.

- The expected output is copied from the spec, so the tests check the engine against the spec rather than against itself.
- Ticks the spec omits (e.g. "ticks 2–3 omitted") are filled in by hand.
- Adding a case only needs a new `.in`/`.out` pair and they are picked up automatically.

### Interactive Mode Tests

`tests/test_cli.py` checks that typed lines turn into the right JSON commands, that malformed lines are rejected with a usage message, and that pending changes are shown as `committed → planned`.

