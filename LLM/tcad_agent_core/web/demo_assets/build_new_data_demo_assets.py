from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
NEW_DATA_ROOT = Path("/data/yphu/Dataset/New_Data").resolve()

MOS_ROOT = NEW_DATA_ROOT / "第25课_6.1节_MOS_Gate_Tunneling" / "MOS_Gate_Tunneling"
LDMOS_ROOT = NEW_DATA_ROOT / "第33课_8.3节_Radiation仿真案例" / "Radiation" / "LDMOS_Alpha"
NMOS_ROOT = NEW_DATA_ROOT / "第34课_8.4节_可靠性HCI和NBTI" / "NMOS_180nm_HCI"

STRUCTURE_TARGET = ROOT / "prototype_mos_gate_tunneling"
ELECTRICAL_TARGET = ROOT / "prototype_ldmos_alpha"
COMPACT_TARGET = ROOT / "prototype_nmos_180nm_hci"


def _read_dfise_xyplot(path: Path) -> tuple[list[str], list[list[float]]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    datasets_match = re.search(r"datasets\s*=\s*\[(.*?)\]\s*functions", text, re.S)
    functions_match = re.search(r"functions\s*=\s*\[(.*?)\]\s*}", text, re.S)
    if not datasets_match or not functions_match or "Data {" not in text:
        raise ValueError(f"unsupported DF-ISE xyplot: {path}")
    dataset_names = re.findall(r'"([^"]+)"', datasets_match.group(1))
    value_count = len(functions_match.group(1).split())
    raw_values = text.split("Data {", 1)[1].rsplit("}", 1)[0].split()
    values = [float(item) for item in raw_values]
    rows = [values[index : index + value_count] for index in range(0, len(values), value_count)]
    return dataset_names, rows


def _extract_xy(path: Path, *, x_name: str, y_name: str, abs_y: bool = False) -> list[tuple[float, float]]:
    names, rows = _read_dfise_xyplot(path)
    x_index = names.index(x_name)
    y_index = names.index(y_name)
    parsed: list[tuple[float, float]] = []
    for row in rows:
        y_value = abs(row[y_index]) if abs_y else row[y_index]
        parsed.append((row[x_index], y_value))
    return parsed


def _deduplicate_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not points:
        return []
    buckets: dict[float, list[float]] = {}
    for x_value, y_value in points:
        key = round(x_value, 9)
        buckets.setdefault(key, []).append(y_value)
    ordered = sorted(buckets)
    return [(x_value, max(buckets[x_value])) for x_value in ordered]


def _plot_curve(
    points: list[tuple[float, float]],
    *,
    output_path: Path,
    title: str,
    x_label: str,
    y_label: str,
    log_y: bool = False,
    color: str = "#1f77b4",
    label: str | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    ax.plot([item[0] for item in points], [item[1] for item in points], linewidth=2.2, color=color, label=label)
    ax.set_title(title, fontsize=13)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    if log_y:
        ax.set_yscale("log")
    ax.grid(alpha=0.25)
    if label:
        ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def _run_svisual_export(*, source_tdr: Path, output_png: Path, export_mode: str) -> None:
    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_png.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="tcad-demo-assets-") as temp_dir:
        temp_root = Path(temp_dir)
        staged_tdr = temp_root / "input.tdr"
        shutil.copy2(source_tdr, staged_tdr)
        script_path = temp_root / "export.tcl"
        if export_mode == "structure":
            script_body = (
                f'set src "{staged_tdr}"\n'
                f'set out_png "{output_png}"\n'
                'if {[catch {load_file $src -name D0} err]} {puts "ERROR: $err"; exit 1}\n'
                'set p [create_plot -dataset D0]\n'
                'if {[catch {export_view $out_png -plots [list $p] -format png -resolution 1400x900} err]} {puts "ERROR: $err"; exit 2}\n'
                'exit 0\n'
            )
        elif export_mode == "doping":
            output_csv = output_png.with_suffix(".csv")
            output_csv.unlink(missing_ok=True)
            script_body = (
                f'set src "{staged_tdr}"\n'
                f'set out_png "{output_png}"\n'
                f'set out_csv "{output_csv}"\n'
                'if {[catch {load_file $src -name D0} err]} {puts "ERROR: $err"; exit 1}\n'
                'set p2 [create_plot -dataset D0]\n'
                'if {[catch {set c1 [create_cutline -plot $p2 -type x -at 0.0]} err]} {puts "ERROR: $err"; exit 2}\n'
                'if {[catch {export_variables {DopingConcentration Y} -dataset [list $c1] -filename $out_csv -overwrite} err]} {puts "ERROR: $err"; exit 3}\n'
                'set p1 [create_plot -dataset $c1 -1d]\n'
                'if {[catch {create_curve -plot $p1 -dataset $c1 -axisX {Y} -axisY {DopingConcentration}} err]} {puts "ERROR: $err"; exit 4}\n'
                'catch {set_axis_prop -plot $p1 -axis y -type log}\n'
                'if {[catch {export_view $out_png -plots [list $p1] -format png -resolution 1400x900} err]} {puts "ERROR: $err"; exit 5}\n'
                'exit 0\n'
            )
        else:
            raise ValueError(f"unsupported export mode: {export_mode}")
        script_path.write_text(script_body, encoding="utf-8")
        subprocess.run(
            ["svisual", "-bx", "-tcl", "-s", str(script_path)],
            cwd=temp_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


def _linear_regression(points: list[tuple[float, float]]) -> tuple[float, float]:
    xs = [item[0] for item in points]
    ys = [item[1] for item in points]
    denominator = len(xs) * sum(x * x for x in xs) - sum(xs) ** 2
    if not denominator:
        return 0.0, 0.0
    slope = (len(xs) * sum(x * y for x, y in zip(xs, ys)) - sum(xs) * sum(ys)) / denominator
    intercept = (sum(ys) - slope * sum(xs)) / len(xs)
    return slope, intercept


def _gradient(xs: list[float], ys: list[float]) -> list[float]:
    values = []
    for index, x_value in enumerate(xs):
        if index == 0:
            slope = (ys[index + 1] - ys[index]) / (xs[index + 1] - x_value)
        elif index == len(xs) - 1:
            slope = (ys[index] - ys[index - 1]) / (x_value - xs[index - 1])
        else:
            slope = (ys[index + 1] - ys[index - 1]) / (xs[index + 1] - xs[index - 1])
        values.append(slope)
    return values


def _build_structure_assets() -> None:
    STRUCTURE_TARGET.mkdir(parents=True, exist_ok=True)
    _run_svisual_export(
        source_tdr=MOS_ROOT / "n11_msh.tdr",
        output_png=STRUCTURE_TARGET / "mos_structure.png",
        export_mode="structure",
    )
    _run_svisual_export(
        source_tdr=MOS_ROOT / "n11_msh.tdr",
        output_png=STRUCTURE_TARGET / "mos_doping_profile.png",
        export_mode="doping",
    )


def _build_ldmos_assets() -> None:
    ELECTRICAL_TARGET.mkdir(parents=True, exist_ok=True)
    output_curve = _extract_xy(
        LDMOS_ROOT / "IdVds_n33_des.plt",
        x_name="drain OuterVoltage",
        y_name="drain TotalCurrent",
        abs_y=True,
    )
    breakdown_curve = _extract_xy(
        LDMOS_ROOT / "hi_n33_des.plt",
        x_name="drain InnerVoltage",
        y_name="drain TotalCurrent",
        abs_y=True,
    )
    _plot_curve(
        output_curve,
        output_path=ELECTRICAL_TARGET / "ldmos_output_curve.png",
        title="LDMOS Output Curve",
        x_label="Vd [V]",
        y_label="Id [A]",
        color="#1f77b4",
    )
    _plot_curve(
        breakdown_curve,
        output_path=ELECTRICAL_TARGET / "ldmos_breakdown_curve.png",
        title="LDMOS Breakdown Curve",
        x_label="Vd [V]",
        y_label="Id [A]",
        log_y=True,
        color="#d62728",
    )
    metrics = {
        "ids_max": max(y for _, y in output_curve),
        "vd_max": max(x for x, _ in output_curve),
        "breakdown_voltage": max(x for x, _ in breakdown_curve),
    }
    (ELECTRICAL_TARGET / "ldmos_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _compact_current(vgs: float, *, vth0: float, kp: float, nfactor: float, lambda_value: float, vds: float) -> float:
    vt = 0.02585
    nvt = max(nfactor * vt, 1e-6)
    soft_vgt = nvt * math.log1p(math.exp((vgs - vth0) / nvt))
    ids_lin = kp * max(soft_vgt * vds - 0.5 * vds * vds, 0.0)
    ids_sat = 0.5 * kp * soft_vgt * soft_vgt
    ids = ids_lin if vds < soft_vgt else ids_sat
    return max(ids * (1.0 + lambda_value * max(vds, 0.0)), 1e-18)


def _build_compact_assets() -> None:
    COMPACT_TARGET.mkdir(parents=True, exist_ok=True)
    transfer_curve = _extract_xy(
        NMOS_ROOT / "IdVgs_n21_des.plt",
        x_name="gate OuterVoltage",
        y_name="drain TotalCurrent",
        abs_y=True,
    )
    transfer_curve = _deduplicate_points(transfer_curve)
    output_curve = _extract_xy(
        NMOS_ROOT / "temp_n21_des.plt",
        x_name="drain OuterVoltage",
        y_name="drain TotalCurrent",
        abs_y=True,
    )
    output_curve = _deduplicate_points(output_curve)
    _plot_curve(
        transfer_curve,
        output_path=COMPACT_TARGET / "nmos_transfer_curve.png",
        title="NMOS Transfer Curve",
        x_label="Vg [V]",
        y_label="Id [A]",
        log_y=True,
        color="#1f77b4",
    )
    _plot_curve(
        output_curve,
        output_path=COMPACT_TARGET / "nmos_output_curve.png",
        title="NMOS Output Curve",
        x_label="Vd [V]",
        y_label="Id [A]",
        color="#2ca02c",
    )

    vg = [item[0] for item in transfer_curve]
    id_values = [item[1] for item in transfer_curve]
    vd = [item[0] for item in output_curve]
    id_output = [item[1] for item in output_curve]
    gm_values = _gradient(vg, id_values)
    gm_max = max(gm_values)
    ion = max(id_values)
    ioff = min(id_values)
    vth0 = next((gate for gate, current in transfer_curve if current >= 1e-5), vg[min(range(len(gm_values)), key=lambda index: abs(gm_values[index] - gm_max))])
    ss_points = [(x_value, math.log10(current)) for x_value, current in transfer_curve if 1e-7 < current < 1e-4]
    ss_slope, _ = _linear_regression(ss_points) if len(ss_points) >= 2 else (0.0, 0.0)
    ss_mv_dec = 1000.0 / ss_slope if ss_slope else 0.0
    nfactor = round(max(1.3, min(2.1, ss_mv_dec / 320.0 if ss_mv_dec else 1.5)), 3)
    high_vd_threshold = output_curve[0][0] + 0.7 * (max(vd) - output_curve[0][0])
    lambda_points = [(x_value, current) for x_value, current in output_curve if x_value >= high_vd_threshold]
    lambda_slope, lambda_intercept = _linear_regression(lambda_points) if len(lambda_points) >= 2 else (0.0, 0.0)
    lambda_value = round(max(0.03, min(0.35, lambda_slope / lambda_intercept if lambda_intercept else 0.08)), 4)
    idsat = max(id_output)
    vds_bias = max(vd)
    overdrive = max(max(vg) - vth0, 1e-3)
    kp = max(2.0 * idsat / (overdrive ** 2 * (1.0 + lambda_value * vds_bias)), 1e-8)

    fit_transfer = [
        (x_value, _compact_current(x_value, vth0=vth0, kp=kp, nfactor=nfactor, lambda_value=lambda_value, vds=0.1))
        for x_value, _ in transfer_curve
    ]
    fit_output = [
        (x_value, _compact_current(max(vg), vth0=vth0, kp=kp, nfactor=nfactor, lambda_value=lambda_value, vds=x_value))
        for x_value, _ in output_curve
    ]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    axes[0].plot([item[0] for item in transfer_curve], [item[1] for item in transfer_curve], linewidth=2.2, color="#1f77b4", label="TCAD Id-Vg")
    axes[0].plot([item[0] for item in fit_transfer], [item[1] for item in fit_transfer], linewidth=2.0, color="#ff7f0e", linestyle="--", label="Compact-model fit")
    axes[0].set_title("Transfer Curve", fontsize=13)
    axes[0].set_xlabel("Vg [V]")
    axes[0].set_ylabel("Id [A]")
    axes[0].set_yscale("log")
    axes[0].grid(alpha=0.25)
    axes[0].legend(frameon=False)

    axes[1].plot([item[0] for item in output_curve], [item[1] for item in output_curve], linewidth=2.2, color="#1f77b4", label="TCAD Id-Vd")
    axes[1].plot([item[0] for item in fit_output], [item[1] for item in fit_output], linewidth=2.0, color="#2ca02c", linestyle="--", label="Compact-model fit")
    axes[1].set_title("Output Curve", fontsize=13)
    axes[1].set_xlabel("Vd [V]")
    axes[1].set_ylabel("Id [A]")
    axes[1].grid(alpha=0.25)
    axes[1].legend(frameon=False)
    fig.suptitle("NMOS Compact-Model Overlay", fontsize=13)
    fig.tight_layout()
    fig.savefig(COMPACT_TARGET / "compact_model_overlay.png", dpi=180)
    plt.close(fig)
    parameter_card = {
        "case_id": "compact_model_task",
        "source_case": "NMOS_180nm_HCI",
        "description": "基于 NMOS_180nm_HCI 中参考 Id-Vg / Id-Vd 曲线整理的 compact-model 参数卡。",
        "model_form": "iv_curve_behavioral_overlay",
        "VTH0": round(vth0, 4),
        "KP": round(kp, 7),
        "NFACTOR": nfactor,
        "LAMBDA": lambda_value,
        "RS": 2.0,
        "RD": 2.0,
        "ION": ion,
        "IOFF": ioff,
        "GM_MAX": gm_max,
        "SS_MV_DEC": round(ss_mv_dec, 2) if ss_mv_dec else 0.0,
        "IDSAT": idsat,
    }
    (COMPACT_TARGET / "parameter_card.json").write_text(
        json.dumps(parameter_card, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = f"""# NMOS 紧凑模型摘要

- Source case: `NMOS_180nm_HCI`
- Goal: 基于原型工程中的参考 `Id-Vg` / `Id-Vd` 曲线，整理 compact-model 参数，并保留 `Verilog-A` 导出接口。
- Model form: `iv_curve_behavioral_overlay`
- Derived parameters:
  - `VTH0 = {parameter_card['VTH0']:.4f}`
  - `KP = {parameter_card['KP']:.7f}`
  - `NFACTOR = {parameter_card['NFACTOR']:.3f}`
  - `RS = {parameter_card['RS']:.2f}`
  - `RD = {parameter_card['RD']:.2f}`
  - `IDSAT = {parameter_card['IDSAT']:.6e}`

## Notes

1. 参数提取基于原型工程中的参考 `Id-Vg` / `Id-Vd` 曲线整理，保留网页演示所需的可读性与一致性。
2. 参数卡强调阈值、电流能力、亚阈值趋势和接口参数，不等同于完整工业级 BSIM/CMI 提取。
3. 当前导出的 `Verilog-A` 文件为接口级模型骨架，后续可继续接入外部电路验证流程。
"""
    (COMPACT_TARGET / "compact_model_summary.md").write_text(summary, encoding="utf-8")
    verilog_a = f"""`include "constants.vams"
`include "disciplines.vams"

module nmos_compact_model(d, g, s, b);
  inout d, g, s, b;
  electrical d, g, s, b;

  parameter real VTH0 = {parameter_card['VTH0']:.4f};
  parameter real KP = {parameter_card['KP']:.7f};
  parameter real NFACTOR = {parameter_card['NFACTOR']:.3f};
  parameter real RS = {parameter_card['RS']:.2f};
  parameter real RD = {parameter_card['RD']:.2f};

  analog begin
    real vgs, vds, vt, nvt, soft_vgt, ids_lin, ids_sat, ids;
    vt = 0.02585;
    nvt = max(NFACTOR * vt, 1e-6);
    vgs = V(g, s);
    vds = V(d, s);
    soft_vgt = nvt * ln(1.0 + exp((vgs - VTH0) / nvt));

    ids_sat = 0.5 * KP * soft_vgt * soft_vgt;
    ids_lin = KP * (soft_vgt * vds - 0.5 * vds * vds);
    ids = (vds < soft_vgt) ? ids_lin : ids_sat;

    I(d, s) <+ ids;
  end
endmodule
"""
    (COMPACT_TARGET / "nmos_compact_model.va").write_text(verilog_a, encoding="utf-8")


def main() -> None:
    _build_structure_assets()
    _build_ldmos_assets()
    _build_compact_assets()


if __name__ == "__main__":
    main()
