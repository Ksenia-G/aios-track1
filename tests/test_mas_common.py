"""Frozen enum vocabulary shared by the Hybrid MAS v1.1 contract layer."""

from __future__ import annotations

import pytest

from aios.mas.common import (
    MAS_CONTRACT_SCHEMA_VERSION,
    AgentRole,
    CandidateKind,
    DecisionRound,
    Direction,
    Intensity,
    NetTotalEffect,
    ProposalSource,
    UncertaintyLevel,
)


def test_schema_version_is_stable_identifier() -> None:
    assert isinstance(MAS_CONTRACT_SCHEMA_VERSION, str)
    assert MAS_CONTRACT_SCHEMA_VERSION


def test_agent_role_members() -> None:
    assert {member.value for member in AgentRole} == {"PRODUCTION", "INJECTION", "ARBITER"}


def test_decision_round_members() -> None:
    assert {member.value for member in DecisionRound} == {"ROUND_1", "ROUND_2"}


def test_direction_members() -> None:
    assert {member.value for member in Direction} == {"UP", "DOWN", "NONE"}


def test_intensity_members() -> None:
    assert {member.value for member in Intensity} == {"NONE", "SMALL", "MODERATE", "AGGRESSIVE"}


def test_uncertainty_level_members() -> None:
    assert {member.value for member in UncertaintyLevel} == {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}


def test_proposal_source_members() -> None:
    assert {member.value for member in ProposalSource} == {
        "DETERMINISTIC_BASELINE",
        "LLM_LIVE",
        "LLM_LOCAL",
        "LLM_REPLAY",
        "DETERMINISTIC_FALLBACK",
    }


def test_candidate_kind_members() -> None:
    assert {member.value for member in CandidateKind} == {
        "HOLD",
        "DETERMINISTIC_BASELINE",
        "AGENT_BUNDLE",
    }


def test_net_total_effect_members() -> None:
    assert {member.value for member in NetTotalEffect} == {
        "ZERO",
        "INCREASE",
        "DECREASE",
        "DIRECTIONAL",
    }


def test_intensity_has_no_numeric_magnitude_mapping() -> None:
    """Ни один enum в этом слое не должен нести числовое значение."""
    for member in Intensity:
        with pytest.raises(ValueError):
            float(member)  # type: ignore[arg-type]
