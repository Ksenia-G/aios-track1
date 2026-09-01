# MAS StateCard v1.0

## Purpose

StateCard is the stable structured state contract between the physical simulation layer and future MAS reasoning components. Agents, controllers, and LLM-facing code must consume StateCard, not simulator files.

The first version is intentionally narrow. It defines the object shape, validation rules, deterministic serialization, and extraction from already parsed simulator output. It does not implement agents, optimization, decision making, or a new simulator reader.

## Architecture Position

```text
OPM Flow output
  -> existing summary reader
  -> SummaryData / run metadata / schedule control context
  -> StateExtractor
  -> StateCard artifact
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
- `source_summary_identity`
- `extractor_version`

Changing simulator, summary, extractor, or schedule provenance changes the deterministic StateCard digest. Runtime wall-clock extraction time is intentionally not emitted by the current extractor, because the publication contract requires identical simulator output to produce identical JSON and digest.

### Field State

The field block contains pressure, production, injection, water availability, and compensation state:

- `average_pressure`
- `pressure_trend`
- `oil_rate`
- `water_rate`
- `liquid_rate`
- `water_injection_rate`
- `voidage.FVPR`
- `voidage.FVPT`
- `cumulative.WOPT`
- `cumulative.WWPT`
- `cumulative.WWIT`
- `produced_water_available`
- `compensation.value`
- `compensation.status`

The runtime extractor treats `FPR`, `FOPR`, `FWPR`, `FLPR`, `FWIR`, `FVPR`, and `FVPT` as required simulator vectors. Missing required vectors fail extraction. Compensation formula is not assumed. Training Model Y may report `NOT_APPLICABLE`; unresolved future cases must remain explicit.

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
- `realized.WWIR`
- `realized.WWCT`
- `realized.WOPT`
- `realized.WWPT`
- `realized.WWIT`
- `derived.producer_bhp_margin`
- `derived.injector_bhp_margin`
- `derived.wlpr_margin`

The extractor requires explicit well control context. It does not infer roles or targets from raw simulator artifacts. Missing required well vectors fail extraction; zeros are never substituted.

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

StateCard consumers should treat all fields as immutable. Incompatible future contract changes must advance `schema_version` deliberately.

## Runtime Publication Flow

```text
Simulator
  -> SummaryData
  -> StateExtractor
  -> StateCard
  -> artifacts/state/state_card_YYYY_MM_DD.json
  -> future agents
```

The production validation pipeline now publishes a deterministic StateCard artifact after a clean OPM result has yielded parsed `SummaryData`. The artifact is part of the final successful package and is removed at the start of the next run with other final artifacts. Failed runs do not publish a claimed result or a final StateCard package.

Each published artifact contains:

- `state_card`: the canonical StateCard JSON payload.
- `canonical_digest`: SHA-256 over the canonical StateCard payload.
- `provenance`: case id, run id, schedule hash, simulator digest, source summary identity, extractor version, and source artifact names.

The extractor reuses the existing OPM summary reader and never opens `.SMSPEC`, `.UNSMRY`, `.UNRST`, or raw simulator files itself. Well roles, status, targets, and control modes are derived from the schedule/state layer and supplied as explicit context. Missing required simulator vectors fail closed with a precise extraction error.

## Current Limitations

MAS-STATE-001B publishes field rates, pressure where available, cumulative well totals, well realized vectors, and official realized-constraint margins from parsed `SummaryData`. Geology extraction remains explicit-empty unless a future source is configured. Compensation formula support, agent contracts, and closed-loop controller behavior are future tasks.
