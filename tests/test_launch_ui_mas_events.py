"""Граф агентов MAS: разметка не меняет ни объектов, ни решений.

Без страницы запуска контур собирается из тех же самых объектов, что и
раньше. Со страницей границы оборачиваются, но обёртки проходят проверку
протоколов (`@runtime_checkable` в Python 3.12 ищет методы статически) и
возвращают ровно то, что вернул настоящий объект.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from aios.benchmark.mas_variant import _announced, _announced_boundaries, _proposal_reasons
from aios.mas.orchestration.boundaries import (
    CHDD,
    CandidateExecutor,
    RealizedConstraintGuardian,
    StateAdvancer,
    TrajectoryConstraintGuardianLike,
)
from aios.ui.events import EVENTS_ENV


@dataclass
class _Recorder:
    calls: list[str] = field(default_factory=list)

    def execute(self, candidate, *, run_label):
        self.calls.append(f"execute:{run_label}")
        return SimpleNamespace(result=SimpleNamespace(status="SUCCESS"))

    def evaluate(self, *args, **kwargs):
        self.calls.append("evaluate")
        return SimpleNamespace(
            violations=(), result=SimpleNamespace(npv_mln_rub=1249.4366799710856)
        )

    def advance(self, state, candidate, execution):
        self.calls.append("advance")
        return "next-state"

    def propose(self, envelope):
        self.calls.append("propose")
        return SimpleNamespace(primary_proposal=None)


def _boundaries() -> dict[str, object]:
    return {
        "candidate_executor": _Recorder(),
        "realized_state_extractor": _Recorder(),
        "guardian": _Recorder(),
        "trajectory_guardian": _Recorder(),
        "chdd": _Recorder(),
        "state_advancer": _Recorder(),
    }


def test_without_the_page_the_loop_gets_the_very_same_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EVENTS_ENV, raising=False)
    boundaries = _boundaries()
    agent = _Recorder()

    assert _announced_boundaries(boundaries) is boundaries
    assert _announced(agent, "production") is agent


def test_announced_boundaries_still_satisfy_their_protocols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EVENTS_ENV, "1")
    wrapped = _announced_boundaries(_boundaries())

    assert isinstance(wrapped["candidate_executor"], CandidateExecutor)
    assert isinstance(wrapped["guardian"], RealizedConstraintGuardian)
    assert isinstance(wrapped["trajectory_guardian"], TrajectoryConstraintGuardianLike)
    assert isinstance(wrapped["chdd"], CHDD)
    assert isinstance(wrapped["state_advancer"], StateAdvancer)


def test_announced_calls_return_what_the_real_object_returned(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(EVENTS_ENV, "1")
    boundaries = _boundaries()
    wrapped = _announced_boundaries(boundaries)
    candidate = SimpleNamespace(candidate_kind="AGENT_BUNDLE")

    execution = wrapped["candidate_executor"].execute(candidate, run_label="c1")
    evaluation = wrapped["chdd"].evaluate(candidate, execution)
    next_state = wrapped["state_advancer"].advance("state", candidate, execution)

    assert execution.result.status == "SUCCESS"
    assert evaluation.result.npv_mln_rub == 1249.4366799710856
    assert next_state == "next-state"
    assert boundaries["candidate_executor"].calls == ["execute:c1"]

    events = [
        json.loads(line.removeprefix("::aios "))
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("::aios ")
    ]
    assert [(event["node"], event["state"]) for event in events if "state" in event] == [
        ("opm", "active"),
        ("opm", "done"),
        ("chdd", "active"),
        ("chdd", "done"),
        ("selector", "active"),
        ("selector", "done"),
    ]
    assert events[3]["text"] == "ЧДД 1249.44 млн ₽"


def test_a_failing_agent_is_marked_and_the_error_is_not_swallowed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(EVENTS_ENV, "1")

    class Broken:
        def propose(self, envelope):
            raise RuntimeError("provider is down")

    agent = _announced(Broken(), "injection")

    with pytest.raises(RuntimeError, match="provider is down"):
        agent.propose(object())
    assert '"state": "fail"' in capsys.readouterr().out


def test_selector_explains_why_the_committed_candidate_won(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(EVENTS_ENV, "1")
    wrapped = _announced_boundaries(_boundaries())
    candidate = SimpleNamespace(candidate_kind="AGENT_BUNDLE")

    execution = wrapped["candidate_executor"].execute(candidate, run_label="c1")
    wrapped["chdd"].evaluate(candidate, execution)
    wrapped["state_advancer"].advance("state", candidate, execution)

    why = [
        json.loads(line.removeprefix("::aios "))
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("::aios ") and '"why": true' in line
    ]
    assert why[0]["node"] == "selector"
    assert "1249.44" in why[0]["text"] and "Guardian" in why[0]["text"]


def test_agent_reasons_are_its_own_rationale_and_evidence() -> None:
    intent = SimpleNamespace(
        action="REDUCE_OFFTAKE",
        direction="DOWN",
        intensity="MODERATE",
        rationale_short="Высокая обводнённость при низкой эффективности по нефти.",
        evidence_ids=("diagnostic.sweep.2014-01-01",),
    )
    output = SimpleNamespace(
        primary_proposal=SimpleNamespace(
            intents=(intent,),
            provenance=SimpleNamespace(fallback_used=False),
            audit=SimpleNamespace(declined_alternatives=("INCREASE_OFFTAKE: цена воды",)),
        )
    )

    reasons = _proposal_reasons(output)

    assert reasons == [
        "почему (REDUCE_OFFTAKE): Высокая обводнённость при низкой эффективности по нефти.",
        "опирается на: diagnostic.sweep.2014-01-01",
        "рассмотрено и отклонено: INCREASE_OFFTAKE: цена воды",
    ]
