"""Выписать обоснования агентов из замороженного прогона MAS в данные страницы.

Артефакт прогона (`benchmark_summary.json`) лежит вне репозитория: он тяжёлый и
не входит в пакет сабмишена. Страница показывает обоснования финального прогона,
поэтому они переносятся в репозиторий отдельным файлом -- дословно, без
пересказа. Скрипт переносит текст как есть и рядом кладёт происхождение: сумму
артефакта и дайджесты, которыми он привязан к опубликованному пакету.

    python scripts/extract_frozen_rationale.py \
        --artifact <...>/kt3-final-value-run/benchmark_summary.json \
        --out src/aios/ui/frozen_rationale.json

Повторный запуск на том же артефакте даёт тот же файл.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "aios.ui.frozen_rationale/1.0.0"
VARIANT = "B4_MAS_ON"
SPECIALISTS = ("production", "injection")


def _records(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Намерения одного специалиста с непустым `rationale_short`."""
    out: list[dict[str, Any]] = []
    for slot in ("primary", "alternatives"):
        items = block.get(slot)
        if items is None:
            continue
        proposals = items if isinstance(items, list) else [items]
        for proposal in proposals:
            for intent in proposal.get("intents") or []:
                rationale = (intent.get("rationale_short") or "").strip()
                if not rationale:
                    continue
                out.append(
                    {
                        "slot": "preferred" if slot == "primary" else "considered",
                        "action": intent.get("action"),
                        "direction": intent.get("direction"),
                        "intensity": intent.get("intensity"),
                        "wells": list(intent.get("well_ids") or ()),
                        "scope": list(intent.get("scope") or intent.get("source_regions") or ()),
                        "evidence_ids": list(intent.get("evidence_ids") or ()),
                        "rationale": rationale,
                    }
                )
    return out


def build(artifact: Path, reproducibility: Path) -> dict[str, Any]:
    """Собрать данные страницы из артефакта прогона.

    Нужный прогон опознаётся не порядковым номером, а дайджестом
    закоммиченного кандидата из опубликованного пакета: в сводке лежат все
    варианты бенчмарка, и MAS OFF так же имеет свои обоснования.
    """
    published = json.loads(reproducibility.read_text(encoding="utf-8"))
    expected = published["committed_candidate_digest"]
    raw = artifact.read_bytes()
    summary = json.loads(raw)
    outcome = next(
        (
            item
            for item in summary["outcomes"]
            if item.get("extra", {}).get("committed_candidate_digest") == expected
        ),
        None,
    )
    if outcome is None:
        raise SystemExit(
            f"{artifact}: не найден прогон с закоммиченным кандидатом {expected}; "
            "артефакт не от опубликованного сабмишена"
        )
    extra = outcome["extra"]
    epochs = []
    for index, audit in enumerate(extra["epoch_audits"]):
        entry: dict[str, Any] = {
            "index": index,
            "date": audit.get("state_card_date") or audit.get("effective_date"),
        }
        for specialist in SPECIALISTS:
            entry[specialist] = _records(audit.get(specialist) or {})
        epochs.append(entry)
    return {
        "schema": SCHEMA,
        "variant": VARIANT,
        "note": (
            "Обоснования записаны самими специалистами в замороженном прогоне MAS. "
            "Текст перенесён дословно; ничего не досочинено и не пересказано."
        ),
        "source": {
            "artifact": artifact.name,
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "committed_candidate_digest": extra["committed_candidate_digest"],
            "manifest_digest": outcome["manifest_digest"],
            "binds_to": "submission_evidence/reproducibility.json",
        },
        "epochs": epochs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--reproducibility",
        type=Path,
        default=Path("submission_evidence/reproducibility.json"),
    )
    args = parser.parse_args()
    payload = build(args.artifact, args.reproducibility)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    total = sum(len(e[s]) for e in payload["epochs"] for s in SPECIALISTS)
    print(f"{args.out}: {total} обоснований из {len(payload['epochs'])} эпох")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
