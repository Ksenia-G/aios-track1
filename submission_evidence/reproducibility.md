# Reproducibility manifest

Configuration of `PERF-004-CAUSAL-CLEAN-001`. External resource locations are
given as semantic labels; this file contains no machine-local paths and no
credentials.

## Code

| | |
|---|---|
| measurement code SHA | `205ad86cd97e69ff2e24a0c7dd097f29183a7444` |
| worktree at run time | clean |
| summary schema of the frozen artifact | `aios.benchmark/1.0.0` |

## Cognitive provider

| setting | value |
|---|---|
| provider | `openai_compatible` |
| model | `gpt-5.6-sol` |
| structured output | `true` |
| reasoning effort | `low` |
| temperature | unset (`null`) |
| timeout | 120 s |
| credential | supplied through the project's provider loader from an untracked local `.env`; never recorded in any artifact |

`temperature` is deliberately unset rather than `0`: this model rejects the
parameter, and the specialists fail closed, which would silently degrade the
run to a deterministic fallback while still labelling it MAS ON.

## Experiment

| setting | value |
|---|---|
| variants | `mas-off`, `mas-on` |
| repeats | 1 |
| decision steps | 3 |
| simulation budget | 8 |
| suite | `mas-v1.1` |
| compensation policy | `OFFICIAL_UNKNOWN_PORT` |

## Decision grid

| epoch | date |
|---|---|
| T0 | 2014-01-01 |
| T1 | 2014-09-01 |
| T2 | 2015-04-01 |
| horizon | 2015-12-01 |

Derived deterministically as `grid[round(i·(n−1)/N)]` over the case's monthly
report-step grid; observed in the frozen artifact's per-epoch `search_funnel`
and in the three control blocks of the committed schedule deck.

## Physical and economic frame

| | |
|---|---|
| case | `model_y_validation` |
| deck | `model_y_compat_case` (semantic label; the OPM-compatible Model Y deck) |
| CHDD calculator | `chdd_python_official_package`, version `7.0.2-negative-row-filter` |
| OPM image | `openporousmedia/opmreleases@sha256:18c497f6a918c160cd290081d4163abf6e2c349797e18e3b5669c9095044adff` |
| Guardian | `aios.agents.constraint_guardian/1.0.0` |
| runtime mode | `FULL` |
| shared initial state digest | `bc0e48fc59a6ec67800e139901c44c950fc06ea535dca0c80b650708dba24222` |

Both variants start from the same initial state digest, which is what makes the
comparison `DIRECT_COMPARABLE`.

## Caveats carried with the numbers

- The frame records `chdd_calculator_version: NOT_OFFICIAL_PENDING_TNAV_REFERENCE`.
  Our CHDD port has not been reconciled against an official tNavigator reference.
- `compensation_policy: OFFICIAL_UNKNOWN_PORT` — the official compensation
  formula remains unconfirmed by the organisers.
- One run, one repeat. No repeated-run dispersion was measured.

## Re-running

Re-running at `205ad86` reproduces the measurement-time code path. Re-running
at `83cdd43` or later reproduces the same decisions with the added per-epoch
audit telemetry, and reports MAS OFF `fallback_count = 6` instead of `3`
(see `perf_004_errata.md`, Erratum B). Either way it is a **new** run and does
not overwrite the frozen record; a new economic run also requires OPM and a
paid provider call.
