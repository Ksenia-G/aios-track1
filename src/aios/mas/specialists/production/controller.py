"""Production Controller facade for Hybrid MAS v1.1."""

from __future__ import annotations

from dataclasses import dataclass

from aios.evaluation.canonical import digest
from aios.mas.common import ProposalSource
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.proposals import (
    ProductionAgentOutput,
    ProductionProposal,
    ProposalAudit,
    ProposalProvenance,
)

from .baseline import BaselinePolicy
from .compiler import ProposalCompiler
from .coverage import evaluate_coverage, validate_declarations
from .errors import (
    ProductionControllerError,
    ProposalCompilationError,
    StrictOutputError,
    VocabularyValidationError,
)
from .evidence import EvidenceBuilder
from .grounding import GroundingAuditor
from .parser import StrictOutputParser
from .prompt import PromptBuilder
from .provider import ProductionLLMProvider, ProviderResponse
from .validation import InputValidator
from .vocabulary import VocabularyValidator


@dataclass(frozen=True, slots=True)
class _ProviderAttempt:
    """Одна попытка обращения к провайдеру: промпт и ЕГО ответ.

    Провенанс записывается только из этой пары, поэтому промпт retry-попытки
    структурно не может быть записан вместе с ответом первой попытки. Если
    попытка не дошла до ответа, `response` остаётся None и в провенанс
    попадает промпт без response_digest -- а не чужой ответ.
    """

    prompt: str
    response: ProviderResponse | None = None


class ProductionController:
    """Generate structural production proposals with deterministic safety rails."""

    def __init__(
        self,
        *,
        llm_enabled: bool = False,
        provider: ProductionLLMProvider | None = None,
        input_validator: InputValidator | None = None,
        evidence_builder: EvidenceBuilder | None = None,
        baseline_policy: BaselinePolicy | None = None,
        prompt_builder: PromptBuilder | None = None,
        output_parser: StrictOutputParser | None = None,
        vocabulary_validator: VocabularyValidator | None = None,
        grounding_auditor: GroundingAuditor | None = None,
        proposal_compiler: ProposalCompiler | None = None,
    ) -> None:
        if not isinstance(llm_enabled, bool):
            raise ProductionControllerError("llm_enabled must be a boolean")
        self.llm_enabled = llm_enabled
        self.provider = provider
        self.input_validator = input_validator or InputValidator()
        self.evidence_builder = evidence_builder or EvidenceBuilder()
        self.baseline_policy = baseline_policy or BaselinePolicy()
        self.prompt_builder = prompt_builder or PromptBuilder(self.evidence_builder)
        self.output_parser = output_parser or StrictOutputParser()
        self.vocabulary_validator = vocabulary_validator or VocabularyValidator()
        self.grounding_auditor = grounding_auditor or GroundingAuditor()
        self.proposal_compiler = proposal_compiler or ProposalCompiler()

    def propose(self, envelope: ProductionEnvelope) -> ProductionAgentOutput:
        """Run the specialist pipeline; invalid inputs fail, invalid LLM output falls back."""

        self.input_validator.validate(envelope)
        evidence = self.evidence_builder.build(envelope)
        baseline = self.baseline_policy.build(envelope)

        if not self.llm_enabled:
            return self._output_with_fallback(envelope, baseline, reason="llm_disabled")
        if self.provider is None:
            return self._output_with_fallback(envelope, baseline, reason="provider_unavailable")

        attempt = _ProviderAttempt(self.prompt_builder.build(envelope))
        try:
            response = self.provider.complete(attempt.prompt)
            if not isinstance(response, ProviderResponse):
                raise ProductionControllerError("provider returned an unsupported response type")
        except Exception:  # noqa: BLE001 - fail closed at the external provider boundary
            return self._output_with_fallback(
                envelope, baseline, reason="provider_error", attempt=attempt
            )
        attempt = _ProviderAttempt(attempt.prompt, response)

        try:
            parsed = self.output_parser.parse_beam(response.text)
        except StrictOutputError:
            # Новая попытка -- новая пара. Ответ первой попытки сюда не переносится.
            attempt = _ProviderAttempt(self.prompt_builder.build_schema_retry(envelope))
            try:
                retry_response = self.provider.complete(attempt.prompt)
                if not isinstance(retry_response, ProviderResponse):
                    raise ProductionControllerError(
                        "provider returned an unsupported response type"
                    )
            except Exception:  # noqa: BLE001 - fail closed at the retry provider boundary
                return self._output_with_fallback(
                    envelope, baseline, reason="provider_error", attempt=attempt
                )
            attempt = _ProviderAttempt(attempt.prompt, retry_response)
            try:
                parsed = self.output_parser.parse_beam(retry_response.text)
            except StrictOutputError:
                return self._output_with_fallback(
                    envelope, baseline, reason="strict_schema_failed", attempt=attempt
                )
            response = retry_response

        provenance = self._provenance(attempt)
        audit = ProposalAudit(
            latency_ms=response.latency_ms,
            declined_alternatives=tuple(
                item.label() for item in parsed.considered if not item.supported
            ),
        )
        seen: set[str] = set()
        first_failure: str | None = None

        def _compile_group(groups) -> list[ProductionProposal]:
            nonlocal first_failure
            out: list[ProductionProposal] = []
            for intents in groups:
                proposal, failure = self._compile_hypothesis(
                    envelope, evidence, intents, response.source, provenance, audit
                )
                if proposal is None:
                    first_failure = first_failure or failure
                    continue
                digest_ = proposal.digest()
                if digest_ in seen:
                    continue
                seen.add(digest_)
                out.append(proposal)
            return out

        compiled = _compile_group(parsed.hypotheses)
        considered = _compile_group(parsed.considered_alternatives)
        if not compiled:
            return self._output_with_fallback(
                envelope, baseline, reason=first_failure or "vocabulary_failed", attempt=attempt
            )

        # Полнота структурного покрытия. Пробел здесь -- это не «почти
        # валидный» ответ: допустимое направление осталось и не рассмотренным,
        # и не отклонённым с причиной, поэтому утверждение о превосходстве
        # противоположного не опровергнуто, а просто не проверялось. Закрыть
        # пробел за модель детерминированный слой не имеет права.
        preferred_intents = [i for p in compiled for i in p.intents]
        considered_intents = [i for p in considered for i in p.intents]
        _declarations, declaration_errors = validate_declarations(
            envelope,
            parsed_declarations=parsed.coverage_declarations,
            preferred_intents=preferred_intents,
            considered_intents=considered_intents,
        )
        if declaration_errors:
            return self._output_with_fallback(
                envelope,
                baseline,
                reason=f"coverage_declarations_invalid:{';'.join(declaration_errors)}"[:240],
                attempt=attempt,
            )
        # Полнота доказана слоем заявлений: на каждую требуемую ячейку ровно
        # одно заявление, и каждое сверено с реально написанной структурой или
        # с тем, что система знает наверняка. Второй, независимый вывод
        # полноты здесь только разошёлся бы с первым -- отчёт ниже остаётся
        # наблюдаемостью, а не воротами.
        coverage = evaluate_coverage(
            envelope,
            preferred_intents=preferred_intents,
            considered_intents=considered_intents,
            declared_reasons={
                str(item.cell.action): str(item.decline_reason)
                for item in _declarations
                if item.decline_reason is not None
            },
        )
        if coverage.gaps and not _declarations:
            gaps = ",".join(gap.cell.key() for gap in coverage.gaps)
            return self._output_with_fallback(
                envelope,
                baseline,
                reason=f"structural_coverage_incomplete:{gaps}"[:240],
                attempt=attempt,
            )

        return ProductionAgentOutput(
            primary_proposal=compiled[0],
            baseline_proposal=baseline,
            alternatives=tuple(compiled[1:]),
            considered_alternatives=tuple(considered),
        )

    def _compile_hypothesis(
        self,
        envelope: ProductionEnvelope,
        evidence: object,
        intents: tuple,
        source: ProposalSource,
        provenance: ProposalProvenance,
        audit: ProposalAudit,
    ) -> tuple[ProductionProposal | None, str | None]:
        """Одна гипотеза пучка, либо None с причиной.

        Каждая гипотеза проверяется НЕЗАВИСИМО: негодная альтернатива не имеет
        права уносить с собой годных соседей -- ровно ради этого пучок и нужен.
        """
        try:
            validated = self.vocabulary_validator.validate(intents, envelope)
            grounding_report = self.grounding_auditor.audit(validated, evidence)
            return (
                self.proposal_compiler.compile(
                    envelope=envelope,
                    source=source,
                    intents=validated,
                    grounding_report=grounding_report,
                    provenance=provenance,
                    audit=audit,
                ),
                None,
            )
        except VocabularyValidationError:
            return None, "vocabulary_failed"
        except ProposalCompilationError:
            return None, "grounding_failed"
        except ProductionControllerError:
            return None, "proposal_validation_failed"

    def _provenance(
        self, attempt: _ProviderAttempt, *, reason: str | None = None
    ) -> ProposalProvenance:
        """Провенанс ровно одной попытки: промпт и ответ ТОЙ ЖЕ попытки."""
        response = attempt.response
        return ProposalProvenance(
            prompt_builder_version=self.prompt_builder.version,
            prompt_digest=digest(attempt.prompt),
            model_id=response.model_id if response is not None else None,
            response_digest=digest(response.text) if response is not None else None,
            fallback_used=reason is not None,
            fallback_reason=reason,
        )

    def _output_with_fallback(
        self,
        envelope: ProductionEnvelope,
        baseline: ProductionProposal,
        *,
        reason: str,
        attempt: _ProviderAttempt | None = None,
    ) -> ProductionAgentOutput:
        primary = self.proposal_compiler.deterministic_fallback(
            envelope=envelope,
            baseline=baseline,
            reason=reason,
            prompt_builder_version=(self.prompt_builder.version if attempt is not None else None),
            prompt_digest=digest(attempt.prompt) if attempt is not None else None,
            model_id=(
                attempt.response.model_id
                if attempt is not None and attempt.response is not None
                else None
            ),
            response_digest=(
                digest(attempt.response.text)
                if attempt is not None and attempt.response is not None
                else None
            ),
        )
        return ProductionAgentOutput(primary_proposal=primary, baseline_proposal=baseline)


# Role-language alias for integration code that calls specialists "agents".
ProductionAgent = ProductionController
