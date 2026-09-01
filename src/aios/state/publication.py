"""Deterministic StateCard artifact publication."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from aios.state.state_card import StateCard

STATE_CARD_ARTIFACT_ROOT = "artifacts"
STATE_CARD_ARTIFACT_DIR = "artifacts/state"


def state_card_filename(card: StateCard) -> str:
    """Return the stable filename for one StateCard date."""

    stamp = card.metadata.date.isoformat().replace("-", "_")
    return f"state_card_{stamp}.json"


def state_card_artifact_payload(
    card: StateCard, *, source_artifacts: Iterable[str] = ()
) -> dict[str, Any]:
    """Build the publication wrapper around canonical StateCard JSON."""

    return {
        "state_card": card.to_json(),
        "canonical_digest": card.digest(),
        "provenance": {
            "case_id": card.metadata.case_id,
            "run_id": card.metadata.run_id,
            "schedule_hash": card.metadata.schedule_hash,
            "simulator_digest": card.metadata.simulator_digest,
            "source_summary_identity": card.metadata.source_summary_identity,
            "extractor_version": card.metadata.extractor_version,
            "source_artifacts": list(dict.fromkeys(source_artifacts)),
        },
    }


def publish_state_card(
    card: StateCard, *, output_dir: Path, source_artifacts: Iterable[str] = ()
) -> Path:
    """Write a deterministic StateCard artifact under `artifacts/state`."""

    target = output_dir / STATE_CARD_ARTIFACT_DIR / state_card_filename(card)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            state_card_artifact_payload(card, source_artifacts=source_artifacts),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return target
