"""Фасад Injection Controller: Agent[InjectionEnvelope, InjectionAgentOutput].

llm_enabled=False собирает primary как отдельный DETERMINISTIC_FALLBACK.
llm_enabled=True идёт через PromptBuilder -> provider -> StrictOutputParser.
Baseline -- всегда отдельный DETERMINISTIC_BASELINE, в промпт не попадает.

Schema/provider/grounding ошибка LLM-ветки допускает ровно один повтор
(LLM_SCHEMA_GROUNDING_RETRY_LIMIT). Второй отказ не уничтожает deterministic
floor: primary становится отдельным DETERMINISTIC_FALLBACK, baseline остаётся.
Частичный «почти валидный» LLM-proposal не принимается.
"""

from __future__ import annotations

from aios.evaluation.canonical import digest
from aios.mas.common import Direction, Intensity, ProposalSource, UncertaintyLevel
from aios.mas.envelopes import InjectionEnvelope
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.proposals import (
    InjectionAgentOutput,
    InjectionProposal,
    ProposalAudit,
    ProposalProvenance,
)
from aios.mas.specialists.injection.baseline_policy import BaselinePolicy
from aios.mas.specialists.injection.compiler import CompileError, ProposalCompiler
from aios.mas.specialists.injection.evidence_builder import EvidenceBuilder, EvidenceContext
from aios.mas.specialists.injection.grounding import GroundingAuditor, GroundingError
from aios.mas.specialists.injection.input_validator import InputValidator
from aios.mas.specialists.injection.parser import ParseError, StrictOutputParser
from aios.mas.specialists.injection.prompt import PromptBuilder
from aios.mas.specialists.injection.provider import (
    LlmProvider,
    LlmProviderError,
    read_provider_text,
)
from aios.mas.specialists.injection.scope import ScopeAuditor, ScopeError
from aios.mas.specialists.injection.vocabulary import VocabularyError, VocabularyValidator

LLM_SCHEMA_GROUNDING_RETRY_LIMIT = 1
_LLM_SOURCES = frozenset(
    {
        ProposalSource.LLM_LIVE,
        ProposalSource.LLM_LOCAL,
        ProposalSource.LLM_REPLAY,
    }
)
_RETRYABLE = (
    ParseError,
    VocabularyError,
    GroundingError,
    ScopeError,
    CompileError,
    LlmProviderError,
)


class InjectionAgentError(ValueError):
    """Injection Controller не может собрать вход; floor при LLM-сбое сохраняется."""


class InjectionController:
    """Specialist facade. propose() принимает только envelope, без baseline."""

    def __init__(
        self,
        *,
        llm_enabled: bool = False,
        llm_provider: LlmProvider | None = None,
        llm_source: ProposalSource = ProposalSource.LLM_LOCAL,
        prompt_builder: PromptBuilder | None = None,
        output_parser: StrictOutputParser | None = None,
    ) -> None:
        self._llm_enabled = llm_enabled
        self._llm_provider = llm_provider
        self._llm_source = ProposalSource(llm_source)
        self._prompt_builder = prompt_builder if prompt_builder is not None else PromptBuilder()
        self._parser = output_parser if output_parser is not None else StrictOutputParser()
        self._input_validator = InputValidator()
        self._evidence_builder = EvidenceBuilder()
        self._baseline_policy = BaselinePolicy()
        self._vocabulary = VocabularyValidator()
        self._scope = ScopeAuditor()
        self._grounding = GroundingAuditor()
        self._compiler = ProposalCompiler()

    def propose(self, envelope: InjectionEnvelope) -> InjectionAgentOutput:
        validated = self._input_validator.validate(envelope)
        evidence = self._evidence_builder.build(validated)
        baseline = self._baseline_policy.propose(validated)
        if self._llm_enabled:
            primary, alternatives = self._compile_llm_beam_or_floor(validated, evidence)
        else:
            primary, alternatives = self._compile_deterministic_fallback(validated, evidence), ()
        return InjectionAgentOutput(
            primary_proposal=primary,
            baseline_proposal=baseline,
            alternatives=alternatives,
        )

    def _compile_deterministic_fallback(
        self,
        envelope: InjectionEnvelope,
        evidence: EvidenceContext,
        *,
        fallback_reason: str | None = None,
    ) -> InjectionProposal:
        intents = self._vocabulary.validate_intents(
            (_fallback_hold_intent(),),
            allowed_actions=envelope.allowed_actions,
        )
        intents = self._scope.audit(intents, envelope)
        report = self._grounding.audit(intents, evidence)
        provenance = ProposalProvenance()
        if fallback_reason is not None:
            provenance = ProposalProvenance(
                fallback_used=True,
                fallback_reason=fallback_reason[:240],
            )
        return self._compiler.compile(
            envelope,
            intents=intents,
            grounding_report=report,
            source=ProposalSource.DETERMINISTIC_FALLBACK,
            provenance=provenance,
        )

    def _compile_llm_beam_or_floor(
        self,
        envelope: InjectionEnvelope,
        evidence: EvidenceContext,
    ):
        """Пучок структурных гипотез из ОДНОГО ответа провайдера.

        Негодная гипотеза отбрасывается поодиночке: одна неудачная
        альтернатива не имеет права уносить с собой годные -- именно ради
        этого пучок и вводился. Пустой результат остаётся fail-closed и
        падает на детерминированный пол.
        """
        if self._llm_provider is None:
            return (
                self._compile_deterministic_fallback(
                    envelope,
                    evidence,
                    fallback_reason="llm_enabled=True requires llm_provider",
                ),
                (),
            )
        if self._llm_source not in _LLM_SOURCES:
            return (
                self._compile_deterministic_fallback(
                    envelope,
                    evidence,
                    fallback_reason="llm_source must be an LLM ProposalSource",
                ),
                (),
            )
        prompt = self._prompt_builder.build(envelope)
        attempts = LLM_SCHEMA_GROUNDING_RETRY_LIMIT + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                last_response = read_provider_text(self._llm_provider, prompt)
                beam = self._parser.parse_beam(last_response)
            except _RETRYABLE as exc:
                last_error = exc
                continue

            provenance = ProposalProvenance(
                prompt_builder_version=getattr(self._prompt_builder, "version", None),
                prompt_digest=digest(prompt),
                model_id=getattr(self._llm_provider, "model_id", None),
                # Общий на весь пучок: это происхождение ОДНОГО завершения.
                response_digest=digest(last_response),
                fallback_used=False,
            )
            audit = ProposalAudit(
                declined_alternatives=tuple(
                    item.label() for item in beam.considered if not item.supported
                )
            )
            compiled: list[InjectionProposal] = []
            seen: set[str] = set()
            for intents in beam.hypotheses:
                proposal = self._compile_hypothesis(envelope, evidence, intents, provenance, audit)
                if proposal is None:
                    continue
                digest_ = proposal.digest()
                if digest_ in seen:
                    continue
                seen.add(digest_)
                compiled.append(proposal)
            if compiled:
                return compiled[0], tuple(compiled[1:])
            last_error = last_error or ScopeError("no hypothesis in the beam was groundable")

        reason = f"LLM proposal rejected after {attempts} schema/grounding attempts"
        if last_error is not None:
            reason = f"{reason}: {last_error}"
        return (
            self._compile_deterministic_fallback(envelope, evidence, fallback_reason=reason),
            (),
        )

    def _compile_hypothesis(
        self,
        envelope: InjectionEnvelope,
        evidence: EvidenceContext,
        intents,
        provenance: ProposalProvenance,
        audit: ProposalAudit,
    ):
        """Одна гипотеза пучка, либо None если она не заземляется.

        Заземляемость проверяется по каждой гипотезе НЕЗАВИСИМО: негодная
        альтернатива не имеет права уносить годных соседей.
        """
        try:
            validated = self._vocabulary.validate_intents(
                intents, allowed_actions=envelope.allowed_actions
            )
            validated = self._scope.audit(validated, envelope)
            report = self._grounding.audit(validated, evidence)
            return self._compiler.compile(
                envelope,
                intents=validated,
                grounding_report=report,
                source=self._llm_source,
                provenance=provenance,
                audit=audit,
            )
        except _RETRYABLE:
            return None


def _fallback_hold_intent() -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )
