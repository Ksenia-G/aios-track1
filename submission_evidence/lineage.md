# Lineage

Correctness work leading to the measurement, the measurement itself, and the
observability work that came after it. The boundary matters: everything below
the line was **not** present when the numbers were produced.

## Before the measurement — code under test at `205ad86`

| SHA | what it established |
|---|---|
| `c320087` | official CHDD END-stamp: an interval is attributed to the report step that reports it |
| `e15f3a9` | recursive `NO_SETPOINTS_CROSS`: the authority guard descends into containers |
| `b6bc496` | causal T0 StateCard: MAS starts from T0, not from the run's last report step |
| `6dd9bbc` | fallback aggregation derived from actual specialist provenance |
| `da955ca` | Injection structured-output schema agrees with the typed domain contract |
| `418b846` | fresh per-epoch diagnostics: the epoch decision context is rebuilt, not dropped |
| `205ad86` | numeric Model Y well IDs represented as identifier-semantic `well_ids` |

```
════════════════════════════════════════════════════════════════════════
   MEASUREMENT BOUNDARY

   PERF-004-CAUSAL-CLEAN-001   ran at   205ad86
   one run · variants mas-off, mas-on · repeats 1
   decision_steps 3 · simulation_budget 8

   MAS OFF   5325.0465605957925 mln RUB
   MAS ON    5342.722480331016  mln RUB
   delta        +17.675919735223943 mln RUB   (+0.3319%)
   verdict   PERF_004_CAUSAL_CLEAN_GAIN

   Everything BELOW this line came AFTER the numbers above.
   None of it existed in, or contributed to, the frozen artifact.
════════════════════════════════════════════════════════════════════════
```

## After the measurement — observability only

| SHA | what it changed |
|---|---|
| `83cdd43` | post-run audit telemetry (`extra.epoch_audits`, summary schema `1.1.0`) and the MAS OFF Injection provenance correction |

`83cdd43` is observational. It adds records and corrects a metadata counter; it
does not touch specialist decisions, prompts, schema, action enums, the
MagnitudeResolver, candidate generation or ordering, the simulation budget, OPM
inputs, Guardian decisions, CHDD, economic selection, or committed controls.
See `perf_004_errata.md` for the measured scope of what its provenance
correction does and does not move.

## Reading rule

Any artifact carrying `code_sha = 205ad86` is measurement-time evidence.
Any capability described at `83cdd43` is post-measurement and must never be
presented as having been available during the run.
