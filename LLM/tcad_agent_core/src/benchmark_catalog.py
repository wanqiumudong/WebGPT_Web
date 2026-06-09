from __future__ import annotations

"""教育 benchmark 目录运行时加载。"""

import json
from collections import Counter
from pathlib import Path
from typing import Any


ARCHIVE_DIRNAME = "archive_git_recovery_20260307"
LESSONS_RELATIVE_PATH = Path("snapshot_86f83e6_lessons_tutorial/LESSIONS_TASK_CATALOG.json")
TUTORIAL_RELATIVE_PATH = Path(
    "snapshot_86f83e6_lessons_tutorial/tutorial_suite/TUTORIAL_TASK_CONSOLIDATED_STATUS.md"
)
MANUAL_RELATIVE_PATH = Path("snapshot_manual_full_suite_v2/manual_full_suite_run_v2/QA_CATALOG_V2.json")


def load_education_benchmark(workspace: Path) -> dict[str, Any]:
    """加载恢复的 lessons/tutorial/manual benchmark 摘要。

    参数:
        workspace: 仓库根目录，或直接指向 archive_git_recovery_20260307 的目录。
    """

    archive_root = _resolve_archive_root(workspace)
    lessons_path = archive_root / LESSONS_RELATIVE_PATH
    tutorial_path = archive_root / TUTORIAL_RELATIVE_PATH
    manual_path = archive_root / MANUAL_RELATIVE_PATH

    errors: dict[str, str] = {}
    missing_sources: list[str] = []

    lessons_summary = _load_lessons_summary(lessons_path, errors)
    tutorial_summary = _load_tutorial_summary(tutorial_path, errors)
    manual_summary = _load_manual_summary(manual_path, errors)

    for name, path in (
        ("lessons_catalog", lessons_path),
        ("tutorial_status", tutorial_path),
        ("manual_full_suite_v2", manual_path),
    ):
        if not path.exists():
            missing_sources.append(name)

    return {
        "benchmark": "education",
        "workspace": str(workspace.resolve()),
        "archive_root": str(archive_root),
        "sources": {
            "archive_root": _build_source_entry(archive_root),
            "lessons_catalog": _build_source_entry(lessons_path),
            "tutorial_status": _build_source_entry(tutorial_path),
            "manual_full_suite_v2": _build_source_entry(manual_path),
        },
        "lessons": lessons_summary,
        "tutorial": tutorial_summary,
        "manual_full_suite_v2": manual_summary,
        "missing_sources": missing_sources,
        "errors": errors,
    }


def _resolve_archive_root(workspace: Path) -> Path:
    resolved = workspace.resolve()
    candidates = (
        resolved / "deliverables" / ARCHIVE_DIRNAME,
        resolved / ARCHIVE_DIRNAME,
        resolved,
    )
    for candidate in candidates:
        if candidate.name == ARCHIVE_DIRNAME and candidate.exists():
            return candidate
        if (candidate / LESSONS_RELATIVE_PATH).exists() or (candidate / MANUAL_RELATIVE_PATH).exists():
            return candidate
    return candidates[0]


def _build_source_entry(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists()}


def _load_lessons_summary(path: Path, errors: dict[str, str]) -> dict[str, Any]:
    payload = _load_json_file(path, "lessons_catalog", errors)
    if payload is None:
        return {"available": False, "task_count": 0, "day_count": 0, "lessons_root": None}

    days = payload.get("days", {})
    day_count = len(days) if isinstance(days, dict) else 0
    return {
        "available": True,
        "task_count": _read_task_count(payload),
        "day_count": day_count,
        "lessons_root": payload.get("lessons_root"),
    }


def _load_tutorial_summary(path: Path, errors: dict[str, str]) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "task_count": 0, "status_counts": {}}

    try:
        rows = _parse_tutorial_status_table(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as exc:  # pragma: no cover - 极端容错兜底
        errors["tutorial_status"] = str(exc)
        return {"available": False, "task_count": 0, "status_counts": {}}

    counts = Counter(row["status"] for row in rows if row["status"])
    return {
        "available": True,
        "task_count": len(rows),
        "status_counts": dict(counts),
    }


def _load_manual_summary(path: Path, errors: dict[str, str]) -> dict[str, Any]:
    payload = _load_json_file(path, "manual_full_suite_v2", errors)
    if payload is None:
        return {"available": False, "task_count": 0, "version": None, "created_at": None}

    return {
        "available": True,
        "task_count": _read_task_count(payload),
        "version": payload.get("version"),
        "created_at": payload.get("created_at"),
    }


def _load_json_file(path: Path, source_name: str, errors: dict[str, str]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors[source_name] = str(exc)
        return None
    if not isinstance(payload, dict):
        errors[source_name] = "Top-level payload must be a JSON object."
        return None
    return payload


def _read_task_count(payload: dict[str, Any]) -> int:
    task_count = payload.get("task_count")
    if isinstance(task_count, int):
        return task_count
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        return len(tasks)
    return 0


def _parse_tutorial_status_table(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        columns = [part.strip() for part in stripped.strip("|").split("|")]
        if len(columns) < 4:
            continue
        if columns[0].lower() == "task" and columns[1].lower() == "status":
            continue
        if all(set(column) <= {"-", ":"} for column in columns[:4]):
            continue
        rows.append(
            {
                "task": columns[0],
                "status": columns[1],
                "best_evidence": columns[2],
                "notes": columns[3],
            }
        )
    return rows
