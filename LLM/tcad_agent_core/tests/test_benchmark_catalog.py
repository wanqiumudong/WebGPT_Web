from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

from src.benchmark_catalog import load_education_benchmark


def _build_archive_tree(workspace: Path) -> Path:
    archive_root = workspace / "deliverables" / "archive_git_recovery_20260307"
    lessons_root = archive_root / "snapshot_86f83e6_lessons_tutorial"
    tutorial_root = lessons_root / "tutorial_suite"
    manual_root = archive_root / "snapshot_manual_full_suite_v2" / "manual_full_suite_run_v2"

    tutorial_root.mkdir(parents=True, exist_ok=True)
    manual_root.mkdir(parents=True, exist_ok=True)

    (lessons_root / "LESSIONS_TASK_CATALOG.json").write_text(
        json.dumps(
            {
                "lessons_root": "/mock/lessons",
                "days": {"DAY01": {"pdf_count": 1}, "DAY02": {"pdf_count": 2}},
                "task_count": 5,
                "tasks": [{"task_id": "DAY01_SUMMARY"}, {"task_id": "DAY02_SDE"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (tutorial_root / "TUTORIAL_TASK_CONSOLIDATED_STATUS.md").write_text(
        "\n".join(
            [
                "# Tutorial Task Consolidated Status",
                "",
                "| Task | Status | Best Evidence | Notes |",
                "|---|---|---|---|",
                "| L01_summary | PASS | run_1 | ok |",
                "| L02_summary | FAIL | run_2 | bad |",
                "| L03_summary | PASS | run_3 | ok |",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (manual_root / "QA_CATALOG_V2.json").write_text(
        json.dumps(
            {
                "version": "v2",
                "created_at": "2026-02-28",
                "task_count": 4,
                "tasks": [{"task_id": "T01"}, {"task_id": "T02"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return archive_root


def test_load_education_benchmark_parses_archive_catalogs(tmp_path: Path):
    archive_root = _build_archive_tree(tmp_path)

    payload = load_education_benchmark(tmp_path)

    assert payload["archive_root"] == str(archive_root)
    assert payload["lessons"]["available"] is True
    assert payload["lessons"]["task_count"] == 5
    assert payload["lessons"]["day_count"] == 2
    assert payload["tutorial"]["available"] is True
    assert payload["tutorial"]["task_count"] == 3
    assert payload["tutorial"]["status_counts"] == {"PASS": 2, "FAIL": 1}
    assert payload["manual_full_suite_v2"]["available"] is True
    assert payload["manual_full_suite_v2"]["task_count"] == 4
    assert payload["sources"]["lessons_catalog"]["path"].endswith("LESSIONS_TASK_CATALOG.json")
    assert payload["missing_sources"] == []


def test_load_education_benchmark_handles_missing_or_broken_sources(tmp_path: Path):
    archive_root = tmp_path / "deliverables" / "archive_git_recovery_20260307"
    lessons_root = archive_root / "snapshot_86f83e6_lessons_tutorial"
    manual_root = archive_root / "snapshot_manual_full_suite_v2" / "manual_full_suite_run_v2"
    lessons_root.mkdir(parents=True, exist_ok=True)
    manual_root.mkdir(parents=True, exist_ok=True)

    (lessons_root / "LESSIONS_TASK_CATALOG.json").write_text("{broken", encoding="utf-8")

    payload = load_education_benchmark(tmp_path)

    assert payload["archive_root"] == str(archive_root)
    assert payload["lessons"]["available"] is False
    assert payload["lessons"]["task_count"] == 0
    assert payload["tutorial"]["available"] is False
    assert payload["tutorial"]["task_count"] == 0
    assert payload["tutorial"]["status_counts"] == {}
    assert payload["manual_full_suite_v2"]["available"] is False
    assert payload["manual_full_suite_v2"]["task_count"] == 0
    assert "lessons_catalog" in payload["errors"]
    assert "tutorial_status" in payload["missing_sources"]
    assert "manual_full_suite_v2" in payload["missing_sources"]
