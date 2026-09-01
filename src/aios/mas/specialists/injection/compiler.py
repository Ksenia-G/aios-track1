"""Сборка primary InjectionProposal.

ProposalCompiler не ставит setpoints и не строит baseline: baseline уже
собрал BaselinePolicy. Сюда приходят только проверенные intents и
GroundingReport. Невалидный набор отклоняется целиком.
"""

from __future__ import annotations

from collections.abc import Sequence

from aios.mas.common import MAS_CONTRACT_SCHEMA_VERSION, ProposalSource
from aios.mas.envelopes import InjectionEnvelope
from aios.mas.intents import InjectionIntent
from aios.mas.proposals import (
    GroundingReport,
    InjectionProposal,
    ProposalAudit,
    ProposalError,
    ProposalProvenance,
)


class CompileError(ValueError):
    """Primary proposal собрать нельзя."""


class ProposalCompiler:
    """Собрать primary InjectionProposal. All-or-nothing, без baseline."""

    def compile(
        self,
        envelope: InjectionEnvelope,
        *,
        intents: Sequence[InjectionIntent],
        grounding_report: GroundingReport,
        source: ProposalSource,
        provenance: ProposalProvenance | None = None,
        audit: ProposalAudit | None = None,
    ) -> InjectionProposal:
        if not intents:
            raise CompileError("intents must not be empty")
        try:
            resolved_source = ProposalSource(source)
        except ValueError as exc:
            raise CompileError(f"source {source!r} is not a frozen ProposalSource") from exc
        if resolved_source is ProposalSource.DETERMINISTIC_BASELINE:
            raise CompileError("primary must not use DETERMINISTIC_BASELINE")
        if not grounding_report.passed:
            raise CompileError(
                "proposal is not grounded, unknown_evidence_ids="
                f"{grounding_report.unknown_evidence_ids}"
            )
        known = frozenset(grounding_report.grounded_evidence_ids)
        for intent in intents:
            for evidence_id in intent.evidence_ids:
                if evidence_id not in known:
                    raise CompileError(f"intent cites ungrounded evidence_id {evidence_id!r}")
        try:
            return InjectionProposal(
                schema_version=MAS_CONTRACT_SCHEMA_VERSION,
                envelope_digest=envelope.digest(),
                source=resolved_source,
                intents=tuple(intents),
                grounding_report=grounding_report,
                provenance=provenance if provenance is not None else ProposalProvenance(),
                audit=audit if audit is not None else ProposalAudit(),
            )
        except ProposalError as exc:
            raise CompileError(str(exc)) from exc
