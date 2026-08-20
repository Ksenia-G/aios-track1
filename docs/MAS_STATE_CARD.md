# MAS StateCard v1.0

## Purpose

StateCard is the stable structured state contract between the physical simulation layer and future MAS reasoning components. Agents, controllers, and LLM-facing code must consume StateCard, not simulator files.

The first version is intentionally narrow. It defines the object shape, validation rules, deterministic serialization, and a skeleton extractor over already parsed simulator output. It does not implement agents, optimization, decision making, or a new simulator reader.

## Architecture Position

```text
OPM Flow output
  -> existing summary reader
  -> SummaryData / run metadata / schedule control context
  -> StateExtractor
  -> StateCard
  -> future MAS agents
```

Future MAS agents must not read `.SMSPEC`, `.UNSMRY`, `.UNRST`, workspace directories, raw trajectories, or simulator artifact stores. Those remain owned by the physical simulation layer. The StateCard is the only supported MAS-facing state interface.

## Contract Fields

### Metadata

Every card carries reproducibility metadata:

- `date`
- `case_id`
- `run_id`
- `simulator_digest`
- `schedule_hash`

Changing simulator or schedule provenance changes the deterministic StateCard digest.

### Field State

The field block contains pressure, production, injection, water availability, and compensation state:

- `average_pressure`
- `pressure_trend`
- `oil_rate`
- `water_rate`
- `liquid_rate`
- `water_injection_rate`
- `produced_water_available`
- `compensation.value`
- `compensation.status`

Unavailable values are serialized as `null` and must be accompanied by uncertainty items where the extractor can identify the missing source. Compensation formula is not assumed. Training Model Y may report `NOT_APPLICABLE`; unresolved future cases must remain explicit.

### Well State

Each well contains identity, role, status, current command context, and realized simulator values:

- `well_id`
- `role`: `producer` or `injector`
- `status`
- `controls.current_target`
- `controls.control_mode`
- `realized.BHP`
- `realized.WLPR`
- `realized.WOPR`
- `realized.WWPR`
- `realized.WCT`
- `realized.WOPT`
- `realized.WWPT`
- `realized.WWIT`

The extractor requires explicit well control context. It does not infer roles or targets from raw simulator artifacts.

### Constraint State

Constraints are split into two namespaces:

- `official`: organizer physical constraints checked on realized trajectories.
- `search`: internal controller or command bounds used before simulation.

The card also carries `violations`, `binding_constraints`, and numeric `margins`. Search bounds must not be presented as official constraints.

### Geology Context

Static geological context may include:

- `block_membership`
- `fault_relationship`
- `connectivity_confidence`

Missing geological data is represented by empty mappings. The contract does not invent topology.

### Uncertainty

The uncertainty block is a list of explicit unresolved or unavailable items, for example:

- compensation definition unknown
- field vector unavailable
- weak or missing connectivity information

## Data Ownership

The simulation layer owns raw OPM outputs and heavy artifacts. The schedule/controller side owns current controls and well roles. StateExtractor combines parsed simulator values and explicit control context into StateCard.

StateCard consumers should treat all fields as immutable. If future versions add fields, `schema_version` must change deliberately.

## Current Limitations

MAS-STATE-001A implements only the v1.0 contract and skeleton extraction from `SummaryData`. Real simulator extraction wiring, richer geology extraction, compensation formula support, agent contracts, and closed-loop controller behavior are future tasks.
