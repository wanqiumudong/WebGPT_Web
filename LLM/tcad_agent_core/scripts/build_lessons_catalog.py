#!/usr/bin/env python3
from __future__ import annotations

"""扫描 tutorials/lessions，生成可执行任务目录与矩阵。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path('/data/yphu/TCAD_Agent/code')
LESSONS_ROOT = Path('/data/yphu/TCAD_Agent/doc/tutorials/lessions')
DELIVERABLES = ROOT / 'deliverables'
OUT_JSON = DELIVERABLES / 'LESSIONS_TASK_CATALOG.json'
OUT_MD = DELIVERABLES / 'LESSIONS_TASK_MATRIX.md'
OUT_INDEX = DELIVERABLES / 'LESSIONS_FILE_INDEX.txt'

DAY_RE = re.compile(r'DAY\s*([0-9]{1,2})', re.IGNORECASE)


@dataclass
class DayInfo:
    day: int
    pdfs: list[Path]
    cmd_sde: list[Path]
    cmd_sdevice: list[Path]
    cmd_inspect: list[Path]
    tcl_svisual: list[Path]


def _find_day(path: Path) -> int | None:
    for part in [path.name, *path.parts[::-1]]:
        m = DAY_RE.search(part)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
    return None


def _read_text_safe(path: Path, max_chars: int = 180_000) -> str:
    try:
        txt = path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return ''
    return txt[:max_chars]


def _classify_cmd(path: Path, text: str) -> str:
    low = text.lower()
    if 'sdegeo:' in low or 'sdedr:' in low or '(sde:' in low:
        return 'sde'
    if 'solve {' in low or 'electrode{' in low or 'electrode {' in low:
        return 'sdevice'
    if 'cv_create' in low or 'proj_load' in low or 'extract' in low:
        return 'inspect'
    # 文件名弱规则兜底
    name = path.name.lower()
    if 'inspect' in name:
        return 'inspect'
    if '_des' in name:
        return 'sdevice'
    if '_dvs' in name or 'sde' in name:
        return 'sde'
    return 'unknown'


def _collect_days() -> dict[int, DayInfo]:
    data: dict[int, DayInfo] = {}

    for pdf in sorted(LESSONS_ROOT.rglob('*.pdf')):
        day = _find_day(pdf)
        if day is None:
            continue
        if day not in data:
            data[day] = DayInfo(day, [], [], [], [], [])
        data[day].pdfs.append(pdf)

    for cmd in sorted(LESSONS_ROOT.rglob('*.cmd')):
        day = _find_day(cmd)
        if day is None:
            continue
        if day not in data:
            data[day] = DayInfo(day, [], [], [], [], [])
        cls = _classify_cmd(cmd, _read_text_safe(cmd))
        if cls == 'sde':
            data[day].cmd_sde.append(cmd)
        elif cls == 'sdevice':
            data[day].cmd_sdevice.append(cmd)
        elif cls == 'inspect':
            data[day].cmd_inspect.append(cmd)

    for tcl in sorted(LESSONS_ROOT.rglob('*.tcl')):
        day = _find_day(tcl)
        if day is None:
            continue
        if day not in data:
            data[day] = DayInfo(day, [], [], [], [], [])
        txt = _read_text_safe(tcl).lower()
        if any(k in txt for k in ['create_plot', 'create_curve', 'load_file', 'export_view', 'export_variables']):
            data[day].tcl_svisual.append(tcl)

    return dict(sorted(data.items(), key=lambda kv: kv[0]))


def _rel(p: Path) -> str:
    return str(p.relative_to(LESSONS_ROOT))


def _pick(paths: list[Path], n: int = 3) -> list[str]:
    return [_rel(p) for p in paths[:n]]


def _make_task(task_id: str, day: int, kind: str, expect: str, prompt: str, refs: list[str]) -> dict:
    return {
        'task_id': task_id,
        'day': day,
        'kind': kind,
        'expect': expect,
        'prompt': prompt,
        'refs': refs,
    }


def build_catalog() -> dict:
    days = _collect_days()
    tasks: list[dict] = []

    for day, info in days.items():
        # 理论总结任务（每个 DAY 一条）
        pdf_refs = _pick(info.pdfs, 2)
        if pdf_refs:
            tasks.append(
                _make_task(
                    task_id=f'DAY{day:02d}_SUMMARY',
                    day=day,
                    kind='summary',
                    expect='text',
                    prompt=(
                        f'请阅读并总结 lessons 的 DAY{day} 课程目标与关键步骤，'
                        '特别说明本课涉及的 Sentaurus 工具、输入文件与输出结果。'
                        '仅文字总结，不执行工具。'
                    ),
                    refs=pdf_refs,
                )
            )

        # 结构任务
        if info.cmd_sde:
            refs = _pick(info.cmd_sde, 3)
            tasks.append(
                _make_task(
                    task_id=f'DAY{day:02d}_SDE',
                    day=day,
                    kind='sde',
                    expect='structure_png',
                    prompt=(
                        f'请参考 lessons DAY{day} 的工程风格，生成并运行一个与该课主题一致的 SDE 结构，'
                        '完成语法检查与运行，并用 svisual 导出结构 PNG。'
                        '请尽量贴近该课程示例的器件类型与层次。'
                    ),
                    refs=refs,
                )
            )

        # 电学仿真任务
        if info.cmd_sdevice:
            refs = _pick(info.cmd_sdevice, 3)
            tasks.append(
                _make_task(
                    task_id=f'DAY{day:02d}_SDEVICE',
                    day=day,
                    kind='sdevice',
                    expect='curve_png',
                    prompt=(
                        f'请参考 lessons DAY{day} 的工程风格，在完成结构后生成并运行 SDevice 仿真，'
                        '导出主要电学曲线 PNG（如 IdVg/IdVd/CV/AC 之一），并给出关键指标。'
                    ),
                    refs=refs,
                )
            )

        # SVisual 后处理任务
        if info.tcl_svisual:
            refs = _pick(info.tcl_svisual, 2)
            tasks.append(
                _make_task(
                    task_id=f'DAY{day:02d}_SVISUAL',
                    day=day,
                    kind='svisual',
                    expect='curve_png',
                    prompt=(
                        f'请参考 lessons DAY{day} 的可视化脚本风格，使用 svisual 对已有结果做后处理，'
                        '导出曲线 PNG 和可读文本数据。'
                    ),
                    refs=refs,
                )
            )

        # Inspect 提参任务
        if info.cmd_inspect:
            refs = _pick(info.cmd_inspect, 2)
            tasks.append(
                _make_task(
                    task_id=f'DAY{day:02d}_INSPECT',
                    day=day,
                    kind='inspect',
                    expect='text',
                    prompt=(
                        f'请参考 lessons DAY{day} 的 Inspect 工作流，基于仿真结果进行参数提取，'
                        '输出提取到的关键量及其物理含义。'
                    ),
                    refs=refs,
                )
            )

    return {
        'lessons_root': str(LESSONS_ROOT),
        'days': {
            f'DAY{day:02d}': {
                'pdf_count': len(info.pdfs),
                'sde_cmd_count': len(info.cmd_sde),
                'sdevice_cmd_count': len(info.cmd_sdevice),
                'inspect_cmd_count': len(info.cmd_inspect),
                'svisual_tcl_count': len(info.tcl_svisual),
                'sample_pdf': _pick(info.pdfs, 2),
                'sample_sde': _pick(info.cmd_sde, 2),
                'sample_sdevice': _pick(info.cmd_sdevice, 2),
                'sample_inspect': _pick(info.cmd_inspect, 2),
                'sample_svisual_tcl': _pick(info.tcl_svisual, 2),
            }
            for day, info in days.items()
        },
        'task_count': len(tasks),
        'tasks': tasks,
    }


def write_outputs(catalog: dict) -> None:
    DELIVERABLES.mkdir(parents=True, exist_ok=True)

    OUT_JSON.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')

    # 文件索引（便于人工检索）
    files = sorted(LESSONS_ROOT.rglob('*'))
    lines = [str(p) for p in files if p.is_file()]
    OUT_INDEX.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    md: list[str] = []
    md.append('# LESSONS TASK MATRIX')
    md.append('')
    md.append(f"来源目录：`{LESSONS_ROOT}`")
    md.append(f"任务总数：`{catalog['task_count']}`")
    md.append('')
    md.append('## DAY 资源统计')
    for day_name, info in catalog['days'].items():
        md.append(
            f"- {day_name}: pdf={info['pdf_count']}, sde_cmd={info['sde_cmd_count']}, "
            f"sdevice_cmd={info['sdevice_cmd_count']}, inspect_cmd={info['inspect_cmd_count']}, svisual_tcl={info['svisual_tcl_count']}"
        )
    md.append('')
    md.append('## 自动化任务清单（用于自然语言驱动验证）')
    for t in catalog['tasks']:
        refs = ', '.join(t['refs']) if t['refs'] else '(无)'
        md.append(f"- [{t['task_id']}] kind={t['kind']}, expect={t['expect']}, refs={refs}")
    md.append('')
    md.append('## 状态约定')
    md.append('- TODO：未运行')
    md.append('- RUNNING：运行中')
    md.append('- PASS：通过')
    md.append('- FAIL：失败，需修复框架/MCP/prompt')
    md.append('')
    OUT_MD.write_text('\n'.join(md) + '\n', encoding='utf-8')


def main() -> int:
    catalog = build_catalog()
    write_outputs(catalog)
    print(json.dumps({'task_count': catalog['task_count'], 'out_json': str(OUT_JSON), 'out_md': str(OUT_MD)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
