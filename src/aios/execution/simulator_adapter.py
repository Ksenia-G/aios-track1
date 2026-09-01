"""OpmFlowSimulator -- адаптер официального симулятора под протокол Simulator.

Это единственная реализация ``aios.evaluation.Simulator`` поверх реального
движка: docker-образ из спецификации кейса, рабочая копия колоды через
``prepare_workspace``, запуск через существующий ``run_simulation``. Новый
протокол не изобретается и второй модели запуска не появляется.

Контракт результата:

* успех -- нормализованный физический документ сводки в артефакте;
  никаких NPV/ЧДД внутри (это authority ниже по конвейеру);
* провал симулятора -- типизированный CRASHED/TIMEOUT результат c
  ``SimulationFailure``; он никогда не превращается в успешный;
* невалидный запрос (нет текста расписания, расходится SHA) --
  INVALID_INPUT с внятной причиной;
* недиагностируемые отказы окружения (нет docker и т.п.) пробрасываются
  как исключение -- наверху решают, как их записать в историю.

Всё недетерминированное (wall time, полный лог, команда запуска) уходит в
diagnostics и не участвует в result_digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from aios.cases.contract import Case
from aios.evaluation.simulation import (
    SimulationFailure,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorIdentity,
)
from aios.execution.execution import (
    TRAJECTORY_FORMAT,
    normalized_summary_document,
    run_full_opm,
    write_trajectory_artifact,
)
from aios.opm.runner import OpmRunResult

OPM_SIMULATOR_ID = "aios.execution.opm-flow"
_SCHEDULE_PAYLOAD_KEY = "schedule_text"


@dataclass(frozen=True, slots=True)
class OpmFlowSimulator:
    """Реализация протокола Simulator поверх docker-прогона OPM Flow.

    ``history_text`` -- неизменяемый исторический префикс расписания кейса;
    он один на все прогоны кейса, поэтому вводится конструктором, а не
    таскается в каждом запросе.
    """

    case: Case
    history_text: str

    @property
    def identity(self) -> SimulatorIdentity:
        return SimulatorIdentity(
            simulator_id=OPM_SIMULATOR_ID,
            simulator_version=f"docker:{self.case.simulator.docker_image}",
            configuration={
                "flow_command": self.case.simulator.flow_command,
                "extra_args": list(self.case.simulator.extra_args),
                "timeout_seconds": self.case.simulator.timeout_seconds,
            },
        )

    def _invalid_input(self, message: str, details: dict[str, object]) -> SimulationResult:
        return SimulationResult.create(
            status=SimulationStatus.INVALID_INPUT,
            failure=SimulationFailure(message=message, details=details),
        )

    def _failed_result(
        self, run_result: OpmRunResult, diagnostics: dict[str, object]
    ) -> SimulationResult:
        if run_result.exit_code == 124:
            status = SimulationStatus.TIMEOUT
        else:
            status = SimulationStatus.CRASHED
        return SimulationResult.create(
            status=status,
            failure=SimulationFailure(
                message="opm flow run did not complete successfully",
                details={
                    "exit_code": run_result.exit_code,
                    "errors": list(run_result.errors[:5]),
                    "report_steps": run_result.report_steps,
                },
            ),
            diagnostics=diagnostics,
        )

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        schedule_text = request.payload.get(_SCHEDULE_PAYLOAD_KEY)
        if not isinstance(schedule_text, str) or not schedule_text:
            return self._invalid_input(f"request payload is missing '{_SCHEDULE_PAYLOAD_KEY}'", {})
        if request.schedule_digest is not None:
            actual_sha256 = hashlib.sha256(schedule_text.encode("utf-8")).hexdigest()
            if actual_sha256 != request.schedule_digest:
                return self._invalid_input(
                    "schedule_text does not match request.schedule_digest",
                    {"expected": request.schedule_digest, "actual": actual_sha256},
                )

        opm_run = run_full_opm(
            self.case,
            root=Path(workspace) / "opm",
            history_text=self.history_text,
            schedule_text=schedule_text,
        )
        diagnostics = {
            "opm_run": opm_run.run_result.to_json(),
            "wall_seconds": round(opm_run.run_result.wall_seconds, 3),
        }
        if not opm_run.is_pass or opm_run.summary is None:
            return self._failed_result(opm_run.run_result, diagnostics)

        document = normalized_summary_document(opm_run.summary)
        trajectory_ref = write_trajectory_artifact(
            opm_run.summary,
            workspace_root=Path(workspace),
            producer_id=f"{OPM_SIMULATOR_ID}@{self.identity.simulator_version}",
        )
        dates = document["dates"]
        return SimulationResult.create(
            status=SimulationStatus.SUCCESS,
            trajectory_ref=trajectory_ref,
            metadata={
                "trajectory_format": TRAJECTORY_FORMAT,
                "report_steps": len(dates),
                "final_date": str(dates[-1]) if dates else "",
            },
            diagnostics=diagnostics,
        )


def decode_trajectory_artifact(data: bytes) -> dict:
    """Прочитать нормализованный документ из байтов артефакта.

    Отдельная функция вместо ``json.loads`` в каждом потребителе: формат
    документа проверяется здесь в одном месте.
    """
    document = json.loads(data)
    if not isinstance(document, dict):
        raise TypeError("trajectory artifact is not a JSON object")
    if document.get("format") != TRAJECTORY_FORMAT:
        raise ValueError(f"unexpected trajectory format: {document.get('format')!r}")
    return document
