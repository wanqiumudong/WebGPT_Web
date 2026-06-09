#!/usr/bin/env python3
from __future__ import annotations

"""运行一组参考 Dataset 的端到端回归案例。"""

import argparse
import json
import time
from pathlib import Path

from main import clean_runtime
from src.agent_system import TCADAgentSystem


ROOT = Path("/data/yphu/TCAD_Agent/code")


CASES: list[dict[str, str]] = [
    {
        "id": "dataset_nmos_idvg",
        "reference": "/data/yphu/Dataset/sde_2d_n_mos.cmd",
        "requirement": (
            "参考 Dataset 的 2D NMOS 风格，生成包含 Si 衬底、SiO2 栅氧和多晶硅栅的器件，"
            "源漏高掺杂、沟道轻掺杂并进行沟道/界面网格加密；随后生成 SDevice 完成 IdVg 仿真并验证。"
        ),
    },
    {
        "id": "dataset_pmos_idvg",
        "reference": "/data/yphu/Dataset/sde_2d_p_mos.cmd",
        "requirement": (
            "参考 Dataset 的 2D PMOS 风格，生成 PMOS 结构并设置对应掺杂极性，"
            "完成 IdVg 仿真、导出曲线并验证。"
        ),
    },
    {
        "id": "dataset_diode_iv",
        "reference": "/data/yphu/Dataset/cy/SimpleDiode.cmd",
        "requirement": (
            "参考 Dataset 的 SimpleDiode 思路，生成 2D PN 二极管（anode/cathode），"
            "定义P/N掺杂和结区网格，完成 I-V 仿真并验证。"
        ),
    },
    {
        "id": "dataset_finfet_double_gate",
        "reference": "/data/yphu/Dataset/sde_3d_n_fin.cmd",
        "requirement": (
            "参考 Dataset FinFET 风格，生成 2D 双栅 FinFET 截面：左右源漏扩展区、中间沟道，"
            "上下栅氧+栅金属，定义源漏高斯掺杂并完成 IdVg 仿真与验证。"
        ),
    },
]


def run_case(case: dict[str, str]) -> dict[str, object]:
    clean_runtime()

    t0 = time.time()
    agent = TCADAgentSystem(ROOT)
    out = agent.agent_decide_and_execute(case["requirement"])
    dt = round(time.time() - t0, 3)

    validation = out.get("validation", {})
    result: dict[str, object] = {
        "id": case["id"],
        "reference": case["reference"],
        "requirement": case["requirement"],
        "elapsed_s": dt,
        "stage": out.get("stage"),
        "validation_success": validation.get("success"),
        "validation_message": validation.get("message"),
        "passed": bool(out.get("stage") == "validated" and validation.get("success")),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dataset-inspired E2E regression cases")
    parser.add_argument("--max-cases", type=int, default=0, help="limit number of cases (0 = all)")
    args = parser.parse_args()

    selected = CASES if args.max_cases <= 0 else CASES[: args.max_cases]
    results: list[dict[str, object]] = []

    for case in selected:
        print(f"[CASE] {case['id']}")
        try:
            res = run_case(case)
        except Exception as exc:
            res = {
                "id": case["id"],
                "reference": case["reference"],
                "requirement": case["requirement"],
                "elapsed_s": 0.0,
                "stage": None,
                "validation_success": False,
                "validation_message": f"exception: {exc}",
                "passed": False,
            }
        results.append(res)
        print(f"  -> passed={res['passed']} stage={res.get('stage')} elapsed={res.get('elapsed_s')}s")

    passed = sum(1 for r in results if r.get("passed"))
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round((passed / len(results)) if results else 0.0, 4),
        "results": results,
    }

    report_dir = ROOT / "runtime" / "default" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"dataset_case_report_{int(time.time())}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[REPORT] {report_path}")
    print(json.dumps({k: summary[k] for k in ["total", "passed", "failed", "pass_rate"]}, ensure_ascii=False))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
