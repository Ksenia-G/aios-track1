# PERF-004 errata

Two limitations of the **frozen historical artifact** produced by
`PERF-004-CAUSAL-CLEAN-001` at code SHA `205ad86`.

Both were found *after* the measurement and fixed *after* it, at `83cdd43`.
Neither changes a decision, a control or an economic value. The historical
artifact has not been edited, regenerated or backfilled, and no economic rerun
was performed — so the frozen file still exhibits both limitations below, by
design. That is the point: the record shows what was actually observed.

---

## ERRATUM A — the artifact was not self-auditing per epoch

At `205ad86`, `benchmark_summary.json` did **not** persist:

- per-epoch StateCard dates;
- the four per-epoch diagnostic artifact identities (pressure, sweep, support
  connectivity, regional support imbalance);
- complete specialist beams, hypotheses and per-call provenance.

**What this does and does not mean.** The run used the intended pipeline, and
the causal properties were covered by repository tests at that SHA. Two facts
about the decision grid *are* recoverable from the frozen artifacts, and are
recorded in this package: the per-epoch `search_funnel` carries the effective
dates `2014-01-01`, `2014-09-01`, `2015-04-01`, and the committed schedule deck
contains exactly three control blocks stamped with those same dates.

What was **not** recoverable is the per-epoch evidence identity and specialist
beam. Causality was therefore demonstrated by runtime behaviour and by
reconstruction from surrounding artifacts, **not** read directly out of the
final artifact.

Fixed post-run at `83cdd43` (`BENCHMARK_AUDIT_TELEMETRY_FIXED`): summary schema
`aios.benchmark/1.1.0` adds `extra.epoch_audits`, one observational record per
epoch. **That telemetry did not exist when PERF-004 ran.** Any future run at
`83cdd43` or later will carry it; this frozen artifact never will.

---

## ERRATUM B — MAS OFF `fallback_count` reads 3, should read 6

The frozen artifact reports `fallback_count = 3` for `B3_MAS_OFF`.

**This is a telemetry defect, not a policy difference.** The MAS OFF ablation
behaved correctly; it under-counted itself.

At `205ad86`, with `llm_enabled=False`:

| specialist | proposal source | `fallback_used` |
|---|---|---|
| Production | `DETERMINISTIC_FALLBACK` | `True` (reason `llm_disabled`) |
| Injection | `DETERMINISTIC_FALLBACK` | `False` ← the defect |

`fallback_count` aggregates both specialists independently per epoch, so three
epochs counted the three Production decisions and none of the three Injection
decisions. The semantically correct count for three epochs is **6**.

Fixed at `83cdd43` by correcting the authoritative per-call provenance — the
Injection controller now passes the same `llm_disabled` reason Production
already passed — with no downstream special-casing of the counter.

**MAS ON is unaffected in both the old and the new code.** Its
`fallback_count = 0` was and remains meaningful: the live branch was never
touched, and every deterministic-floor exit on that branch carries its own
distinct reason. The frozen `0` therefore still means what it meant — no
specialist decision fell back.

### Reading the two numbers together

A reader comparing this package with a future run must not read `3` versus `6`
as the ablation having changed. Same behaviour, differently counted:

| | frozen artifact (`205ad86`) | future run (`83cdd43`+) |
|---|---|---|
| MAS OFF `fallback_count` | 3 | 6 |
| MAS ON `fallback_count` | 0 | 0 |
| committed controls | unchanged between the two | unchanged |

---

## Scope of the post-run correction

Making provenance truthful changes identities that embed it. Measured on the
project's deterministic harness, comparing `205ad86` with `83cdd43`:

**Unchanged** — committed candidate digest, candidate ordering, every candidate
digest, realized next-state digest, numerical controls, and therefore all
economics.

**Changed, for MAS OFF only** — the arbiter decision digest, and under the real
resolver the non-`HOLD` bundle digests, because a proposal's provenance sits
inside its digested payload. `HOLD` carries no proposal reference by contract
and is untouched — and `HOLD` is what MAS OFF committed in PERF-004. MAS ON is
bit-identical, so the frozen `+17.675919735223943` mln RUB result is
unaffected in every respect.
