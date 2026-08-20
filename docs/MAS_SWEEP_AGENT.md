# MAS Sweep / Water-Cut Agent

## Role

Sweep / Water-Cut Agent is a deterministic advisory agent for water-management and sweep-efficiency reasoning. It reads the current `StateCard` and produces one structured `SweepProposal`.

The agent exists to identify:

- excessive produced-water risk;
- poor oil efficiency;
- high liquid rate combined with high water cut;
- producer-side water-management opportunities.

It proposes; it does not execute.

## Architecture

```text
Simulator
  -> StateExtractor
  -> StateCard
  -> SweepAgent
  -> SweepProposal
  -> ConstraintGuardian
  -> Arbiter later
```

SweepAgent reads only `StateCard`. It must not read `SummaryData`, simulator files, deck files, CSV files, experiment outputs, restart artifacts, or schedules.

## Ownership

SweepAgent owns deterministic producer-side sweep and water-cut proposal logic:

- high `WWCT` detection;
- low oil-efficiency detection using `WOPR / WLPR`;
- near-WLPR-limit risk detection;
- advisory proposal classification.

Allowed proposal types:

- `HOLD`
- `REDUCE_WATER_RISK`
- `PRODUCER_PRIORITY_CHANGE`
- `WATER_SWEEP_WARNING`

The v1 implementation emits `HOLD`, `REDUCE_WATER_RISK`, or `WATER_SWEEP_WARNING`.

## Relationship With Pressure Agent

PressureAgent reasons about pressure support and injector-side pressure capacity. SweepAgent reasons about producer water behavior and oil efficiency. They may both produce advisory proposals from the same StateCard, but neither decides final action.

## Relationship With Constraint Guardian

Constraint Guardian owns official legality:

- producer BHP violations;
- injector BHP violations;
- official WLPR violations.

SweepAgent must not report official constraint violations. It may report advisory risks such as `NEAR_WLPR_LIMIT`, but that is not a legality verdict.

## Output Contract

`SweepProposal` contains:

- `metadata.state_card_digest`
- `metadata.agent_version`
- `proposal_type`
- `target`
- `reason.water_condition`
- `reason.oil_efficiency_condition`
- `reason.expected_effect`
- `confidence`
- `risk_flags`

Proposal digests are deterministic over canonical proposal JSON.

## Fail-Closed Behavior

Missing required StateCard values block proposal generation. The agent never substitutes zero, skips wells, or reads raw artifacts to fill gaps.

Required producer data:

- `WLPR`
- `WOPR`
- `WWPR`
- `WWCT`
- `wlpr_margin`

The agent is intentionally advisory. Later MAS tasks may add richer regional targeting or action payloads, but final legality remains with Constraint Guardian and final selection remains with future arbitration.
