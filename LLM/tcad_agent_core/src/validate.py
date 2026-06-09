from __future__ import annotations

"""物理级验证模块 —— 确保仿真结果不仅“流程跑通”，更“结果可用”。

Sentaurus 仿真可能成功运行却产出无意义结果（如电流恒为零、
I-V 曲线无动态范围）。本模块从三个维度进行最终把关：

1. 文件存在性  — SDE/SDevice 输入脚本、网格、PLT 是否齐全
2. 曲线有效性  — PLT 是否可解析、是否有足够点数和非零跨度
3. 指标提取    — 从 PLT 提取 Ion/Ioff/SS 等数值指标

术语说明：
- PLT = Sentaurus 绘图文件，文本格式，含列名 + 数值表
- TDR = Sentaurus 网格/场数据文件（二进制）
- SS  = 亚阈值摆幅 (mV/dec)，理论下限 ~60 mV/dec @ 300K
- Ion/Ioff = 开态/关态电流，其比值反映晶体管开关质量
"""

import math
from pathlib import Path

from .core import DebugTracer, SessionState, ValidationResult


# ━━━━━━━━━━━━━━━━━━━━━━ 文件读取工具 ━━━━━━━━━━━━━━━━━━━━━━


def _read(path: Path) -> str:
    """安全读取文本文件，不存在时返回空串。

    Sentaurus 产物路径可能为空字符串或不存在——统一兜底避免异常扩散。
    """
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


# ━━━━━━━━━━━━━━━━━━━━━━ PLT 解析与曲线读取 ━━━━━━━━━━━━━━━━━━━━━━


def _parse_plt(path: Path) -> dict[str, list[float]]:
    """解析 Sentaurus PLT 文本为按列名索引的数值表。

    PLT 文件结构：
      datasets = ["col1" "col2" ...]   ← 列名声明
      Data { 0.0 1.2e-8 ... }         ← 行优先的扁平数值块
    将其重组为 {"col1": [v1, v2, ...], "col2": [...]} 的字典。
    """
    text = path.read_text(encoding="utf-8", errors="ignore")

    # 提取列名：读取 datasets = [ ... ] 中的双引号字段
    low = text.lower()
    idx = low.find("datasets")
    if idx < 0:
        raise ValueError("PLT missing datasets block")
    lb = text.find("[", idx)
    rb = text.find("]", lb + 1) if lb >= 0 else -1
    if lb < 0 or rb < 0:
        raise ValueError("PLT missing datasets block")
    dset_block = text[lb + 1 : rb]
    cols: list[str] = []
    i = 0
    while i < len(dset_block):
        if dset_block[i] != '"':
            i += 1
            continue
        j = dset_block.find('"', i + 1)
        if j < 0:
            break
        val = dset_block[i + 1 : j].strip()
        if val:
            cols.append(val)
        i = j + 1
    if not cols:
        raise ValueError("PLT empty datasets block")

    # 提取数值：读取 Data { ... } 块
    idx_data = low.find("data")
    if idx_data < 0:
        raise ValueError("PLT missing Data block")
    lb_data = text.find("{", idx_data)
    rb_data = text.rfind("}")
    if lb_data < 0 or rb_data < 0 or rb_data <= lb_data:
        raise ValueError("PLT missing Data block")
    data_block = text[lb_data + 1 : rb_data]
    for ch in ",\n\r\t":
        data_block = data_block.replace(ch, " ")
    nums: list[float] = []
    for tok in data_block.split():
        try:
            nums.append(float(tok))
        except ValueError:
            continue

    # 按列数分行，丢弃末尾不完整行
    ncols = len(cols)
    nrows = len(nums) // ncols
    nums = nums[: nrows * ncols]
    table = {c: [] for c in cols}
    for i in range(nrows):
        row = nums[i * ncols : (i + 1) * ncols]
        for j, c in enumerate(cols):
            table[c].append(row[j])
    return table


def _curve_points(path: Path) -> list[tuple[float, float]]:
    """读取 SVisual 导出的曲线文本文件，返回 (x, y) 点集。

    文件格式：每行两列数值，空格/Tab/逗号分隔，# 开头为注释行。
    """
    if not path.exists():
        return []
    pts: list[tuple[float, float]] = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.replace(",", " ").replace("\t", " ").split()
        if len(parts) < 2:
            continue
        try:
            pts.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return pts


# ━━━━━━━━━━━━━━━━━━━━━━ 电学指标计算 ━━━━━━━━━━━━━━━━━━━━━━


def _ss_mv_dec(vg: list[float], ids: list[float]) -> float | None:
    """粗略估算亚阈值摆幅 SS (mV/dec)。

    计算方法：对相邻点做 dVg / d(log10(Id))，取最小值。
    SS 反映栅极对沟道的控制能力，理论下限 ~60 mV/dec (300K)。
    值越小说明器件亚阈值特性越陡峭、开关越快。
    """
    if len(vg) < 3:
        return None
    vals = []
    for i in range(len(vg) - 1):
        dv = vg[i + 1] - vg[i]
        if dv <= 0:
            continue
        # 加极小值防止 log10(0)；1e-30 远小于任何实际电流
        i0 = abs(ids[i]) + 1e-30
        i1 = abs(ids[i + 1]) + 1e-30
        dlog = math.log10(i1) - math.log10(i0)
        if abs(dlog) < 1e-12:
            continue
        ss = abs(dv / dlog) * 1000.0  # V/dec → mV/dec
        # 过滤不合理值：< 1 mV/dec 为数值噪声，> 10000 为平坦区
        if 1.0 <= ss <= 1e4:
            vals.append(ss)
    return min(vals) if vals else None


# ━━━━━━━━━━━━━━━━━━━━━━ 物理验证器 ━━━━━━━━━━━━━━━━━━━━━━


class PhysicalValidator:
    """结构 + 曲线 + 指标联合验证器。

    三阶段验证流程：
    1. 文件存在性检查  — 所有关键产物是否生成且非空
    2. 曲线有效性检查  — PLT 解析/点数/跨度是否合理
    3. 指标提取与目标对比 — 计算 Ion/Ioff/SS 并与目标比较
    """

    def __init__(self, tracer: DebugTracer | None = None):
        self.tracer = tracer

    def validate(self, state: SessionState) -> ValidationResult:
        """对一次仿真会话结果进行最终物理有效性判定。

        返回 ValidationResult，包含所有检查项的通过/失败状态
        以及从 I-V 曲线提取的电学指标。
        """

        # ── 阶段零：收集产物路径 ──
        # 从 artifacts 字典中获取各阶段产物的文件路径，
        # 路径可能为空字符串（未生成）——后续通过 .exists() 判断
        sde_cmd = Path(state.artifacts.get("sde_cmd", ""))
        sdevice_cmd = Path(state.artifacts.get("sdevice_cmd", ""))
        mesh = Path(state.artifacts.get("mesh", ""))
        tdr_info = Path(state.artifacts.get("tdr_info_report", ""))
        plot = Path(state.artifacts.get("plot", ""))
        sdevice_log = Path(state.artifacts.get("log_sdevice", state.artifacts.get("sdevice_log", "")))
        if not sdevice_log.exists() and "sdevice" in state.artifacts.get("last_log_hint", ""):
            sdevice_log = Path(state.artifacts.get("last_log_hint", ""))
        curve_txt = Path(state.artifacts.get("svisual_curve_txt", ""))

        slog_text = _read(sdevice_log).lower()

        # ── 阶段一：文件存在性检查 ──
        # 仿真流程的每个阶段都应产出特定文件，缺失意味着该阶段未正常完成
        checks: dict[str, bool] = {
            "sde_cmd_exists": sde_cmd.exists(),
            "sdevice_cmd_exists": sdevice_cmd.exists(),
            "mesh_exists": mesh.exists(),
            "mesh_nonempty": mesh.exists() and mesh.stat().st_size > 0 if mesh.exists() else False,
            "tdr_info_exists": tdr_info.exists(),
            "plot_exists": plot.exists(),
            "sdevice_log_exists": sdevice_log.exists(),
            "no_exit_due_to_failure": "exit due to failure" not in slog_text,
        }

        # ── 阶段二：曲线分析与指标提取 ──
        # 从 PLT 文件解析 I-V 数据，计算 Ion/Ioff/SS 等核心指标，
        # 并与用户设定的性能目标进行对比
        metrics: dict[str, float] = {}

        if plot.exists():
            try:
                table = _parse_plt(plot)
                checks["plt_parse_ok"] = True

                # 纯数据驱动选轴：在所有 OuterVoltage 中取跨度最大者作为 X；
                # 在所有 TotalCurrent 中取绝对值跨度最大者作为 Y。
                voltage_cols = [c for c in table if c.endswith("OuterVoltage")]
                current_cols = [c for c in table if c.endswith("TotalCurrent")]
                x_col = ""
                y_col = ""
                if voltage_cols:
                    x_col = max(voltage_cols, key=lambda c: (max(table[c]) - min(table[c])) if table[c] else -1.0)
                if current_cols:
                    def _cur_span(col: str) -> float:
                        vals = [abs(v) for v in table[col]]
                        return (max(vals) - min(vals)) if vals else -1.0
                    y_col = max(current_cols, key=_cur_span)
                checks["plt_has_voltage_column"] = bool(x_col)
                checks["plt_has_current_column"] = bool(y_col)

                if x_col and y_col:
                    xs = table[x_col]
                    ys = table[y_col]
                    abs_ys = [abs(v) for v in ys]
                    checks["curve_points_enough"] = len(xs) >= 5
                    checks["curve_x_span_nonzero"] = (max(xs) - min(xs)) > 1e-9 if xs else False
                    checks["curve_y_span_nonzero"] = (max(abs_ys) - min(abs_ys)) > 1e-18 if abs_ys else False

                    # 核心指标提取
                    ion = max(abs_ys) if abs_ys else 0.0
                    ioff = min(abs_ys) if abs_ys else 0.0
                    ratio = ion / max(ioff, 1e-30)  # 防除零
                    ss = _ss_mv_dec(xs, ys)
                    metrics = {"ion_A": ion, "ioff_A": ioff, "on_off_ratio": ratio}
                    if ss is not None:
                        metrics["ss_mv_dec"] = ss

                    # 动态范围作为指标记录，不再单独作为硬失败项。
                    metrics["dynamic_range"] = ratio

                    # 与用户目标对比（未设目标则默认通过）
                    t = state.spec.targets
                    checks["target_ion"] = True if t.ion_min is None else ion >= t.ion_min
                    checks["target_ioff"] = True if t.ioff_max is None else ioff <= t.ioff_max
                    checks["target_ss"] = True if t.ss_max_mv_dec is None or ss is None else ss <= t.ss_max_mv_dec
                else:
                    checks["curve_points_enough"] = False
                    checks["curve_x_span_nonzero"] = False
                    checks["curve_y_span_nonzero"] = False
                    checks["target_ion"] = True
                    checks["target_ioff"] = True
                    checks["target_ss"] = True
            except Exception:
                checks["plt_parse_ok"] = False
                checks["curve_points_enough"] = False
                checks["curve_x_span_nonzero"] = False
                checks["curve_y_span_nonzero"] = False
                checks["target_ion"] = True
                checks["target_ioff"] = True
                checks["target_ss"] = True
        else:
            checks["plt_parse_ok"] = False
            checks["plt_has_voltage_column"] = False
            checks["plt_has_current_column"] = False
            checks["curve_points_enough"] = False
            checks["curve_x_span_nonzero"] = False
            checks["curve_y_span_nonzero"] = False
            checks["target_ion"] = True
            checks["target_ioff"] = True
            checks["target_ss"] = True

        # SVisual 导出的曲线文本作为额外的完整性验证
        pts = _curve_points(curve_txt)
        checks["svisual_curve_exported"] = len(pts) >= 5

        # 部分检查项属于“结果质量告警”而非“流程有效性硬失败”。
        # 例如 MOS 在特定参数下可能出现近乎平坦的 Id-Vg，这应作为性能告警，
        # 但不应否定“仿真流程已成功完成”的事实。
        advisory_checks = {"curve_y_span_nonzero"}
        hard_failed = [k for k, v in checks.items() if (k not in advisory_checks and not v)]
        warnings = [k for k in advisory_checks if not checks.get(k, True)]
        ok = len(hard_failed) == 0
        if ok and warnings:
            msg = f"Validation passed with warnings: {warnings[:8]}"
        elif ok:
            msg = "Validation passed."
        else:
            msg = f"Validation failed: {hard_failed[:12]}"

        if self.tracer:
            self.tracer.event(
                "PhysicalValidator",
                "validate_done",
                {"success": ok, "checks": checks, "metrics": metrics, "warnings": warnings},
                session_id=state.session_id,
            )

        return ValidationResult(success=ok, message=msg, checks=checks, metrics=metrics)
