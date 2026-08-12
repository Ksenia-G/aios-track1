"""Тесты канонизации и ключа симуляции.

Golden-хеши здесь -- не формальность: их изменение означает, что весь ранее
накопленный кеш стал недействительным, и это обязано быть осознанным шагом
с инкрементом KEY_SCHEMA_VERSION.
"""

import subprocess
import sys

import pytest
from conftest import SRC_ROOT

from aios.evaluation.canonical import (
    KEY_SCHEMA_VERSION,
    CanonicalizationError,
    canonical_json,
    digest,
    digest_bytes,
)
from aios.evaluation.simulation import (
    SimulationRequest,
    SimulatorIdentity,
    compute_request_digest,
    compute_simulation_key,
)

GOLDEN_PAYLOAD = {"alpha": 1, "beta": [1, 2.5, None, True], "gamma": {"b": "значение", "a": -0.0}}
GOLDEN_CANONICAL = '{"alpha":1,"beta":[1,2.5,null,true],"gamma":{"a":0.0,"b":"значение"}}'
GOLDEN_DIGEST = "36c6d4c7165bc37d9d0b00035b95164483de42a42ac0cedcdf098edfd01d6178"

GOLDEN_REQUEST = SimulationRequest(
    payload={"a": 1, "b": [1, 2]},
    seed=7,
    model_digest="m0",
)
GOLDEN_IDENTITY = SimulatorIdentity(
    simulator_id="sim",
    simulator_version="1.0",
    configuration={"x": 1},
)
GOLDEN_SIMULATION_KEY = "29ec9aec92978124bbabb429e76ca21ab48a1e624af969812adde70984f0ee55"


def test_canonical_json_golden_string() -> None:
    assert canonical_json(GOLDEN_PAYLOAD) == GOLDEN_CANONICAL


def test_digest_golden_hash() -> None:
    assert digest(GOLDEN_PAYLOAD) == GOLDEN_DIGEST


def test_key_schema_version_is_pinned() -> None:
    assert KEY_SCHEMA_VERSION == 1


def test_digest_is_independent_of_key_insertion_order() -> None:
    first = {"a": 1, "b": {"x": 1, "y": 2}}
    second = {"b": {"y": 2, "x": 1}, "a": 1}
    assert digest(first) == digest(second)


@pytest.mark.parametrize(
    "mutated",
    [
        {"alpha": 2, "beta": [1, 2.5, None, True], "gamma": {"b": "значение", "a": -0.0}},
        {"alpha": 1, "beta": [1, 2.5, None, False], "gamma": {"b": "значение", "a": -0.0}},
        {"alpha": 1, "beta": [1, 2.5, None, True], "gamma": {"b": "другое", "a": -0.0}},
        {"alpha": 1, "beta": [2.5, 1, None, True], "gamma": {"b": "значение", "a": -0.0}},
        {"alpha": 1, "beta": [1, 2.5, None, True], "gamma": {"b": "значение", "a": 1.0}},
        {"alpha": 1, "beta": [1, 2.5, None, True]},
    ],
)
def test_meaningful_mutation_changes_digest(mutated: dict) -> None:
    assert digest(mutated) != GOLDEN_DIGEST


def test_nan_is_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        digest({"value": float("nan")})


def test_positive_infinity_is_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        digest({"value": float("inf")})


def test_negative_infinity_is_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        digest({"value": float("-inf")})


def test_negative_zero_is_normalized() -> None:
    assert digest({"value": -0.0}) == digest({"value": 0.0})
    assert canonical_json({"value": -0.0}) == '{"value":0.0}'


@pytest.mark.parametrize("value", [(1, 2), {1, 2}, object(), b"bytes"])
def test_non_json_types_are_rejected(value: object) -> None:
    with pytest.raises(CanonicalizationError):
        digest({"value": value})


def test_non_string_mapping_key_is_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        digest({1: "value"})


def test_digest_bytes_matches_known_sha256() -> None:
    # sha256(b"") -- общеизвестное значение, ловит подмену алгоритма.
    assert digest_bytes(b"") == ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


@pytest.mark.parametrize("hash_seed", ["0", "1", "random"])
def test_digest_is_stable_across_processes(hash_seed: str) -> None:
    """Ключ не должен зависеть от PYTHONHASHSEED и от конкретного процесса."""
    program = (
        "import sys;"
        f"sys.path.insert(0, {str(SRC_ROOT)!r});"
        "from aios.evaluation.canonical import digest;"
        "print(digest({'alpha': 1, 'beta': [1, 2.5, None, True],"
        " 'gamma': {'b': 'значение', 'a': -0.0}}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": hash_seed, "PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"},
    )
    assert completed.stdout.strip() == GOLDEN_DIGEST


def test_simulation_key_golden_hash() -> None:
    assert compute_simulation_key(GOLDEN_REQUEST, GOLDEN_IDENTITY) == GOLDEN_SIMULATION_KEY


def test_request_digest_differs_from_simulation_key() -> None:
    # Дайджест запроса не зависит от симулятора, ключ симуляции -- зависит.
    assert compute_request_digest(GOLDEN_REQUEST) != GOLDEN_SIMULATION_KEY


@pytest.mark.parametrize(
    ("request_kwargs", "identity_kwargs"),
    [
        ({"payload": {"a": 2, "b": [1, 2]}}, {}),
        ({"seed": 8}, {}),
        ({"seed": None}, {}),
        ({"model_digest": "m1"}, {}),
        ({"scenario_digest": "s1"}, {}),
        ({"schedule_digest": "w1"}, {}),
        ({}, {"simulator_id": "other-sim"}),
        ({}, {"simulator_version": "1.1"}),
        ({}, {"configuration": {"x": 2}}),
    ],
)
def test_simulation_key_changes_with_meaningful_field(
    request_kwargs: dict, identity_kwargs: dict
) -> None:
    request = SimulationRequest(
        payload=request_kwargs.get("payload", GOLDEN_REQUEST.payload),
        seed=request_kwargs.get("seed", GOLDEN_REQUEST.seed),
        model_digest=request_kwargs.get("model_digest", GOLDEN_REQUEST.model_digest),
        scenario_digest=request_kwargs.get("scenario_digest", GOLDEN_REQUEST.scenario_digest),
        schedule_digest=request_kwargs.get("schedule_digest", GOLDEN_REQUEST.schedule_digest),
    )
    identity = SimulatorIdentity(
        simulator_id=identity_kwargs.get("simulator_id", GOLDEN_IDENTITY.simulator_id),
        simulator_version=identity_kwargs.get(
            "simulator_version", GOLDEN_IDENTITY.simulator_version
        ),
        configuration=identity_kwargs.get("configuration", GOLDEN_IDENTITY.configuration),
    )
    assert compute_simulation_key(request, identity) != GOLDEN_SIMULATION_KEY


def test_simulation_key_ignores_payload_key_order() -> None:
    reordered = SimulationRequest(
        payload={"b": [1, 2], "a": 1},
        seed=GOLDEN_REQUEST.seed,
        model_digest=GOLDEN_REQUEST.model_digest,
    )
    assert compute_simulation_key(reordered, GOLDEN_IDENTITY) == GOLDEN_SIMULATION_KEY
