#!/usr/bin/env python3
from __future__ import annotations

"""通过自然语言驱动 main.py 执行 lessons 任务目录。"""

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path('/data/yphu/TCAD_Agent/code')
DELIVERABLES = ROOT / 'deliverables'
CATALOG_JSON = DELIVERABLES / 'LESSIONS_TASK_CATALOG.json'
REPORT_BASE = DELIVERABLES / 'lessons_suite'


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    objs: list[dict[str, Any]] = []
    in_str = False
    esc = False
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    frag = text[start : i + 1]
                    try:
                        obj = json.loads(frag)
                        if isinstance(obj, dict):
                            objs.append(obj)
                    except Exception:
                        pass
                    start = -1
    return objs


def run_prompt(prompt: str, out_dir: Path, timeout_s: int) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    input_text = '/clean\n' + prompt + '\n/exit\n'
    proc = subprocess.run(
        ['python3', 'main.py'],
        cwd=ROOT,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    (out_dir / 'stdout.txt').write_text(proc.stdout, encoding='utf-8', errors='ignore')
    (out_dir / 'stderr.txt').write_text(proc.stderr, encoding='utf-8', errors='ignore')

    objs = extract_json_objects(proc.stdout)
    result: dict[str, Any] = {}
    for obj in reversed(objs):
        if 'stage' in obj or 'session_id' in obj:
            result = obj
            break
    return {
        'return_code': proc.returncode,
        'json_count': len(objs),
        'result': result,
    }


def pick_png(result_obj: dict[str, Any]) -> str:
    arts = result_obj.get('artifacts', {}) if isinstance(result_obj, dict) else {}
    if not isinstance(arts, dict):
        return ''
    for k in ['svisual_png', 'svisual_cutline_png']:
        v = str(arts.get(k, '')).strip()
        if v:
            return v
    return ''


def task_success(expect: str, stage: str, result_obj: dict[str, Any], png_path: str) -> bool:
    if expect == 'text':
        reply = str(result_obj.get('assistant_reply', '')).strip()
        return bool(reply) or stage in {'no_session', 'created', 'tdr_inspected', 'sdevice_done', 'svisual_done', 'validated'}

    if expect == 'structure_png':
        return stage in {'svisual_sde_done', 'tdr_inspected', 'svisual_done', 'validated'} and Path(png_path).exists()

    if expect == 'curve_png':
        return stage in {'svisual_done', 'validated'} and Path(png_path).exists()

    return stage not in {'', 'no_session'}


def load_catalog() -> dict[str, Any]:
    if not CATALOG_JSON.exists():
        raise FileNotFoundError(f'Missing catalog: {CATALOG_JSON}. Run scripts/build_lessons_catalog.py first.')
    return json.loads(CATALOG_JSON.read_text(encoding='utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser(description='Run lessons natural-language suite against TCAD Agent')
    parser.add_argument('--max-tasks', type=int, default=0, help='0 means all')
    parser.add_argument('--task-id', action='append', default=[], help='only run specific task ids')
    parser.add_argument('--day', action='append', default=[], help='only run tasks in specific DAY number, e.g. --day 4 --day 17')
    parser.add_argument('--timeout-s', type=int, default=2400, help='per-task timeout seconds')
    parser.add_argument('--report-dir', default='', help='output root dir; default is code/deliverables/lessons_suite')
    args = parser.parse_args()

    catalog = load_catalog()
    tasks = list(catalog.get('tasks', []))

    if args.task_id:
        wanted = {x.strip() for x in args.task_id if x.strip()}
        tasks = [t for t in tasks if str(t.get('task_id', '')).strip() in wanted]

    if args.day:
        day_set = set()
        for d in args.day:
            try:
                day_set.add(int(d))
            except Exception:
                pass
        tasks = [t for t in tasks if int(t.get('day', -1)) in day_set]

    if args.max_tasks > 0:
        tasks = tasks[: args.max_tasks]

    report_base = Path(args.report_dir).resolve() if args.report_dir else REPORT_BASE
    report_base.mkdir(parents=True, exist_ok=True)
    run_root = report_base / f'run_{int(time.time())}'
    run_root.mkdir(parents=True, exist_ok=True)

    plan_lines = [
        '# Lessons Suite Plan',
        '',
        '执行约束：',
        '- 仅通过自然语言驱动 `python3 main.py`。',
        '- 每个任务前执行 `/clean`，隔离上下文。',
        '- 失败任务保留 stdout/stderr 与 summary，供修复回灌。',
        '',
        '任务列表：',
    ]
    for i, t in enumerate(tasks, start=1):
        plan_lines.append(f"{i}. {t.get('task_id')} (DAY{int(t.get('day', -1)):02d}) kind={t.get('kind')} expect={t.get('expect')}")
    (run_root / 'PLAN.md').write_text('\n'.join(plan_lines) + '\n', encoding='utf-8')

    results: list[dict[str, Any]] = []
    for i, task in enumerate(tasks, start=1):
        task_id = str(task.get('task_id', f'TASK_{i}'))
        print(f'[{i}/{len(tasks)}] {task_id}', flush=True)
        t_dir = run_root / task_id
        out = run_prompt(str(task.get('prompt', '')), t_dir, timeout_s=max(120, int(args.timeout_s)))
        result_obj = out.get('result', {})
        stage = str(result_obj.get('stage', '')) if isinstance(result_obj, dict) else ''
        png_path = pick_png(result_obj)
        success = task_success(str(task.get('expect', '')), stage, result_obj if isinstance(result_obj, dict) else {}, png_path)

        item = {
            'task_id': task_id,
            'day': task.get('day'),
            'kind': task.get('kind'),
            'expect': task.get('expect'),
            'prompt': task.get('prompt'),
            'refs': task.get('refs', []),
            'return_code': out['return_code'],
            'json_count': out['json_count'],
            'stage': stage,
            'generated_png': png_path,
            'generated_png_exists': bool(png_path and Path(png_path).exists()),
            'success': success,
        }
        (t_dir / 'summary.json').write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
        results.append(item)

    passed = sum(1 for x in results if x['success'])
    summary = {
        'total': len(results),
        'passed': passed,
        'failed': len(results) - passed,
        'pass_rate': round(passed / len(results), 4) if results else 0.0,
        'results': results,
    }
    out_json = run_root / 'lessons_suite_report.json'
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    md = [
        '# Lessons Suite Report',
        '',
        f"- 总任务数: {summary['total']}",
        f"- 通过: {summary['passed']}",
        f"- 失败: {summary['failed']}",
        f"- 通过率: {summary['pass_rate']:.2%}",
        '',
        '## 逐任务结果',
    ]
    for r in results:
        md.append(f"- {r['task_id']}: success={r['success']}, stage={r['stage']}, png={r['generated_png_exists']}")
    (run_root / 'FINAL_REPORT.md').write_text('\n'.join(md) + '\n', encoding='utf-8')

    print(json.dumps({'report': str(out_json), 'total': summary['total'], 'passed': summary['passed'], 'failed': summary['failed']}, ensure_ascii=False))
    return 0 if summary['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
