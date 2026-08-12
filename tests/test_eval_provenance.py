"""Тесты защиты от подмены синтетического NPV официальным.

Это самая опасная ошибка этапа: синтетическое число, попавшее в отчёт без
пометки происхождения, невозможно отличить от официальной метрики трека.
"""

from datetime import UTC, datetime

import pytest
from conftest import SRC_ROOT, FakeNpvEvaluator

import aios.evaluation as evaluation_package
from aios.evaluation.npv import EvaluatorProvenance, NpvEstimate, NpvEvaluator, NpvStatus
from aios.evaluation.records import RunRecord, RunStatus
from aios.evaluation.simulation import SimulationRequest, SimulationStatus
from aios.evaluation.store import ExperimentDB

FORBIDDEN_SOURCE_TOKENS = ("FakeNpvEvaluator", "FailingNpvEvaluator", "conftest")


def test_default_provenance_is_not_official() -> None:
    estimate = NpvEstimate(value=1.0, evaluator_id="e", evaluator_version="1")
    assert estimate.provenance is EvaluatorProvenance.NOT_OFFICIAL
    assert estimate.is_official is False


def test_official_provenance_requires_explicit_argument() -> None:
    estimate = NpvEstimate(
        value=1.0,
        evaluator_id="e",
        evaluator_version="1",
        provenance=EvaluatorProvenance.OFFICIAL,
    )
    assert estimate.is_official is True


def test_provenance_string_literal_is_rejected() -> None:
    with pytest.raises(TypeError):
        NpvEstimate(
            value=1.0,
            evaluator_id="e",
            evaluator_version="1",
            provenance="OFFICIAL",  # type: ignore[arg-type]
        )


def test_provenance_is_always_serialized() -> None:
    estimate = NpvEstimate(value=1.0, evaluator_id="e", evaluator_version="1")
    payload = estimate.to_json()
    assert payload["provenance"] == "NOT_OFFICIAL"
    # Единицы измерения не угадываются на этом этапе.
    assert payload["unit"] is None


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_npv_is_rejected(bad_value: float) -> None:
    with pytest.raises(ValueError):
        NpvEstimate(value=bad_value, evaluator_id="e", evaluator_version="1")


def test_fake_evaluator_is_always_not_official(
    make_harness, sample_request: SimulationRequest
) -> None:
    harness = make_harness(npv_evaluator=FakeNpvEvaluator())
    outcome = harness.engine.evaluate(sample_request)
    assert outcome.npv_estimate is not None
    assert outcome.npv_estimate.provenance is EvaluatorProvenance.NOT_OFFICIAL
    assert outcome.run_record.npv_provenance is EvaluatorProvenance.NOT_OFFICIAL


def test_fake_evaluator_satisfies_protocol() -> None:
    assert isinstance(FakeNpvEvaluator(), NpvEvaluator)


def test_provenance_survives_database_roundtrip(experiment_db: ExperimentDB) -> None:
    record = RunRecord(
        run_id="run-provenance",
        created_at=datetime.now(UTC),
        simulation_key="e" * 64,
        simulation_request_digest="f" * 64,
        simulator_id="test-simulator",
        simulator_version="0.1.0",
        status=RunStatus.COMPLETED,
        simulation_status=SimulationStatus.SUCCESS,
        npv_status=NpvStatus.OK,
        npv_value=42.0,
        npv_provenance=EvaluatorProvenance.NOT_OFFICIAL,
    )
    experiment_db.record_run(record)
    restored = experiment_db.get_run("run-provenance")
    assert restored is not None
    assert restored.npv_provenance is EvaluatorProvenance.NOT_OFFICIAL


def test_npv_value_cannot_be_stored_without_provenance() -> None:
    with pytest.raises(ValueError):
        RunRecord(
            run_id="run-bad",
            created_at=datetime.now(UTC),
            simulation_key="e" * 64,
            simulation_request_digest="f" * 64,
            simulator_id="test-simulator",
            simulator_version="0.1.0",
            status=RunStatus.COMPLETED,
            npv_value=1.0,
        )


def test_fake_evaluator_is_not_exported_from_package() -> None:
    exported = set(evaluation_package.__all__)
    assert not {name for name in exported if "Fake" in name and "Npv" in name}
    assert not hasattr(evaluation_package, "FakeNpvEvaluator")


def test_source_tree_does_not_reference_test_fakes() -> None:
    """В src/ не должно быть ни одного упоминания тестового эвалюатора."""
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_SOURCE_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(SRC_ROOT)}: {token}")
    assert offenders == []


def test_source_tree_declares_fake_simulator_as_not_official() -> None:
    """Фейковый симулятор допустим в src/, но обязан быть помечен."""
    from aios.evaluation.fake_simulator import FAKE_SIMULATOR_ID

    assert "NOT_OFFICIAL" in FAKE_SIMULATOR_ID
