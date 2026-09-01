# MAS Constraint Guardian

## Role

Constraint Guardian is the deterministic legality layer for Track 1 MAS. It answers whether the current StateCard satisfies official realized physical constraints. It does not optimize, rank actions, estimate NPV, suggest controls, call an LLM, or read simulator artifacts.

The Guardian has veto power because legality is a hard gate. Any future controller or arbiter must treat a failed official check as a blocked state/action, not as a soft preference.

## Architecture

```text
Simulator
  -> SummaryData
  -> StateExtractor
  -> StateCard
  -> ConstraintGuardian
  -> ConstraintReport
```

Constraint Guardian reads only `StateCard`. It must not read `.SMSPEC`, `.UNSMRY`, `.UNRST`, CSV files, deck files, OPM workspaces, or raw simulator artifacts. StateCard is the only allowed interface between the numerical simulator layer and future MAS agents.

## Official Constraints

The Guardian evaluates only organizer physical constraints from `StateCard.constraints.official`:

- Producer `WBHP >= producer_bhp_min`
- Injector `WBHP <= injector_bhp_max`
- Producer realized `WLPR <= wlpr_max`

Internal controller/search constraints remain separate under `StateCard.constraints.search`. They may describe candidate limits, optimizer bounds, or policy limits, but they are never reported as official violations.

## Output Contract

`ConstraintReport` contains:

- `metadata.state_card_digest`
- `metadata.evaluation_version`
- `official_checks`
- `summary`
- `violations`
- `near_binding`

Each official check records:

- `well_name`
- `constraint`
- `measured_value`
- `limit`
- `margin`
- `status`
- `near_binding`
- `reason`

Default near-binding margins:

- BHP: `0.5 bar`
- WLPR: `5 m3/day`

Near-binding checks are still `PASS`; they are exposed so future MAS orchestration can treat them cautiously without confusing them with violations.

## Fail-Closed Behavior

Missing required data is an evaluation error. The Guardian never substitutes zero, skips a well, or silently continues. Examples:

- Missing producer `WBHP` blocks evaluation.
- Missing injector `WBHP` blocks evaluation.
- Missing producer `WLPR` blocks evaluation.
- Missing official constraint limits block evaluation.

This makes the Guardian safe as the first deterministic agent foundation while later MAS components are still absent.
