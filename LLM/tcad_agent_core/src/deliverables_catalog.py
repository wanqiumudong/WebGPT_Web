from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ARCHIVE_DIRNAME = "archive_git_recovery_20260307"
MANUAL_RELATIVE_PATH = Path("snapshot_manual_full_suite_v2/manual_full_suite_run_v2/QA_CATALOG_V2.json")
CURRENT_CASE_RELATIVE_PATH = Path("deliverables/current/RUN_1772343388_NMOS_E2E_DIRECT_V2")


def resolve_archive_root(repo_root: Path) -> Path:
    archive_root = repo_root / "deliverables" / ARCHIVE_DIRNAME
    if not archive_root.exists():
        raise FileNotFoundError(f"Missing archive root: {archive_root}")
    return archive_root


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Top-level JSON must be an object: {path}")
    return payload



EXCLUDED_ARCHIVAL_SNAPSHOTS = [
    "snapshot_86f83e6_lessons_tutorial/LESSIONS_TASK_CATALOG.json",
    "snapshot_86f83e6_lessons_tutorial/tutorial_suite/TUTORIAL_TASK_CONSOLIDATED_STATUS.md",
]


def build_manual_catalog(repo_root: Path) -> dict[str, Any]:
    source_path = resolve_archive_root(repo_root) / MANUAL_RELATIVE_PATH
    payload = _read_json(source_path)
    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        tasks = []
    normalized = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        normalized.append(
            {
                "task_id": task.get("task_id") or task.get("id", ""),
                "suite": task.get("suite", ""),
                "topic": task.get("topic", ""),
                "prompt": task.get("user_prompt", ""),
                "expected_tools": task.get("expected_tools", []),
                "expected_outputs": task.get("expected_outputs", []),
                "pass_criteria": task.get("pass_criteria", []),
                "source_snapshot": str(source_path),
            }
        )
    return {
        "catalog": "manual_full_suite_v2_recovered",
        "source_snapshot": str(source_path),
        "task_count": len(normalized),
        "tasks": normalized,
    }


def build_current_main_case(repo_root: Path) -> dict[str, Any]:
    case_root = repo_root / CURRENT_CASE_RELATIVE_PATH
    agent_output_path = case_root / "agent_output.json"
    payload = _read_json(agent_output_path)
    artifacts_dir = case_root / "artifacts"
    artifact_files = sorted(path.name for path in artifacts_dir.glob("*") if path.is_file())
    spec = payload.get("spec", {}) if isinstance(payload.get("spec"), dict) else {}
    return {
        "case_id": case_root.name,
        "source_group": "current_main_case",
        "source_path": str(case_root),
        "prompt": spec.get("requirement", ""),
        "simulation_type": spec.get("simulation_type", ""),
        "device_type": spec.get("device_type", ""),
        "stage": payload.get("stage", ""),
        "artifact_files": artifact_files,
    }


def build_active_manifest(repo_root: Path) -> dict[str, Any]:
    manual_catalog = build_manual_catalog(repo_root)
    manual_cases = []
    for item in manual_catalog["tasks"]:
        manual_cases.append(
            {
                "case_id": item["task_id"],
                "source_group": "manual_full_suite_v2",
                "source_snapshot": item["source_snapshot"],
                "prompt": item["prompt"],
                "expect": item["expected_outputs"],
                "expected_tools": item["expected_tools"],
                "pass_criteria": item["pass_criteria"],
            }
        )

    cases = [build_current_main_case(repo_root), *manual_cases]
    return {
        "manifest": "active_case_manifest_v1",
        "description": "Current execution entry for deliverables-focused regression and future agent optimization.",
        "total_cases": len(cases),
        "excluded_archival_snapshots": list(EXCLUDED_ARCHIVAL_SNAPSHOTS),
        "cases": cases,
    }


def build_catalog_bundle(repo_root: Path) -> dict[str, dict[str, Any]]:
    return {
        "manual_full_suite_v2_recovered.json": build_manual_catalog(repo_root),
        "active_case_manifest_v1.json": build_active_manifest(repo_root),
    }


def build_catalogs_readme(bundle: dict[str, dict[str, Any]], repo_root: Path) -> str:
    manual = bundle["manual_full_suite_v2_recovered.json"]
    active = bundle["active_case_manifest_v1.json"]
    lines = [
        "# deliverables/catalogs",
        "",
        "当前目录是从历史快照中整理出的**当前可执行案例入口层**。",
        "",
        "## 目录角色",
        "",
        "- `archive_git_recovery_20260307/`：历史快照原件。",
        "- `current/`：当前主案例与最新交付快照。",
        "- `catalogs/`：当前真正建议拿来执行的案例入口。",
        "",
        "## 当前保留的 catalog",
        "",
        f"- `manual_full_suite_v2_recovered.json`：{manual['task_count']} 个 QA 任务（当前主回归集）。",
        f"- `active_case_manifest_v1.json`：{active['total_cases']} 个当前优先案例（主案例 + manual QA）。",
        "",
        "## 为什么不保留 lessons/tutorial recovered catalog",
        "",
        "lessons 与 tutorial 的历史快照中，包含大量需要 Agent 直接阅读教程/PDF 的任务定义。",
        "这类任务不适合作为当前可执行入口，因此仅保留在 archive 中做历史参考，不进入 catalogs。",
        "",
        "## 当前建议使用顺序",
        "",
        "1. 先跑 `active_case_manifest_v1.json`。",
        "2. 再扩展到 `manual_full_suite_v2_recovered.json` 全量。",
        "3. 若要查看 lessons/tutorial，只去 `archive_git_recovery_20260307/` 追溯，不作为当前运行入口。",
        "",
        "## 数据来源",
        "",
        f"- manual QA: `{Path(manual['source_snapshot']).relative_to(repo_root)}`",
        f"- current main case: `{CURRENT_CASE_RELATIVE_PATH}`",
        "",
        "说明：本目录只整理入口，不篡改历史快照正文。",
    ]
    return "\n".join(lines) + "\n"


def write_catalog_bundle(repo_root: Path, out_dir: Path | None = None) -> list[Path]:
    repo_root = repo_root.resolve()
    target_dir = out_dir.resolve() if out_dir is not None else repo_root / "deliverables" / "catalogs"
    target_dir.mkdir(parents=True, exist_ok=True)
    bundle = build_catalog_bundle(repo_root)
    written: list[Path] = []
    for name, payload in bundle.items():
        path = target_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    readme_path = target_dir / "README.md"
    readme_path.write_text(build_catalogs_readme(bundle, repo_root), encoding="utf-8")
    written.append(readme_path)
    return written
