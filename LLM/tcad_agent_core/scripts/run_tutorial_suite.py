#!/usr/bin/env python3
from __future__ import annotations

"""通过自然语言驱动 main.py 执行 tutorials 任务，并输出验证报告。

设计约束：
1. 不直接调用内部 MCP 函数；只向 `python3 main.py` 输入自然语言。
2. 每个任务默认 `/clean` 后独立执行，避免上下文污染。
3. 从 Agent 输出中提取产物路径，并与教程 PDF 图片做相似度比对。
"""

import argparse
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2  # type: ignore
    from skimage.metrics import structural_similarity as ssim  # type: ignore

    HAS_IMG_COMPARE = True
except Exception:
    cv2 = None  # type: ignore
    ssim = None  # type: ignore
    HAS_IMG_COMPARE = False

ROOT = Path('/data/yphu/TCAD_Agent/code')
TUTORIAL_DIR = Path('/data/yphu/TCAD_Agent/doc/tutorials')
REPORT_BASE = ROOT / 'deliverables' / 'tutorial_suite'


@dataclass
class TaskSpec:
    task_id: str
    tutorial_pdf: str
    prompt: str
    expect: str  # text | structure_png | iv_png | band_png | cv_png


TASKS: list[TaskSpec] = [
    TaskSpec(
        task_id='L01_summary',
        tutorial_pdf='01_TCAD_laboratory_Introduction_GBB.pdf',
        prompt=(
            '请阅读并总结01_TCAD_laboratory_Introduction_GBB的核心学习目标，'
            '重点说明：TCAD用途、Sentaurus工具链、实验中每一步的输入输出文件。'
            '仅文字回答，不要运行任何工具。'
        ),
        expect='text',
    ),
    TaskSpec(
        task_id='L02_summary',
        tutorial_pdf='02_TCAD_laboratory_A_simulation_primer_GBB.pdf',
        prompt=(
            '请总结02_TCAD_laboratory_A_simulation_primer_GBB教程，'
            '列出从结构到电学仿真的最小流程，并说明常见错误与检查点。'
            '仅文字回答，不要运行任何工具。'
        ),
        expect='text',
    ),
    TaskSpec(
        task_id='L03_summary',
        tutorial_pdf='03_TCAD_laboratory_Overview_of_Synopsys_Sentaurus_TCAD_GBB.pdf',
        prompt=(
            '请总结03_TCAD_laboratory_Overview_of_Synopsys_Sentaurus_TCAD_GBB，'
            '按工具给出SDE/SDevice/SVisual/Inspect的职责与配合关系。'
            '仅文字回答，不要运行任何工具。'
        ),
        expect='text',
    ),
    TaskSpec(
        task_id='L04_structure',
        tutorial_pdf='04_TCAD_laboratory_pn_junction_GBB.pdf',
        prompt=(
            '请严格参考04_TCAD_laboratory_pn_junction_GBB教程，先只完成理想2D pn结结构任务：'
            'Si材料，p_contact/n_contact，Wp=10um，Wn=50um，p掺杂与n掺杂均为1e16 cm^-3。'
            '只需要完成SDE语法检查与运行，并通过svisual导出结构网格PNG，不要运行sdevice。'
        ),
        expect='structure_png',
    ),
    TaskSpec(
        task_id='L04_iv',
        tutorial_pdf='04_TCAD_laboratory_pn_junction_GBB.pdf',
        prompt=(
            '继续参考04教程，在已完成的理想pn结上生成并运行sdevice，'
            '执行从V_start=-1V到V_stop=1.5V的IV扫描，导出IV曲线PNG并完成验证。'
        ),
        expect='iv_png',
    ),
    TaskSpec(
        task_id='L05_structure',
        tutorial_pdf='05_TCAD_laboratory_integrated_diode_GBB.pdf',
        prompt=(
            '请参考05_TCAD_laboratory_integrated_diode_GBB教程，构建集成二极管结构：'
            '包含substrate、pwell、nwell、三个oxide区、p_contact和n_contact，'
            '并采用高斯平滑结分布。只执行SDE并导出结构PNG，不跑sdevice。'
        ),
        expect='structure_png',
    ),
    TaskSpec(
        task_id='L05_iv',
        tutorial_pdf='05_TCAD_laboratory_integrated_diode_GBB.pdf',
        prompt=(
            '继续参考05教程，运行SDevice（含SRH与Band2Band模型），'
            '完成反偏到正偏扫描，导出IV曲线PNG并验证结果。'
        ),
        expect='iv_png',
    ),
    TaskSpec(
        task_id='L06_structure',
        tutorial_pdf='06_TCAD_laboratory_MOSFET_GBB-SC_20150529H1917.pdf',
        prompt=(
            '请参考06_TCAD_laboratory_MOSFET_GBB-SC_20150529H1917教程，构建2D MOSFET：'
            '先建立半结构（substrate/gate oxide/poly/spacer/高斯源漏与扩展），再镜像得到全结构，'
            '并导出结构网格PNG。先只做SDE相关步骤。'
        ),
        expect='structure_png',
    ),
    TaskSpec(
        task_id='L06_iv',
        tutorial_pdf='06_TCAD_laboratory_MOSFET_GBB-SC_20150529H1917.pdf',
        prompt=(
            '继续参考06教程，完成MOSFET Id-Vg（turn-on）仿真并导出IV曲线PNG，'
            '再完成Id-Vd输出特性仿真并导出曲线。'
        ),
        expect='iv_png',
    ),
    TaskSpec(
        task_id='A5_Q1',
        tutorial_pdf='23M1135_Deep_Assignment_5.pdf',
        prompt=(
            '请完成23M1135_Deep_Assignment_5的Question 1：'
            'N型Si bar（0.1um x 0.1um，掺杂1e17 cm^-3），'
            '分别用constant mobility、doping dependence、high-field saturation三种模型，'
            '输出结构mesh图和IV曲线图。'
        ),
        expect='iv_png',
    ),
    TaskSpec(
        task_id='A5_Q2',
        tutorial_pdf='23M1135_Deep_Assignment_5.pdf',
        prompt=(
            '请完成23M1135_Deep_Assignment_5的Question 2：'
            'P型Si bar（长度10um，宽2um），分别设置ohmic-ohmic与ohmic-schottky(0.3eV)接触，'
            '输出结构图和IV曲线图。'
        ),
        expect='iv_png',
    ),
    TaskSpec(
        task_id='A5_Q3',
        tutorial_pdf='23M1135_Deep_Assignment_5.pdf',
        prompt=(
            '请完成23M1135_Deep_Assignment_5的Question 3：'
            '构建图示Si两端NPN样式器件，输出结构mesh图与关键电学分布图。'
        ),
        expect='structure_png',
    ),
    TaskSpec(
        task_id='A6_Q1',
        tutorial_pdf='23M1135_Assignment6.pdf',
        prompt=(
            '请完成23M1135_Assignment6的Question 1：'
            'PN结NA=ND=1e17 cm^-3，在-10V到2V做半对数IV，'
            '比较SRH开关和Band2Band模型，并导出曲线图。'
        ),
        expect='iv_png',
    ),
    TaskSpec(
        task_id='A6_Q2',
        tutorial_pdf='23M1135_Assignment6.pdf',
        prompt=(
            '请完成23M1135_Assignment6的Question 2：'
            'n型MOSCAP（Al gate, L=W=1um），在Vg=Vt下输出能带图与电子/空穴分布图，'
            '比较tox和NA变化。'
        ),
        expect='band_png',
    ),
    TaskSpec(
        task_id='A6_Q3',
        tutorial_pdf='23M1135_Assignment6.pdf',
        prompt=(
            '请完成23M1135_Assignment6的Question 3：'
            'n型MOSCAP在100kHz下做C-V（-5V到2V），比较SiO2、HfO2与EOT等效方案，导出曲线图。'
        ),
        expect='cv_png',
    ),
    TaskSpec(
        task_id='ECE335_design',
        tutorial_pdf='ECE335_Project_2.pdf',
        prompt=(
            '请参考ECE335_Project_2，构建45nm级NMOS设计任务：'
            '通过SubDop/HaloDop/ExtDop调优满足Ion/Ioff目标，'
            '并导出默认与最终结构掺杂图以及Id-Vg曲线。'
        ),
        expect='iv_png',
    ),
]


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


def run_prompt(prompt: str, out_dir: Path, timeout_s: int = 5400) -> dict[str, Any]:
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
    # 取最后一个包含 stage 的对象作为任务结果
    result = {}
    for obj in reversed(objs):
        if 'stage' in obj or 'session_id' in obj:
            result = obj
            break
    return {
        'return_code': proc.returncode,
        'result': result,
        'json_count': len(objs),
    }


def ensure_ref_images(report_root: Path, pdf_name: str) -> list[Path]:
    pdf_path = TUTORIAL_DIR / pdf_name
    ref_dir = report_root / 'ref_images' / pdf_path.stem
    ref_dir.mkdir(parents=True, exist_ok=True)
    marker = ref_dir / '.done'
    if not marker.exists():
        for old in ref_dir.glob('img-*'):
            old.unlink(missing_ok=True)
        subprocess.run(['pdfimages', '-all', str(pdf_path), str(ref_dir / 'img')], check=False)
        marker.write_text('ok', encoding='utf-8')
    imgs = [p for p in sorted(ref_dir.glob('img-*')) if p.is_file()]
    return imgs


def image_similarity(a: Path, b: Path) -> float:
    if not HAS_IMG_COMPARE:
        return -1.0
    ia = cv2.imread(str(a), cv2.IMREAD_GRAYSCALE)
    ib = cv2.imread(str(b), cv2.IMREAD_GRAYSCALE)
    if ia is None or ib is None:
        return -1.0
    if ia.shape[0] < 32 or ia.shape[1] < 32 or ib.shape[0] < 32 or ib.shape[1] < 32:
        return -1.0
    ia_r = cv2.resize(ia, (512, 512), interpolation=cv2.INTER_AREA)
    ib_r = cv2.resize(ib, (512, 512), interpolation=cv2.INTER_AREA)
    s_raw = float(ssim(ia_r, ib_r, data_range=255))
    ea = cv2.Canny(ia_r, 40, 120)
    eb = cv2.Canny(ib_r, 40, 120)
    s_edge = float(ssim(ea, eb, data_range=255))
    # 结构轮廓更重要
    return 0.35 * s_raw + 0.65 * s_edge


def best_match(gen_png: Path, ref_imgs: list[Path]) -> tuple[str, float]:
    best_name = ''
    best_score = -1.0
    for r in ref_imgs:
        sc = image_similarity(gen_png, r)
        if sc > best_score:
            best_score = sc
            best_name = r.name
    return best_name, best_score


def pick_generated_png(result: dict[str, Any], expect: str) -> str:
    arts = result.get('artifacts', {}) if isinstance(result, dict) else {}
    if not isinstance(arts, dict):
        return ''
    # 当前框架统一导出到 svisual_png；不同任务类型先共用该路径。
    if expect in {'structure_png', 'iv_png', 'band_png', 'cv_png'}:
        return str(arts.get('svisual_png', ''))
    return ''


def write_plan_files(run_root: Path, tasks: list[TaskSpec]) -> None:
    plan_lines = [
        '# Tutorial Suite Plan',
        '',
        '执行约束：',
        '- 仅通过自然语言驱动 `python3 main.py`。',
        '- 每个任务前执行 `/clean`，隔离上下文。',
        '- 图形任务导出 PNG，并与教程 PDF 图像做相似度比对（可用时）。',
        '',
        '任务清单：',
    ]
    for i, t in enumerate(tasks, start=1):
        plan_lines.append(f'{i}. {t.task_id} ({t.tutorial_pdf}) -> {t.expect}')
    (run_root / 'PLAN.md').write_text('\n'.join(plan_lines) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Run tutorial natural-language suite against TCAD Agent')
    parser.add_argument('--max-tasks', type=int, default=0, help='0 means all')
    parser.add_argument('--task-id', action='append', default=[], help='only run specific task ids (repeatable)')
    parser.add_argument('--report-dir', default='', help='output root dir; default is code/deliverables/tutorial_suite')
    parser.add_argument('--timeout-s', type=int, default=5400, help='per-task timeout seconds')
    args = parser.parse_args()

    report_base = Path(args.report_dir).resolve() if args.report_dir else REPORT_BASE
    report_base.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    run_root = report_base / f'run_{ts}'
    run_root.mkdir(parents=True, exist_ok=True)

    selected = TASKS
    if args.task_id:
        wanted = set(args.task_id)
        selected = [t for t in TASKS if t.task_id in wanted]
    if args.max_tasks > 0:
        selected = selected[: args.max_tasks]

    write_plan_files(run_root, selected)

    results: list[dict[str, Any]] = []
    for i, task in enumerate(selected, start=1):
        print(f'[{i}/{len(selected)}] {task.task_id}', flush=True)
        t_dir = run_root / task.task_id
        out = run_prompt(task.prompt, t_dir, timeout_s=max(120, int(args.timeout_s)))
        result_obj = out.get('result', {})
        stage = result_obj.get('stage') if isinstance(result_obj, dict) else None
        assistant_reply = result_obj.get('assistant_reply', '') if isinstance(result_obj, dict) else ''
        gen_png = pick_generated_png(result_obj, task.expect)
        png_ok = bool(gen_png and Path(gen_png).exists())

        ref_imgs = ensure_ref_images(run_root, task.tutorial_pdf)
        # 过滤极小图标，提高匹配稳定性
        ref_imgs = [p for p in ref_imgs if p.stat().st_size > 5 * 1024]
        best_ref = ''
        best_score = -1.0
        if png_ok and ref_imgs and HAS_IMG_COMPARE:
            best_ref, best_score = best_match(Path(gen_png), ref_imgs)
            if best_ref:
                ref_src = run_root / 'ref_images' / Path(task.tutorial_pdf).stem / best_ref
                if ref_src.exists():
                    shutil.copy2(ref_src, t_dir / f'best_ref_{best_ref}')
            shutil.copy2(gen_png, t_dir / Path(gen_png).name)
        elif png_ok:
            shutil.copy2(gen_png, t_dir / Path(gen_png).name)

        if task.expect == 'text':
            success = bool(str(assistant_reply).strip())
        else:
            success = bool(stage in {'svisual_done', 'validated', 'svisual_sde_done', 'tdr_inspected'} and png_ok)

        item = {
            'task_id': task.task_id,
            'tutorial_pdf': task.tutorial_pdf,
            'prompt': task.prompt,
            'return_code': out['return_code'],
            'json_count': out['json_count'],
            'stage': stage,
            'assistant_reply_preview': str(assistant_reply)[:500],
            'generated_png': gen_png,
            'generated_png_exists': png_ok,
            'best_ref_image': best_ref,
            'best_similarity_score': round(best_score, 4) if best_score >= 0 and HAS_IMG_COMPARE else None,
            'image_compare_enabled': HAS_IMG_COMPARE,
            'success': success,
        }
        (t_dir / 'summary.json').write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
        results.append(item)

    passed = sum(1 for r in results if r['success'])
    summary = {
        'total': len(results),
        'passed': passed,
        'failed': len(results) - passed,
        'pass_rate': round(passed / len(results), 4) if results else 0.0,
        'results': results,
    }
    out_file = run_root / 'tutorial_suite_report.json'
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    md_lines = [
        '# Tutorial Suite Report',
        '',
        f'- 总任务数: {summary["total"]}',
        f'- 通过: {summary["passed"]}',
        f'- 失败: {summary["failed"]}',
        f'- 通过率: {summary["pass_rate"]:.2%}',
        f'- 图像比对启用: {HAS_IMG_COMPARE}',
        '',
        '## 逐任务结果',
    ]
    for r in results:
        md_lines.append(
            f"- {r['task_id']}: success={r['success']}, stage={r['stage']}, png={r['generated_png_exists']}, sim={r['best_similarity_score']}"
        )
    (run_root / 'FINAL_REPORT.md').write_text('\n'.join(md_lines) + '\n', encoding='utf-8')
    print(
        json.dumps({'report': str(out_file), 'total': summary['total'], 'passed': summary['passed'], 'failed': summary['failed']}, ensure_ascii=False),
        flush=True,
    )
    return 0 if summary['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
