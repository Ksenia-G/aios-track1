"""Провенанс исполнения рантайма.

Каждый прогон сопровождается записью, по которой результат можно проследить
до кандидата, расписания и способа исполнения. Провенанс -- не украшение:
restart-based оценка запрещено выдавать за окончательный конкурсный NPV, а
значит, режим исполнения обязан быть явным полем записи, а не сноской в логе.

Здесь нет времени стенки и hostname: они различаются между машинами при
физически одинаковом прогоне и потому живут в diagnostics результата, а не в
провенансе.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aios.evaluation.canonical import JsonValue
from aios.evaluation.simulation import SimulatorIdentity
from aios.execution.compiler import CompiledCandidateSchedule
from aios.mas.magnitude import MagnitudeCandidate


class ExecutionMode(StrEnum):
    """Способ исполнения.

    FULL -- чистый прогон всего горизонта; только он является авторитетным
    источником заявляемого результата. RESTART -- продолжение с сохранённого
    состояния; ускорение поиска, требующее reconciliation с чистым прогоном.
    """

    FULL = "FULL"
    RESTART = "RESTART"


@dataclass(frozen=True, slots=True)
class RuntimeProvenance:
    """След одного исполнения кандидата через runtime-зону."""

    execution_mode: ExecutionMode
    simulation_key: str
    candidate_digest: str
    candidate_kind: str
    bundle_digest: str
    resolver_version: str
    compiler_id: str
    compiler_version: str
    schedule_sha256: str
    baseline_control_digest: str
    action_control_digest: str
    simulator_identity_json: dict[str, JsonValue]
    restart_spec_json: JsonValue | None = None
    reconciliation_json: JsonValue | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "execution_mode": str(self.execution_mode),
            "simulation_key": self.simulation_key,
            "candidate_digest": self.candidate_digest,
            "candidate_kind": self.candidate_kind,
            "bundle_digest": self.bundle_digest,
            "resolver_version": self.resolver_version,
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "schedule_sha256": self.schedule_sha256,
            "baseline_control_digest": self.baseline_control_digest,
            "action_control_digest": self.action_control_digest,
            "simulator_identity": self.simulator_identity_json,
            "restart_spec": self.restart_spec_json,
            "reconciliation": self.reconciliation_json,
        }

    def with_restart_details(
        self,
        restart_spec_json: JsonValue,
        reconciliation_json: JsonValue,
    ) -> RuntimeProvenance:
        """Копия провенанса со спецификацией и сверкой restart-исполнения."""
        return RuntimeProvenance(
            execution_mode=self.execution_mode,
            simulation_key=self.simulation_key,
            candidate_digest=self.candidate_digest,
            candidate_kind=self.candidate_kind,
            bundle_digest=self.bundle_digest,
            resolver_version=self.resolver_version,
            compiler_id=self.compiler_id,
            compiler_version=self.compiler_version,
            schedule_sha256=self.schedule_sha256,
            baseline_control_digest=self.baseline_control_digest,
            action_control_digest=self.action_control_digest,
            simulator_identity_json=self.simulator_identity_json,
            restart_spec_json=restart_spec_json,
            reconciliation_json=reconciliation_json,
        )

    @classmethod
    def from_compilation(
        cls,
        *,
        mode: ExecutionMode,
        simulation_key: str,
        compilation: CompiledCandidateSchedule,
        candidate: MagnitudeCandidate,
        simulator_identity: SimulatorIdentity,
    ) -> RuntimeProvenance:
        """Собрать провенанс из результата компиляции и frozen-контракта."""
        return cls(
            execution_mode=mode,
            simulation_key=simulation_key,
            candidate_digest=compilation.candidate_digest,
            candidate_kind=str(candidate.canonical().candidate_kind),
            bundle_digest=candidate.bundle_digest,
            resolver_version=candidate.resolver_version,
            compiler_id=compilation.compiler_id,
            compiler_version=compilation.compiler_version,
            schedule_sha256=compilation.schedule_sha256,
            baseline_control_digest=compilation.baseline_control_digest,
            action_control_digest=compilation.action_control_digest,
            simulator_identity_json=simulator_identity.to_json(),
        )
