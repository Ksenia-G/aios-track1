# Submission evidence — PERF-004-CAUSAL-CLEAN-001

Auditable package for the accepted Model Y economic measurement.

Its organising rule: **measurement-time evidence and post-measurement
observability work are never mixed.** Everything produced by the run carries
code SHA `205ad86`. The audit telemetry added afterwards carries `83cdd43` and
did not exist when the numbers were produced.

## The claim

> The MAS system generated structural operating hypotheses with GPT-5.6 Sol
> while deterministic components retained numerical control authority. In the
> frozen three-decision Model Y benchmark, MAS improved organizer-compatible
> NPV from 5325.05 to 5342.72 mln RUB (+17.68 mln RUB, +0.332%) versus the
> deterministic ablation. The committed improvement appeared at the final
> decision epoch as a Guardian-valid, conserved redistribution of injection,
> increasing simulated oil production while reducing cumulative water
> injection.

| | END-stamp objective @2014 | organizer-compatible NPV |
|---|---:|---:|
| MAS OFF (`B3_MAS_OFF`) | 1079.492035705738 | 5325.0465605957925 |
| MAS ON (`B4_MAS_ON`) | 1113.9374027519964 | 5342.722480331016 |
| **delta** | **+34.4453670462583** | **+17.675919735223943** |

+0.3319% versus MAS OFF; +0.3054% versus the published official baseline of
5326.453465501771 mln RUB. Verdict `PERF_004_CAUSAL_CLEAN_GAIN`; comparison
class `DIRECT_COMPARABLE`.

Organizer-compatible totals are composed as
`4771.096458908001 + 0.5131581182307065 × END_STAMP_FORECAST_OBJECTIVE_2014`,
never from our own 2007-origin trajectory.

## Mechanism, in one paragraph

The two committed trajectories are identical through T0 and T1 and diverge only
after T2 (2015-04-01), where the committed deck redistributes injection across
9 of 16 injectors with the **commanded total exactly conserved**
(3060.00 → 3060.00 SM³/day, conservation error 0.0000000000): five donors give
up 866.25, four recipients take 866.25. Recipients then meet the 300 bar
injector BHP cap, so *realized* injection falls while commanded total does not.
Over the window MAS ON produced **+8945.625 SM³ more oil** while injecting
**54539.5 SM³ less water**. Producer BHP floor (50.00 bar) and injector BHP cap
(300.00 bar) are identical in both variants — no constraint was relaxed.

## Files

| file | contents |
|---|---|
| `perf_004_measurement.json` | canonical machine-readable economic record |
| `physics_summary.json` | cumulative volumes, divergence point, T2 redistribution, conservation |
| `guardian_summary.json` | committed-trajectory vs search-wide Guardian evidence, search funnel |
| `perf_004_errata.md` | the two historical artifact limitations, and their post-run fixes |
| `lineage.md` | commit lineage with an explicit measurement boundary |
| `reproducibility.md` | provider, experiment, decision grid, frame, caveats |
| `frozen_artifacts/` | the unmodified run outputs the package is derived from |
| `checksums.sha256` | SHA-256 for every file above |

`frozen_artifacts/` holds `benchmark_summary.json` exactly as the run wrote it,
plus the two committed schedule decks. They were scanned for credentials,
machine-local paths, transcripts and scratchpads before inclusion. Nothing in
this package was regenerated, backfilled or edited.

## Known limitations — read before citing

1. **The frozen artifact is not self-auditing per epoch.** Per-epoch StateCard
   dates, diagnostic evidence identities and specialist beams were not
   persisted at `205ad86`. Causality is supported by the decision dates visible
   in the funnel and the committed deck, by repository tests at that SHA, and
   by reconstruction — not by direct fields. Fixed after the run at `83cdd43`.
2. **MAS OFF `fallback_count` reads 3 and should read 6.** A metadata
   under-count in the ablation's Injection provenance, not a behavioural
   difference. MAS ON's `0` is unaffected and still means no fallback occurred.

Both are documented in `perf_004_errata.md`.

## What this evidence does not claim

- not a global optimum, and not a search over the policy space;
- no statistical significance — one run, one repeat, no dispersion measured;
- no repeated-run robustness;
- no identity with official tNavigator economics: our CHDD port is recorded as
  `NOT_OFFICIAL_PENDING_TNAV_REFERENCE`, and the compensation policy as
  `OFFICIAL_UNKNOWN_PORT`;
- no statement about organizer acceptance tolerance;
- **the model did not choose numerical setpoints.** The LLM emitted structural
  hypotheses only; every **numerical well control** reaching the simulator was
  produced by the deterministic MagnitudeResolver, downstream of the
  specialists, under the `NO_SETPOINTS_CROSS` boundary. Physical outcomes come
  from OPM and economics from the CHDD calculator.
