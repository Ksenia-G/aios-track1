"""EXP-001: контролируемый экран перераспределения закачки."""

from aios.experiments.blocks import (
    BLOCKS_BY_ID,
    SCREENING_BLOCKS,
    ScreeningBlock,
    block_of_injector,
    screening_blocks_with_redistribution,
)
from aios.experiments.gates import ExperimentGateReport, validate_scenario
from aios.experiments.metrics import RunMetrics, collect_metrics, compare_metrics
from aios.experiments.runner import ScenarioResult, build_candidate_schedule, run_scenario
from aios.experiments.scenarios import (
    FIRST_PASS_FRACTION,
    REFINEMENT_FRACTIONS,
    Scenario,
    baseline_scenario,
    first_pass_scenarios,
    fraction_tag,
    make_scenario,
    refinement_scenarios,
    scenario_id,
)
from aios.experiments.sweep import (
    BaselineMismatchError,
    ScenarioRow,
    best_per_block,
    build_row,
    build_summary,
    rank_rows,
    run_baseline,
    run_scenarios,
    write_results_csv,
)
from aios.experiments.transfer import (
    InjectionTransfer,
    InjectionTransferPolicy,
    TransferError,
    baseline_pair_targets,
)

__all__ = [
    "BLOCKS_BY_ID",
    "FIRST_PASS_FRACTION",
    "REFINEMENT_FRACTIONS",
    "SCREENING_BLOCKS",
    "BaselineMismatchError",
    "ExperimentGateReport",
    "InjectionTransfer",
    "InjectionTransferPolicy",
    "RunMetrics",
    "Scenario",
    "ScenarioResult",
    "ScenarioRow",
    "ScreeningBlock",
    "TransferError",
    "baseline_pair_targets",
    "baseline_scenario",
    "best_per_block",
    "block_of_injector",
    "build_candidate_schedule",
    "build_row",
    "build_summary",
    "collect_metrics",
    "compare_metrics",
    "first_pass_scenarios",
    "fraction_tag",
    "make_scenario",
    "rank_rows",
    "refinement_scenarios",
    "run_baseline",
    "run_scenario",
    "run_scenarios",
    "scenario_id",
    "screening_blocks_with_redistribution",
    "validate_scenario",
    "write_results_csv",
]
