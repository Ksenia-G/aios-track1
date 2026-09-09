"""Переносимость рабочей копии OPM: каталог вывода обязан быть писабельным
для пользователя контейнера.

OPM-образ запускает `flow` от внутреннего пользователя `opm` (uid 1001),
который не совпадает с uid хоста. Docker монтирует рабочую копию как есть,
поэтому каталог `OUT`, созданный на хосте, должен быть открыт на запись для
«остальных» -- иначе `flow` завершается с `Failed opening file
/data/OUT/...PRT for StreamLog` и весь прогон выглядит как упавший, хотя колода
исправна. Это гарантируется явным `chmod 0o777`, а не наследованием umask, чтобы
прогон не зависел от раскладки пользователей на машине (в т.ч. организатора).
"""

from __future__ import annotations

import os
from pathlib import Path

from aios.cases import Case
from aios.opm.workspace import (
    OUTPUT_DIRNAME,
    _strip_tnav_dead_code,
    prepare_workspace,
)
from aios.pipeline import build_schedule_for_case
from aios.schedule.policy import DEFAULT_POLICY_ID


def test_output_dir_is_world_writable_for_container_user(mini_case: Case, tmp_path: Path) -> None:
    _schedule, _state, schedule_text, _history_text = build_schedule_for_case(
        mini_case, policy_id=DEFAULT_POLICY_ID
    )

    workspace = prepare_workspace(
        mini_case,
        root=tmp_path / "workspace",
        history_text="",
        schedule_text=schedule_text,
    )

    output_dir: Path = workspace.output_dir
    assert output_dir.name == OUTPUT_DIRNAME
    assert not (output_dir / ".aios-container-probe").exists()

    # «Остальные» (другие пользователи) должны иметь право записи независимо
    # от umask хоста: иначе контейнерный `opm` (uid 1001) не создаст PRT.
    mode = os.stat(output_dir).st_mode & 0o777
    assert mode & 0o002, f"OUT dir is not world-writable (mode {oct(mode)})"

    # Проба письма от чужого uid не нужна (требует прав root), но проверим,
    # что сам процесс владельца способен писать -- базовый инвариант.
    probe = output_dir / "host-write-probe.txt"
    probe.write_text("ok", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "ok"


def test_tnav_if_zero_blocks_are_removed_from_workspace_data(
    mini_case: Case,
    tmp_path: Path,
) -> None:
    """Регрессия портируемости: tNavigator ``#if 0 ... #endif`` не попадает
    в рабочую копию, иначе OPM падает на `String #if 0 ... not formatted`.
    """
    # Внести блок ровно так, как он стоит в экспортированной Model Y.
    deck = mini_case.history.data_path
    original = deck.read_text(encoding="utf-8")
    tnav = "\n#if 0 // tNavigator keyword\nREPORTSCREEN\nCONN         ERR\n/\n#endif\n"
    deck.write_text(original + tnav, encoding="utf-8")

    _schedule, _state, schedule_text, _history_text = build_schedule_for_case(
        mini_case, policy_id=DEFAULT_POLICY_ID
    )
    workspace = prepare_workspace(
        mini_case,
        root=tmp_path / "workspace",
        history_text="",
        schedule_text=schedule_text,
    )

    published = workspace.data_file.read_text(encoding="utf-8")
    assert "#if 0" not in published
    assert "#endif" not in published
    assert "REPORTSCREEN" not in published


def test_strip_tnav_dead_code_keeps_active_lines() -> None:
    text = (
        "RUNSPEC\n"
        "#if 0 // tNavigator keyword\n"
        "REPORTSCREEN\n"
        "CONN ERR\n"
        "/\n"
        "#endif\n"
        "DIMENS\n"
        "49 47 141 /\n"
    )
    cleaned = _strip_tnav_dead_code(text)
    assert "#if 0" not in cleaned
    assert "#endif" not in cleaned
    assert "REPORTSCREEN" not in cleaned
    assert "DIMENS" in cleaned
    assert "49 47 141 /" in cleaned
