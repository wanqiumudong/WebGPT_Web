from __future__ import annotations

"""Sentaurus 命令执行层。

该模块只负责：
1. 调用真实二进制（sde / sdevice / svisual / tdx）
2. 记录日志和返回码
3. 汇总产物路径为标准 StepResult

不含任何 LLM 调用或业务判断——纯粹的"工具人"层。
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

from .core import DebugTracer, SessionState, StepResult, preview_text


BUILD_MESH_RE = re.compile(r"\(sde:build-mesh\s+\"?([^\")\s]+)\"?\)", re.IGNORECASE)


class SentaurusOps:
    """Sentaurus 工具调用封装。

    每个公开方法对应一个 Sentaurus 二进制调用，接收 SessionState、
    返回 StepResult，由上层 agent_system 负责状态跃迁。
    """

    # ━━━━━━━━━━━━━━━━ 初始化与环境检查 ━━━━━━━━━━━━━━━━

    def __init__(self, tracer: DebugTracer | None = None) -> None:
        self.tracer = tracer
        # 启动时即校验四个必需二进制是否在 PATH 中，
        # 避免运行到一半才发现工具缺失导致状态不一致。
        for name in ["sde", "sdevice", "svisual", "tdx"]:
            path = shutil.which(name)
            if self.tracer:
                self.tracer.event("SentaurusOps", "binary_check", {"name": name, "path": path})
            if path is None:
                raise RuntimeError(f"Required binary not found: {name}")
        # 非主流程可选二进制：缺失时仅在调用该能力时报错。
        self.optional_bins = {}
        for name in ["inspect", "boxmethod", "logbrowser", "snmesh"]:
            path = shutil.which(name)
            self.optional_bins[name] = path
            if self.tracer:
                self.tracer.event("SentaurusOps", "binary_check_optional", {"name": name, "path": path})

    @staticmethod
    def ensure_dirs(state: SessionState) -> tuple[Path, Path, Path]:
        """确保会话下 run/logs/reports 三个目录存在。"""
        run_dir = state.session_dir / "run"
        log_dir = state.session_dir / "logs"
        rep_dir = state.session_dir / "reports"
        run_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        rep_dir.mkdir(parents=True, exist_ok=True)
        return run_dir, log_dir, rep_dir

    @staticmethod
    def _safe_label(text: str) -> str:
        out: list[str] = []
        for ch in text:
            if ch.isalnum() or ch in "_.:/-":
                out.append(ch)
            else:
                out.append("_")
        return "".join(out)

    @staticmethod
    def _resolve_session_path(state: SessionState, user_path: str, *, default_dir: str = "run") -> Path:
        """把用户路径解析为会话内绝对路径。

        规则：
        1. 绝对路径直接使用
        2. 相对路径优先在 session/<default_dir> 下查找
        3. 若 default_dir 下不存在，再尝试 session 根目录
        """
        raw = user_path.strip()
        p = Path(raw)
        if p.is_absolute():
            return p
        base = state.session_dir / default_dir / p
        if base.exists():
            return base.resolve()
        return (state.session_dir / p).resolve()

    @staticmethod
    def _choose_output_path(state: SessionState, output_file: str, fallback: Path, *, default_dir: str = "run") -> Path:
        """解析输出路径；为空时使用 fallback。"""
        if not output_file.strip():
            return fallback
        p = Path(output_file.strip())
        if p.is_absolute():
            return p
        return (state.session_dir / default_dir / p).resolve()

    @staticmethod
    def _expected_sde_outputs(run_dir: Path, deck_path: Path | None = None) -> tuple[Path, Path]:
        default_mesh = run_dir / "sde_result_msh.tdr"
        default_bnd = run_dir / "sde_result_bnd.tdr"
        if deck_path is None or not deck_path.exists():
            return default_mesh, default_bnd
        try:
            code = deck_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return default_mesh, default_bnd
        match = BUILD_MESH_RE.search(code)
        if not match:
            return default_mesh, default_bnd
        stem = match.group(1).strip().strip('"').strip("'")
        if stem.lower().endswith(".tdr"):
            stem = stem[:-4]
        if not stem:
            return default_mesh, default_bnd
        return run_dir / f"{stem}_msh.tdr", run_dir / f"{stem}_bnd.tdr"

    # ━━━━━━━━━━━━━━━━ 通用命令执行器 ━━━━━━━━━━━━━━━━━━

    def _run_to_log(
        self,
        cmd: list[str],
        cwd: Path,
        log_file: Path,
        timeout_s: int,
        *,
        session_id: str,
        capture: bool = False,
    ) -> tuple[int, str]:
        """执行命令并将输出落盘到日志文件。

        capture=True 时先收集到内存再写文件（适合需要解析输出的场景，如 tdx -info）；
        capture=False 时直接流式写入文件（适合长时间运行的仿真，避免内存溢出）。
        """
        start = time.time()
        if self.tracer:
            self.tracer.event(
                "SentaurusOps",
                "command_start",
                {"cmd": cmd, "cwd": str(cwd), "log_file": str(log_file), "timeout_s": timeout_s},
                session_id=session_id,
            )

        out = ""
        rc = 0
        if capture:
            try:
                p = subprocess.run(
                    cmd,
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                )
                rc = p.returncode
                out = p.stdout
            except subprocess.TimeoutExpired as exc:
                rc = 124
                out = (exc.stdout or "") + f"\n[timeout] command exceeded {timeout_s}s\n"
            log_file.write_text(out, encoding="utf-8", errors="ignore")
        else:
            with log_file.open("w", encoding="utf-8") as f:
                try:
                    p = subprocess.run(
                        cmd,
                        cwd=str(cwd),
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=timeout_s,
                        check=False,
                    )
                    rc = p.returncode
                except subprocess.TimeoutExpired:
                    rc = 124
                    f.write(f"\n[timeout] command exceeded {timeout_s}s\n")
            out = log_file.read_text(encoding="utf-8", errors="ignore") if log_file.exists() else ""

        if self.tracer:
            self.tracer.event(
                "SentaurusOps",
                "command_done",
                {
                    "cmd": cmd,
                    "return_code": rc,
                    "duration_s": round(time.time() - start, 3),
                    "log_file": str(log_file),
                    "output_preview": preview_text(out, max_chars=900),
                },
                session_id=session_id,
            )
        return rc, out

    # ━━━━━━━━━━━━━━━━ SDE 操作（结构编辑器） ━━━━━━━━━━━━━

    def check_sde(self, state: SessionState, timeout_s: int = 600) -> StepResult:
        """运行 `sde -S` 做语法检查。

        -S 模式只做语法解析不执行，速度快（通常 < 30s），
        能提前捕获拼写错误和 Scheme 语法问题。
        """
        run_dir, log_dir, _ = self.ensure_dirs(state)
        sde_cmd = run_dir / "sde_dvs.cmd"
        if not sde_cmd.exists():
            return StepResult(False, "sde_check_failed", f"Missing file: {sde_cmd}")

        log_file = log_dir / "sde_syntax.log"
        rc, _ = self._run_to_log(["sde", "-S", "sde_dvs.cmd"], run_dir, log_file, timeout_s, session_id=state.session_id)
        ok = rc == 0
        return StepResult(
            success=ok,
            stage="sde_checked" if ok else "sde_check_failed",
            message="SDE syntax check passed." if ok else f"SDE syntax check failed: rc={rc}",
            logs={"sde_syntax": str(log_file)},
            artifacts={"sde_cmd": str(sde_cmd)},
            details={"return_code": rc},
        )

    def run_sde(self, state: SessionState, timeout_s: int = 1800) -> StepResult:
        """运行 `sde -e -l` 生成 mesh 与边界文件。

        -e 表示执行模式，-l 启用日志输出。
        成功后应产出 sde_result_msh.tdr（网格）和 sde_result_bnd.tdr（边界）。
        """
        run_dir, log_dir, _ = self.ensure_dirs(state)
        sde_cmd = run_dir / "sde_dvs.cmd"
        if not sde_cmd.exists():
            return StepResult(False, "sde_failed", f"Missing file: {sde_cmd}")

        log_file = log_dir / "sde.log"
        rc, _ = self._run_to_log(["sde", "-e", "-l", "sde_dvs.cmd"], run_dir, log_file, timeout_s, session_id=state.session_id)

        msh, bnd = self._expected_sde_outputs(run_dir, sde_cmd)
        if rc != 0:
            return StepResult(False, "sde_failed", f"SDE run failed: rc={rc}", logs={"sde": str(log_file)})
        if not msh.exists() or msh.stat().st_size == 0:
            return StepResult(False, "sde_failed", "SDE finished but mesh file missing/empty.", logs={"sde": str(log_file)})

        return StepResult(
            True,
            "sde_done",
            "SDE run done.",
            logs={"sde": str(log_file)},
            artifacts={"mesh": str(msh), "bnd": str(bnd), "sde_cmd": str(sde_cmd)},
            details={"return_code": rc},
        )

    def check_and_run_sde(self, state: SessionState, timeout_s: int = 1800) -> StepResult:
        """运行 `sde -Sl`：先语法检查，再在通过时执行脚本。

        该模式来自 SDE user guide，可减少“语法通过后又单独运行”的来回开销。
        """
        run_dir, log_dir, _ = self.ensure_dirs(state)
        sde_cmd = run_dir / "sde_dvs.cmd"
        if not sde_cmd.exists():
            return StepResult(False, "sde_checkrun_failed", f"Missing file: {sde_cmd}")

        log_file = log_dir / "sde_checkrun.log"
        rc, _ = self._run_to_log(["sde", "-Sl", "sde_dvs.cmd"], run_dir, log_file, timeout_s, session_id=state.session_id)
        msh, bnd = self._expected_sde_outputs(run_dir, sde_cmd)
        ok = rc == 0 and msh.exists() and msh.stat().st_size > 0
        return StepResult(
            ok,
            "sde_done" if ok else "sde_checkrun_failed",
            "SDE syntax-check+run done." if ok else f"sde -Sl failed: rc={rc}",
            logs={"sde_checkrun": str(log_file)},
            artifacts={"mesh": str(msh), "bnd": str(bnd), "sde_cmd": str(sde_cmd)},
            details={"return_code": rc},
        )

    # ━━━━━━━━━━━━━━━━ TDX 操作（结构检查器） ━━━━━━━━━━━━━

    def inspect_tdr(self, state: SessionState, tdr_filename: str = "sde_result_msh.tdr", timeout_s: int = 300) -> StepResult:
        """运行 `tdx -info` 读取网格/材料/接触摘要。

        tdx 是 Sentaurus 自带的 TDR 文件检查工具，
        输出包含维度、顶点数、单元数、区域列表等结构信息。
        """
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        tdr = run_dir / tdr_filename
        if not tdr.exists() and tdr_filename == "sde_result_msh.tdr":
            artifact_mesh = state.artifacts.get("mesh")
            if artifact_mesh:
                tdr = Path(artifact_mesh)
        if not tdr.exists():
            return StepResult(False, "tdr_inspect_failed", f"Missing file: {tdr}")

        log_file = log_dir / f"tdx_info_{tdr.stem}.log"
        rc, out = self._run_to_log(["tdx", "-info", tdr.name], run_dir, log_file, timeout_s, session_id=state.session_id, capture=True)

        report = rep_dir / f"tdr_info_{tdr.stem}.txt"
        report.write_text(out, encoding="utf-8", errors="ignore")
        summary = self._summarize_tdx(out)

        return StepResult(
            rc == 0,
            "tdr_inspected" if rc == 0 else "tdr_inspect_failed",
            "TDR inspected." if rc == 0 else f"tdx -info failed: rc={rc}",
            logs={"tdx_info": str(log_file)},
            artifacts={"tdr_info_report": str(report), "tdr_file": str(tdr)},
            details={"return_code": rc, "summary": summary},
        )

    @staticmethod
    def _summarize_tdx(out: str) -> dict[str, int | str]:
        """从 tdx 文本中提取轻量结构摘要。

        输出格式示例：
        {
            "dimension": 2,
            "vertices": 12345,
            "elements": 24000,
            "regions": 5,
            "states": 1,
            "region_preview": "R.Silicon | R.SiO2 | R.Poly | ..."
        }

        region_preview 只取前 8 个含已知材料名的行，
        跳过 <contact> 和 <interface> 行（它们不是物理区域）。
        """
        summary: dict[str, int | str] = {}
        keys = ["Dimension", "Vertices", "Elements", "Regions", "States"]
        for ln in out.splitlines():
            left, sep, right = ln.partition(":")
            if not sep:
                continue
            key = left.strip()
            if key not in keys:
                continue
            first = right.strip().split()
            if not first:
                continue
            try:
                summary[key.lower()] = int(first[0])
            except ValueError:
                pass
        reg = []
        for ln in out.splitlines():
            if "<contact>" in ln or "<interface>" in ln:
                continue
            if any(mat in ln for mat in ["Silicon", "SiO2", "GaN", "AlGaN", "Poly", "Metal"]):
                reg.append(ln.strip())
        if reg:
            summary["region_preview"] = " | ".join(reg[:8])
        return summary

    # ━━━━━━━━━━━━━━━━ SDevice 操作（器件仿真器） ━━━━━━━━━

    def check_sdevice(self, state: SessionState, timeout_s: int = 900) -> StepResult:
        """运行 `sdevice -P` 做参数/语法预检查。

        -P 模式解析 cmd 文件但不启动求解器，
        能发现材料模型缺失、电极名不匹配等常见错误。
        """
        run_dir, log_dir, _ = self.ensure_dirs(state)
        cmd = run_dir / "sdevice_des.cmd"
        if not cmd.exists():
            return StepResult(False, "sdevice_check_failed", f"Missing file: {cmd}")

        log_file = log_dir / "sdevice_syntax.log"
        rc, _ = self._run_to_log(["sdevice", "-P", "sdevice_des.cmd"], run_dir, log_file, timeout_s, session_id=state.session_id)
        ok = rc == 0
        return StepResult(
            ok,
            "sdevice_checked" if ok else "sdevice_check_failed",
            "SDevice parameter/syntax check passed." if ok else f"SDevice -P failed: rc={rc}",
            logs={"sdevice_syntax": str(log_file)},
            artifacts={"sdevice_cmd": str(cmd)},
            details={"return_code": rc},
        )

    def run_sdevice(self, state: SessionState, timeout_s: int = 5400) -> StepResult:
        """运行 `sdevice --exit-on-failure` 执行真实仿真。

        --exit-on-failure 使仿真在首个收敛失败时立即退出，
        避免长时间等待注定失败的扫描点。
        成功后产出 PLT 文件（IV 数据）和可选的 TDR 快照。
        """
        run_dir, log_dir, _ = self.ensure_dirs(state)
        cmd = run_dir / "sdevice_des.cmd"
        if not cmd.exists():
            return StepResult(False, "sdevice_failed", f"Missing file: {cmd}")

        log_file = log_dir / "sdevice.log"
        rc, _ = self._run_to_log(
            ["sdevice", "--exit-on-failure", "sdevice_des.cmd"],
            run_dir,
            log_file,
            timeout_s,
            session_id=state.session_id,
        )
        if rc != 0:
            return StepResult(False, "sdevice_failed", f"SDevice failed: rc={rc}", logs={"sdevice": str(log_file)})

        plot_candidates = sorted(run_dir.glob("result_*_des.plt")) + sorted(run_dir.glob("*_des.plt"))
        tdr_candidates = sorted(run_dir.glob("*_des.tdr"))

        if not plot_candidates:
            return StepResult(False, "sdevice_failed", "SDevice finished but PLT not generated.", logs={"sdevice": str(log_file)})

        artifacts = {
            "plot": str(plot_candidates[0]),
            "sdevice_cmd": str(cmd),
        }
        mesh, _ = self._expected_sde_outputs(run_dir, run_dir / "sde_dvs.cmd")
        if mesh.exists():
            artifacts["mesh"] = str(mesh)
        if tdr_candidates:
            artifacts["tdr"] = str(tdr_candidates[0])

        return StepResult(
            True,
            "sdevice_done",
            "SDevice run done.",
            logs={"sdevice": str(log_file)},
            artifacts=artifacts,
            details={"return_code": rc},
        )

    def dump_sdevice_parameters(
        self,
        state: SessionState,
        target: str = "Si",
        output_file: str = "",
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行 `sdevice -P` 参数导出。

        target 支持：
        - "Si" / "default"      -> `sdevice -P`
        - "All"                 -> `sdevice -P:All`
        - "Material"            -> `sdevice -P:Material`（如 SiO2、GaN）
        - "Material:x"          -> `sdevice -P:AlGaN:0.25`
        - "Material/Material"   -> 接口参数
        - "file:<cmd_path>"     -> `sdevice -P <cmd_path>`
        """
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        target_norm = target.strip()
        out_path = self._choose_output_path(
            state,
            output_file,
            fallback=rep_dir / f"sdevice_P_{self._safe_label(target_norm or 'Si')}.par",
            default_dir="reports",
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if target_norm.lower().startswith("file:"):
            cmd_file = self._resolve_session_path(state, target_norm[5:].strip(), default_dir="run")
            args = ["sdevice", "-P", str(cmd_file)]
        elif target_norm.lower() in {"", "si", "default"}:
            args = ["sdevice", "-P"]
        else:
            args = ["sdevice", f"-P:{target_norm}"]

        log_file = log_dir / f"sdevice_param_dump_{int(time.time())}.log"
        rc, out = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id, capture=True)
        out_path.write_text(out, encoding="utf-8", errors="ignore")
        ok = rc == 0 and out_path.exists() and out_path.stat().st_size > 0
        return StepResult(
            ok,
            "sdevice_param_dump_done" if ok else "sdevice_param_dump_failed",
            "SDevice parameter dump done." if ok else f"SDevice -P failed: rc={rc}",
            logs={"sdevice_param_dump": str(log_file)},
            artifacts={"sdevice_param_dump": str(out_path)},
            details={"return_code": rc, "target": target_norm},
        )

    def dump_sdevice_library(
        self,
        state: SessionState,
        target: str = "Si",
        output_file: str = "",
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行 `sdevice -L` 参数库导出。"""
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        target_norm = target.strip()
        out_path = self._choose_output_path(
            state,
            output_file,
            fallback=rep_dir / f"sdevice_L_{self._safe_label(target_norm or 'Si')}.parlib",
            default_dir="reports",
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if target_norm.lower().startswith("file:"):
            cmd_file = self._resolve_session_path(state, target_norm[5:].strip(), default_dir="run")
            args = ["sdevice", "-L", str(cmd_file)]
        elif target_norm.lower() in {"", "si", "default"}:
            args = ["sdevice", "-L"]
        else:
            args = ["sdevice", f"-L:{target_norm}"]

        log_file = log_dir / f"sdevice_library_dump_{int(time.time())}.log"
        rc, out = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id, capture=True)
        out_path.write_text(out, encoding="utf-8", errors="ignore")
        ok = rc == 0 and out_path.exists() and out_path.stat().st_size > 0
        return StepResult(
            ok,
            "sdevice_library_dump_done" if ok else "sdevice_library_dump_failed",
            "SDevice library dump done." if ok else f"SDevice -L failed: rc={rc}",
            logs={"sdevice_library_dump": str(log_file)},
            artifacts={"sdevice_library_dump": str(out_path)},
            details={"return_code": rc, "target": target_norm},
        )

    def list_sdevice_parameter_names(self, state: SessionState, timeout_s: int = 600) -> StepResult:
        """运行 `sdevice --parameter-names`。"""
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        log_file = log_dir / "sdevice_parameter_names.log"
        rc, out = self._run_to_log(
            ["sdevice", "--parameter-names"],
            run_dir,
            log_file,
            timeout_s,
            session_id=state.session_id,
            capture=True,
        )
        report = rep_dir / "sdevice_parameter_names.txt"
        report.write_text(out, encoding="utf-8", errors="ignore")
        ok = rc == 0 and report.exists() and report.stat().st_size > 0
        return StepResult(
            ok,
            "sdevice_parameter_names_done" if ok else "sdevice_parameter_names_failed",
            "SDevice parameter-name listing done." if ok else f"SDevice --parameter-names failed: rc={rc}",
            logs={"sdevice_parameter_names": str(log_file)},
            artifacts={"sdevice_parameter_names": str(report)},
            details={"return_code": rc},
        )

    def list_sdevice_field_names(self, state: SessionState, timeout_s: int = 600) -> StepResult:
        """运行 `sdevice --field-names`。"""
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        log_file = log_dir / "sdevice_field_names.log"
        rc, out = self._run_to_log(
            ["sdevice", "--field-names"],
            run_dir,
            log_file,
            timeout_s,
            session_id=state.session_id,
            capture=True,
        )
        report = rep_dir / "sdevice_field_names.txt"
        report.write_text(out, encoding="utf-8", errors="ignore")
        ok = rc == 0 and report.exists() and report.stat().st_size > 0
        return StepResult(
            ok,
            "sdevice_field_names_done" if ok else "sdevice_field_names_failed",
            "SDevice field-name listing done." if ok else f"SDevice --field-names failed: rc={rc}",
            logs={"sdevice_field_names": str(log_file)},
            artifacts={"sdevice_field_names": str(report)},
            details={"return_code": rc},
        )

    def list_sdevice_versions(self, state: SessionState, timeout_s: int = 600) -> StepResult:
        """运行 `sdevice -versions`。"""
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        log_file = log_dir / "sdevice_versions.log"
        rc, out = self._run_to_log(
            ["sdevice", "-versions"],
            run_dir,
            log_file,
            timeout_s,
            session_id=state.session_id,
            capture=True,
        )
        report = rep_dir / "sdevice_versions.txt"
        report.write_text(out, encoding="utf-8", errors="ignore")
        ok = rc == 0 and report.exists() and report.stat().st_size > 0
        return StepResult(
            ok,
            "sdevice_versions_done" if ok else "sdevice_versions_failed",
            "SDevice version listing done." if ok else f"SDevice -versions failed: rc={rc}",
            logs={"sdevice_versions": str(log_file)},
            artifacts={"sdevice_versions": str(report)},
            details={"return_code": rc},
        )

    # ━━━━━━━━━━━━━━━━ TDX 扩展操作（格式转换/几何处理） ━━━━━━━━━

    def tdx_convert(
        self,
        state: SessionState,
        command: str,
        source_file: str,
        dest_file: str = "",
        options: list[str] | None = None,
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行 `tdx --<command>` 执行格式转换。

        command 示例：
        - tif2tdr, tdr2tif, tdr2dfise, dfise2tdr
        - tdf2tdr, tdf2dfise, plx2tdr, ivl2tdr, tdr2tdr
        """
        run_dir, log_dir, _ = self.ensure_dirs(state)
        cmd_norm = command.strip().lstrip("-")
        if not cmd_norm:
            return StepResult(False, "tdx_convert_failed", "tdx command is required.")

        src = self._resolve_session_path(state, source_file, default_dir="run")
        if not src.exists():
            return StepResult(False, "tdx_convert_failed", f"Source file missing: {src}")
        dst = self._choose_output_path(
            state,
            dest_file,
            fallback=(run_dir / f"{src.stem}_{cmd_norm}{src.suffix}"),
            default_dir="run",
        )
        dst.parent.mkdir(parents=True, exist_ok=True)

        args = ["tdx", f"--{cmd_norm}"]
        if options:
            args.extend([str(x) for x in options if str(x).strip()])
        args.extend([str(src), str(dst)])

        log_file = log_dir / f"tdx_{cmd_norm}_{int(time.time())}.log"
        rc, _ = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id)
        ok = rc == 0 and dst.exists() and dst.stat().st_size > 0
        return StepResult(
            ok,
            "tdx_convert_done" if ok else "tdx_convert_failed",
            "TDX convert done." if ok else f"TDX convert failed: rc={rc}",
            logs={"tdx_convert": str(log_file)},
            artifacts={"tdx_source": str(src), "tdx_output": str(dst)},
            details={"return_code": rc, "command": cmd_norm, "args": args},
        )

    def tdx_change_coordinate_system(
        self,
        state: SessionState,
        source_file: str,
        dest_file: str = "",
        target: str = "sprocess",
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行 `tdx --tdr-change-cs` 做坐标系转换。"""
        run_dir, log_dir, _ = self.ensure_dirs(state)
        src = self._resolve_session_path(state, source_file, default_dir="run")
        if not src.exists():
            return StepResult(False, "tdx_cs_failed", f"Source file missing: {src}")

        target_norm = target.strip().lower()
        if target_norm not in {"sprocess", "traditional"}:
            return StepResult(False, "tdx_cs_failed", "target must be 'sprocess' or 'traditional'.")
        flag = "--sp" if target_norm == "sprocess" else "--traditional"
        suffix = "_sp" if target_norm == "sprocess" else "_tr"
        dst = self._choose_output_path(
            state,
            dest_file,
            fallback=(run_dir / f"{src.stem}{suffix}{src.suffix}"),
            default_dir="run",
        )
        dst.parent.mkdir(parents=True, exist_ok=True)

        args = ["tdx", "--tdr-change-cs", flag, str(src), str(dst)]
        log_file = log_dir / f"tdx_change_cs_{int(time.time())}.log"
        rc, _ = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id)
        ok = rc == 0 and dst.exists() and dst.stat().st_size > 0
        return StepResult(
            ok,
            "tdx_cs_done" if ok else "tdx_cs_failed",
            "TDX coordinate-system conversion done." if ok else f"TDX change-cs failed: rc={rc}",
            logs={"tdx_change_cs": str(log_file)},
            artifacts={"tdx_source": str(src), "tdx_output": str(dst)},
            details={"return_code": rc, "target": target_norm, "args": args},
        )

    def tdx_mirror_tdr(
        self,
        state: SessionState,
        source_file: str,
        axis: str = "xmin",
        dest_file: str = "",
        rename_rule: str = "",
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行 `tdx --mirr-tdr` 对 TDR 几何镜像。"""
        run_dir, log_dir, _ = self.ensure_dirs(state)
        src = self._resolve_session_path(state, source_file, default_dir="run")
        if not src.exists():
            return StepResult(False, "tdx_mirror_failed", f"Source file missing: {src}")

        axis_map = {
            "xmin": "--xmin",
            "xmax": "--xmax",
            "ymin": "--ymin",
            "ymax": "--ymax",
            "zmin": "--zmin",
            "zmax": "--zmax",
        }
        axis_norm = axis.strip().lower()
        if axis_norm not in axis_map:
            return StepResult(False, "tdx_mirror_failed", "axis must be one of xmin/xmax/ymin/ymax/zmin/zmax.")

        dst = self._choose_output_path(
            state,
            dest_file,
            fallback=(run_dir / f"{src.stem}_mirr_{axis_norm}{src.suffix}"),
            default_dir="run",
        )
        dst.parent.mkdir(parents=True, exist_ok=True)

        args = ["tdx", "--mirr-tdr", axis_map[axis_norm]]
        if rename_rule.strip():
            args.extend(["--rename", rename_rule.strip()])
        args.extend([str(src), str(dst)])

        log_file = log_dir / f"tdx_mirror_{axis_norm}_{int(time.time())}.log"
        rc, _ = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id)
        ok = rc == 0 and dst.exists() and dst.stat().st_size > 0
        return StepResult(
            ok,
            "tdx_mirror_done" if ok else "tdx_mirror_failed",
            "TDX mirror done." if ok else f"TDX mirror failed: rc={rc}",
            logs={"tdx_mirror": str(log_file)},
            artifacts={"tdx_source": str(src), "tdx_output": str(dst)},
            details={"return_code": rc, "axis": axis_norm, "args": args},
        )

    def tdx_run_tclcmd(self, state: SessionState, tcl_command: str, timeout_s: int = 600) -> StepResult:
        """运行 `tdx -tclcmd` 执行单条 Tcl 命令。"""
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        cmd = tcl_command.strip()
        if not cmd:
            return StepResult(False, "tdx_tclcmd_failed", "tcl_command is required.")

        log_file = log_dir / f"tdx_tclcmd_{int(time.time())}.log"
        rc, out = self._run_to_log(["tdx", "-tclcmd", cmd], run_dir, log_file, timeout_s, session_id=state.session_id, capture=True)
        report = rep_dir / f"tdx_tclcmd_{int(time.time())}.txt"
        report.write_text(out, encoding="utf-8", errors="ignore")
        ok = rc == 0
        return StepResult(
            ok,
            "tdx_tclcmd_done" if ok else "tdx_tclcmd_failed",
            "TDX Tcl command done." if ok else f"TDX -tclcmd failed: rc={rc}",
            logs={"tdx_tclcmd": str(log_file)},
            artifacts={"tdx_tclcmd_output": str(report)},
            details={"return_code": rc, "tcl_command": cmd},
        )

    # ━━━━━━━━━━━━━━━━ SVisual 操作（可视化导出） ━━━━━━━━━

    def run_svisual(self, state: SessionState, source_file: str = "", mode: str = "auto", timeout_s: int = 1200) -> StepResult:
        """运行 svisual 批处理导出 PNG 和曲线文本。

        支持两种模式：
        - PLT 模式：从电学仿真结果提取 IV 曲线，生成 1D 折线图 + 数值文本
        - TDR 模式：从结构文件生成 2D/3D 器件截面图，仅输出 PNG
        auto 模式根据文件扩展名自动判断。
        """
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)

        chosen = source_file.strip()
        if not chosen:
            return StepResult(
                False,
                "svisual_failed",
                "source_file is required for run_svisual_export (expect .plt/.tdr path).",
            )

        src = Path(chosen).resolve()
        if not src.exists():
            return StepResult(False, "svisual_failed", f"SVisual source missing: {src}")

        use_mode = mode.lower()
        if use_mode == "auto":
            use_mode = "plt" if src.suffix.lower() == ".plt" else "tdr"

        src_for_vis = src
        if use_mode == "plt":
            src_for_vis = self._sanitize_plt_for_svisual(src, run_dir)

        png = rep_dir / f"{src.stem}.png"
        curve_txt = rep_dir / f"{src.stem}_curve.txt"

        # PLT 模式需要从文件头解析列名，选出合适的 X/Y 轴
        # 传入 simulation_type 确保 IdVd 选 drain OuterVoltage 而非 gate OuterVoltage
        axis_x = ""
        axis_y = ""
        if use_mode == "plt":
            cols = self._parse_plt_columns(src_for_vis)
            sim_type = state.spec.simulation_type if state.spec else ""
            axis_x, axis_y = self._pick_axes(cols, simulation_type=sim_type)
            axis_x = self._prefer_nonzero_span_voltage_axis(src_for_vis, axis_x)
            # 用实际数据再次挑选 Y 轴：优先选“变化幅度最大”的电流列，避免导出到全零曲线。
            table = self._parse_plt_table(src_for_vis)
            if table:
                current_cols_total = [c for c in table.keys() if c.endswith("TotalCurrent") and c != axis_x]
                current_cols = current_cols_total or [c for c in table.keys() if "current" in c.lower() and c != axis_x]
                if current_cols:
                    def _cur_span(col: str) -> float:
                        vals = [abs(v) for v in table.get(col, [])]
                        return (max(vals) - min(vals)) if vals else 0.0

                    best_y = max(current_cols, key=_cur_span)
                    if _cur_span(best_y) >= _cur_span(axis_y):
                        axis_y = best_y

        script = run_dir / f"svisual_export_{src.stem}.tcl"
        script.write_text(self._build_svisual_tcl(src_for_vis, png, use_mode, axis_x, axis_y, curve_txt), encoding="utf-8")

        log_file = log_dir / f"svisual_{src.stem}.log"
        rc, _ = self._run_to_log(
            ["svisual", "-bx", "-tcl", "-s", str(script)],
            run_dir,
            log_file,
            timeout_s,
            session_id=state.session_id,
        )

        curve_ok = True if use_mode != "plt" else (curve_txt.exists() and curve_txt.stat().st_size > 0)
        png_ok = png.exists() and png.stat().st_size > 0
        # SVisual 在部分环境下可能返回非零 rc 但文件已正确导出。
        # 只要目标文件产物齐全，就判定为成功；rc 作为 details 保留用于调试。
        ok = png_ok and curve_ok

        artifacts = {
            "svisual_script": str(script),
            "svisual_png": str(png),
            "svisual_source": str(src),
            "svisual_source_used": str(src_for_vis),
        }
        if use_mode == "plt":
            artifacts["svisual_curve_txt"] = str(curve_txt)
            artifacts["svisual_axis_x"] = axis_x
            artifacts["svisual_axis_y"] = axis_y

        return StepResult(
            ok,
            "svisual_done" if ok else "svisual_failed",
            "SVisual export done." if ok else f"SVisual export failed: rc={rc}, curve_ok={curve_ok}",
            logs={"svisual": str(log_file)},
            artifacts=artifacts,
            details={"return_code": rc, "mode": use_mode, "curve_ok": curve_ok},
        )

    def run_svisual_tcl_script(
        self,
        state: SessionState,
        script_content: str = "",
        script_file: str = "",
        expected_outputs: list[str] | None = None,
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行用户提供的 SVisual Tcl 脚本（batchx）。"""
        run_dir, log_dir, _ = self.ensure_dirs(state)
        if not script_content.strip() and not script_file.strip():
            return StepResult(False, "svisual_script_failed", "script_content or script_file is required.")

        if script_content.strip():
            script_path = run_dir / f"svisual_custom_{int(time.time())}.tcl"
            script_path.write_text(script_content, encoding="utf-8")
        else:
            script_path = self._resolve_session_path(state, script_file, default_dir="run")
        if not script_path.exists():
            return StepResult(False, "svisual_script_failed", f"SVisual script missing: {script_path}")

        log_file = log_dir / f"svisual_script_{int(time.time())}.log"
        rc, _ = self._run_to_log(
            ["svisual", "-bx", "-tcl", "-s", str(script_path)],
            run_dir,
            log_file,
            timeout_s,
            session_id=state.session_id,
        )
        expected = expected_outputs or []
        expected_paths = [self._resolve_session_path(state, p, default_dir="reports") for p in expected]
        expected_ok = all(p.exists() and p.stat().st_size > 0 for p in expected_paths) if expected_paths else True
        ok = rc == 0 and expected_ok
        return StepResult(
            ok,
            "svisual_script_done" if ok else "svisual_script_failed",
            "SVisual custom script done." if ok else f"SVisual custom script failed: rc={rc}, expected_ok={expected_ok}",
            logs={"svisual_script": str(log_file)},
            artifacts={
                "svisual_script": str(script_path),
                "svisual_expected_outputs": ", ".join(str(p) for p in expected_paths),
            },
            details={"return_code": rc, "expected_ok": expected_ok},
        )

    def run_svisual_cutline_export(
        self,
        state: SessionState,
        source_file: str,
        axis: str = "x",
        at: float = 0.0,
        variables: list[str] | None = None,
        timeout_s: int = 1200,
    ) -> StepResult:
        """对 TDR 创建 cutline 并导出变量 CSV + PNG。"""
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        src = self._resolve_session_path(state, source_file, default_dir="run")
        if not src.exists():
            return StepResult(False, "svisual_cutline_failed", f"SVisual source missing: {src}")

        axis_norm = axis.strip().lower()
        if axis_norm not in {"x", "y", "z"}:
            return StepResult(False, "svisual_cutline_failed", "axis must be x/y/z.")

        vars_list = [v.strip() for v in (variables or ["DopingConcentration", "Potential", "Y"]) if v.strip()]
        if not vars_list:
            vars_list = ["DopingConcentration", "Potential", "Y"]
        vars_tcl = " ".join(vars_list)

        stem = f"{src.stem}_cut_{axis_norm}_{str(at).replace('-', 'm').replace('.', 'p')}"
        out_png = rep_dir / f"{stem}.png"
        out_csv = rep_dir / f"{stem}.csv"
        # 先探测维度：3D 情况下，需先做 cutplane 再做 cutline。
        dim = 2
        if src.suffix.lower() == ".tdr":
            dim_log = log_dir / f"svisual_cutline_dim_probe_{int(time.time())}.log"
            rc_dim, out_dim = self._run_to_log(
                ["tdx", "-info", str(src)],
                run_dir,
                dim_log,
                300,
                session_id=state.session_id,
                capture=True,
            )
            if rc_dim == 0:
                for ln in out_dim.splitlines():
                    left, sep, right = ln.partition(":")
                    if not sep:
                        continue
                    if left.strip().lower() != "dimension":
                        continue
                    first = right.strip().split()
                    if first:
                        try:
                            dim = int(first[0])
                        except ValueError:
                            pass
                    break

        attempts: list[dict[str, float | str | bool]] = []
        # 2D 直接 cutline
        for cand_axis, cand_at in [(axis_norm, at), ("x", at), ("y", at), ("x", 0.0), ("y", 0.0)]:
            if cand_axis in {"x", "y", "z"}:
                item = {"use_cutplane": False, "line_axis": cand_axis, "line_at": float(cand_at)}
                if item not in attempts:
                    attempts.append(item)
        # 3D 增加 cutplane+cutline 组合
        if dim == 3:
            for plane_axis, plane_at, line_axis, line_at in [
                ("z", 0.0, "x", 0.0),
                ("z", 0.0, "y", 0.0),
                ("x", 0.0, "y", 0.0),
                ("y", 0.0, "x", 0.0),
            ]:
                item = {
                    "use_cutplane": True,
                    "plane_axis": plane_axis,
                    "plane_at": float(plane_at),
                    "line_axis": line_axis,
                    "line_at": float(line_at),
                }
                if item not in attempts:
                    attempts.append(item)

        last_rc = 1
        last_log = log_dir / f"svisual_cutline_{stem}.log"
        used_axis = axis_norm
        used_at = at
        used_script = run_dir / f"svisual_cutline_{stem}.tcl"

        for i, cfg in enumerate(attempts, start=1):
            try_axis = str(cfg["line_axis"])
            try_at = float(cfg["line_at"])
            used_axis = try_axis
            used_at = try_at
            used_script = run_dir / f"svisual_cutline_{stem}_try{i}.tcl"
            x_axis_var = "Y" if try_axis == "x" else "X"
            y_axis_var = vars_list[0]
            if bool(cfg.get("use_cutplane", False)):
                plane_axis = str(cfg["plane_axis"])
                plane_at = float(cfg["plane_at"])
                script_body = (
                    f'set src "{src}"\n'
                    f'set out_png "{out_png}"\n'
                    f'set out_csv "{out_csv}"\n'
                    'if {[catch {load_file $src -name D0} err]} {puts "ERROR: $err"; exit 1}\n'
                    'set p3 [create_plot -dataset D0]\n'
                    f'if {{[catch {{set cp [create_cutplane -plot $p3 -type {plane_axis} -at {plane_at:g}]}} err]}} {{puts "ERROR: $err"; exit 2}}\n'
                    'set p2 [create_plot -dataset $cp]\n'
                    f'if {{[catch {{set c1 [create_cutline -plot $p2 -type {try_axis} -at {try_at:g}]}} err]}} {{puts "ERROR: $err"; exit 3}}\n'
                    f'if {{[catch {{export_variables {{{vars_tcl}}} -dataset [list $c1] -filename $out_csv -overwrite}} err]}} {{puts "ERROR: $err"; exit 4}}\n'
                    'set p1 [create_plot -dataset $c1 -1d]\n'
                    f'catch {{create_curve -plot $p1 -dataset $c1 -axisX {{{x_axis_var}}} -axisY {{{y_axis_var}}}}}\n'
                    'if {[catch {export_view $out_png -plots [list $p2] -format png -resolution 1600x900} err]} {puts "ERROR: $err"; exit 5}\n'
                    'exit 0\n'
                )
            else:
                script_body = (
                    f'set src "{src}"\n'
                    f'set out_png "{out_png}"\n'
                    f'set out_csv "{out_csv}"\n'
                    'if {[catch {load_file $src -name D0} err]} {puts "ERROR: $err"; exit 1}\n'
                    'set p2 [create_plot -dataset D0]\n'
                    f'if {{[catch {{set c1 [create_cutline -plot $p2 -type {try_axis} -at {try_at:g}]}} err]}} {{puts "ERROR: $err"; exit 2}}\n'
                    f'if {{[catch {{export_variables {{{vars_tcl}}} -dataset [list $c1] -filename $out_csv -overwrite}} err]}} {{puts "ERROR: $err"; exit 3}}\n'
                    'set p1 [create_plot -dataset $c1 -1d]\n'
                    f'catch {{create_curve -plot $p1 -dataset $c1 -axisX {{{x_axis_var}}} -axisY {{{y_axis_var}}}}}\n'
                    'if {[catch {export_view $out_png -plots [list $p2] -format png -resolution 1600x900} err]} {puts "ERROR: $err"; exit 4}\n'
                    'exit 0\n'
                )
            used_script.write_text(script_body, encoding="utf-8")

            last_log = log_dir / f"svisual_cutline_{stem}_try{i}.log"
            rc, _ = self._run_to_log(
                ["svisual", "-bx", "-tcl", "-s", str(used_script)],
                run_dir,
                last_log,
                timeout_s,
                session_id=state.session_id,
            )
            last_rc = rc
            if rc == 0 and out_png.exists() and out_png.stat().st_size > 0 and out_csv.exists() and out_csv.stat().st_size > 0:
                return StepResult(
                    True,
                    "svisual_cutline_done",
                    "SVisual cutline export done.",
                    logs={"svisual_cutline": str(last_log)},
                    artifacts={
                        "svisual_script": str(used_script),
                        "svisual_source": str(src),
                        "svisual_cutline_png": str(out_png),
                        "svisual_cutline_csv": str(out_csv),
                    },
                    details={"return_code": rc, "axis": used_axis, "at": used_at, "variables": vars_list},
                )

        return StepResult(
            False,
            "svisual_cutline_failed",
            f"SVisual cutline export failed: rc={last_rc}",
            logs={"svisual_cutline": str(last_log)},
            artifacts={
                "svisual_script": str(used_script),
                "svisual_source": str(src),
                "svisual_cutline_png": str(out_png),
                "svisual_cutline_csv": str(out_csv),
            },
            details={"return_code": last_rc, "axis": used_axis, "at": used_at, "variables": vars_list, "attempts": attempts},
        )

    def run_inspect_script(
        self,
        state: SessionState,
        script_content: str = "",
        script_file: str = "",
        input_files: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        batch: bool = True,
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行 Inspect 脚本（支持批处理）。

        适用场景：
        - lessons/tutorial 中的 Inspect 提参脚本（Vth/SS/Ion/Ioff 等）
        - 对 .plt/.tdr/.ivl 曲线做二次分析并导出文本/图像
        """
        run_dir, log_dir, _ = self.ensure_dirs(state)
        if shutil.which("inspect") is None:
            return StepResult(False, "inspect_failed", "Binary not found: inspect")

        if not script_content.strip() and not script_file.strip():
            return StepResult(False, "inspect_failed", "script_content or script_file is required.")

        if script_content.strip():
            script_path = run_dir / f"inspect_custom_{int(time.time())}.cmd"
            script_path.write_text(script_content, encoding="utf-8")
        else:
            script_path = self._resolve_session_path(state, script_file, default_dir="run")
        if not script_path.exists():
            return StepResult(False, "inspect_failed", f"Inspect script missing: {script_path}")

        args = ["inspect"]
        if batch:
            args.append("-batch")
        args.extend(["-f", str(script_path)])

        resolved_inputs: list[Path] = []
        for raw in input_files or []:
            p = self._resolve_session_path(state, raw, default_dir="run")
            resolved_inputs.append(p)
            args.append(str(p))

        log_file = log_dir / f"inspect_{int(time.time())}.log"
        rc, _ = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id)

        expected = expected_outputs or []
        expected_paths = [self._resolve_session_path(state, p, default_dir="reports") for p in expected]
        expected_ok = all(p.exists() and p.stat().st_size > 0 for p in expected_paths) if expected_paths else True
        ok = rc == 0 and expected_ok
        return StepResult(
            ok,
            "inspect_done" if ok else "inspect_failed",
            "Inspect script run done." if ok else f"Inspect script failed: rc={rc}, expected_ok={expected_ok}",
            logs={"inspect": str(log_file)},
            artifacts={
                "inspect_script": str(script_path),
                "inspect_inputs": ", ".join(str(p) for p in resolved_inputs),
                "inspect_expected_outputs": ", ".join(str(p) for p in expected_paths),
            },
            details={"return_code": rc, "batch": batch, "expected_ok": expected_ok, "args": args},
        )

    # ━━━━━━━━━━━━━━━━ Utilities 扩展（boxmethod/logbrowser） ━━━━━━━━━

    def run_boxmethod(
        self,
        state: SessionState,
        grid_file: str,
        algorithm: str = "CVPL_AverageBoxMethod",
        num_threads: int = 1,
        timeout_s: int = 1200,
    ) -> StepResult:
        """运行 `boxmethod` 分析网格质量，生成 `_bxm.tdr`。"""
        run_dir, log_dir, _ = self.ensure_dirs(state)
        if shutil.which("boxmethod") is None:
            return StepResult(False, "boxmethod_failed", "Binary not found: boxmethod")

        src = self._resolve_session_path(state, grid_file, default_dir="run")
        if not src.exists():
            return StepResult(False, "boxmethod_failed", f"Grid file missing: {src}")
        # boxmethod 输出文件名按“输入文件 basename + _bxm.tdr”生成，
        # 实际落点通常在当前工作目录（run_dir），而不是源文件目录。
        out_candidates = [
            run_dir / f"{src.stem}_bxm.tdr",
            src.with_name(f"{src.stem}_bxm.tdr"),
            run_dir / f"{src.name}_bxm.tdr",
        ]

        args = ["boxmethod", "-a", algorithm.strip() or "CVPL_AverageBoxMethod", "-numThreads", str(max(1, int(num_threads))), str(src)]
        log_file = log_dir / f"boxmethod_{src.stem}.log"
        rc, _ = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id)
        out_file = next((p for p in out_candidates if p.exists() and p.stat().st_size > 0), out_candidates[0])
        ok = rc == 0 and out_file.exists() and out_file.stat().st_size > 0
        return StepResult(
            ok,
            "boxmethod_done" if ok else "boxmethod_failed",
            "boxmethod mesh-quality analysis done." if ok else f"boxmethod failed: rc={rc}",
            logs={"boxmethod": str(log_file)},
            artifacts={"boxmethod_source": str(src), "boxmethod_tdr": str(out_file)},
            details={"return_code": rc, "algorithm": algorithm, "num_threads": max(1, int(num_threads))},
        )

    def run_logbrowser(
        self,
        state: SessionState,
        xml_log_file: str,
        info_level: int = 1,
        batch: bool = True,
        timeout_s: int = 600,
    ) -> StepResult:
        """运行 `logbrowser` 查看 XML 日志摘要。"""
        run_dir, log_dir, rep_dir = self.ensure_dirs(state)
        if shutil.which("logbrowser") is None:
            return StepResult(False, "logbrowser_failed", "Binary not found: logbrowser")

        src = self._resolve_session_path(state, xml_log_file, default_dir="logs")
        if not src.exists():
            return StepResult(False, "logbrowser_failed", f"XML log file missing: {src}")
        lvl = max(0, min(3, int(info_level)))

        args = ["logbrowser", "-info", str(lvl)]
        if batch:
            args.append("-b")
        args.append(str(src))

        log_file = log_dir / f"logbrowser_{src.stem}.log"
        rc, out = self._run_to_log(args, run_dir, log_file, timeout_s, session_id=state.session_id, capture=True)
        report = rep_dir / f"logbrowser_{src.stem}.txt"
        report.write_text(out, encoding="utf-8", errors="ignore")
        ok = rc == 0 and report.exists() and report.stat().st_size > 0
        return StepResult(
            ok,
            "logbrowser_done" if ok else "logbrowser_failed",
            "logbrowser analysis done." if ok else f"logbrowser failed: rc={rc}",
            logs={"logbrowser": str(log_file)},
            artifacts={"logbrowser_source": str(src), "logbrowser_report": str(report)},
            details={"return_code": rc, "info_level": lvl, "batch": batch},
        )

    # ━━━━━━━━━━━━━━━━ PLT 列解析与轴选择 ━━━━━━━━━━━━━━━

    @staticmethod
    def _parse_plt_columns(path: Path) -> list[str]:
        """解析 PLT 文件中 datasets 列名。

        PLT 文件头包含形如 datasets = ["gate OuterVoltage", "drain TotalCurrent", ...]
        的声明。列名遵循 Sentaurus 约定："电极名 物理量名"，
        例如 "gate OuterVoltage" 表示栅极的外加电压。
        """
        text = path.read_text(encoding="utf-8", errors="ignore")
        low = text.lower()
        idx = low.find("datasets")
        if idx < 0:
            return []
        lb = text.find("[", idx)
        rb = text.find("]", lb + 1) if lb >= 0 else -1
        if lb < 0 or rb < 0:
            return []
        block = text[lb + 1 : rb]
        cols: list[str] = []
        i = 0
        while i < len(block):
            if block[i] != '"':
                i += 1
                continue
            j = block.find('"', i + 1)
            if j < 0:
                break
            val = block[i + 1 : j].strip()
            if val:
                cols.append(val)
            i = j + 1
        return cols

    @staticmethod
    def _parse_plt_table(path: Path) -> dict[str, list[float]]:
        """解析 PLT 文件为按列名索引的数值表。"""
        text = path.read_text(encoding="utf-8", errors="ignore")
        cols = SentaurusOps._parse_plt_columns(path)
        if not cols:
            return {}
        low = text.lower()
        idx = low.find("data")
        if idx < 0:
            return {}
        lb = text.find("{", idx)
        rb = text.rfind("}")
        if lb < 0 or rb < 0 or rb <= lb:
            return {}
        block = text[lb + 1 : rb]
        for ch in ",\n\r\t":
            block = block.replace(ch, " ")
        nums: list[float] = []
        for tok in block.split():
            try:
                nums.append(float(tok))
            except ValueError:
                continue
        ncols = len(cols)
        nrows = len(nums) // ncols
        nums = nums[: nrows * ncols]
        table = {c: [] for c in cols}
        for i in range(nrows):
            row = nums[i * ncols : (i + 1) * ncols]
            for j, c in enumerate(cols):
                table[c].append(row[j])
        return table

    @staticmethod
    def _sanitize_plt_for_svisual(src: Path, run_dir: Path) -> Path:
        """清理 PLT 中的 NUL 字节，避免 svisual 文本解析失败。"""
        try:
            raw = src.read_bytes()
        except Exception:
            return src
        if b"\x00" not in raw:
            return src
        cleaned = raw.replace(b"\x00", b"")
        dst = run_dir / f"{src.stem}_clean{src.suffix}"
        try:
            dst.write_bytes(cleaned)
            return dst
        except Exception:
            return src

    @classmethod
    def _prefer_nonzero_span_voltage_axis(cls, path: Path, preferred: str) -> str:
        """从实际数据中选择有跨度的 X 轴列。"""
        table = cls._parse_plt_table(path)
        if not table:
            return preferred

        def span(col: str) -> float:
            vals = table.get(col, [])
            if not vals:
                return 0.0
            return float(max(vals) - min(vals))

        # 首选列若有跨度，直接使用
        if preferred in table and span(preferred) > 1e-9:
            return preferred

        candidates = [c for c in table.keys() if "voltage" in c.lower()]
        if not candidates:
            candidates = list(table.keys())
        if not candidates:
            return preferred

        best = max(candidates, key=span)
        if span(best) > 1e-9:
            return best
        return preferred or best

    @staticmethod
    def _pick_axes(columns: list[str], simulation_type: str = "") -> tuple[str, str]:
        """基于列名语义的通用轴选择，不绑定特定器件/电极名。"""
        if not columns:
            return "", ""

        x = next((c for c in columns if "voltage" in c.lower()), columns[0])
        y = next((c for c in columns if "current" in c.lower() and c != x), "")
        if not y:
            y = next((c for c in columns if c != x), columns[0])

        return x, y

    # ━━━━━━━━━━━━━━━━ SVisual TCL 脚本生成 ━━━━━━━━━━━━━

    @staticmethod
    def _axis_split(name: str) -> tuple[str, str]:
        """将 'region field' 字符串拆成 region/field。

        Sentaurus TCL API 中 create_curve 的 -axisX/-axisY 参数
        需要 {region field} 格式，因此需将 "gate OuterVoltage"
        拆成 region="gate", field="OuterVoltage"。
        """
        toks = name.split()
        if len(toks) < 2:
            return name, "Value"
        return toks[0], " ".join(toks[1:])

    @classmethod
    def _build_svisual_tcl(
        cls,
        src: Path,
        png: Path,
        mode: str,
        axis_x: str,
        axis_y: str,
        curve_txt: Path,
    ) -> str:
        """按模式生成 svisual TCL 脚本。

        PLT 模式（电学数据）：
          1. 加载 PLT 文件为 dataset D0
          2. 创建 1D 曲线图，指定 X/Y 轴（从 _pick_axes 获得）
          3. 设 Y 轴为对数刻度（IV 曲线跨越数个数量级）
          4. 导出 PNG 图片
          5. 逐点导出 X/Y 数值到文本文件（供验证器解析）

        TDR 模式（结构数据）：
          1. 加载 TDR 文件为 dataset D0
          2. 创建 2D/3D 截面图
          3. 导出 PNG 图片（不导出数值文本）
        """
        if mode == "plt":
            x_region, x_field = cls._axis_split(axis_x)
            y_region, y_field = cls._axis_split(axis_y)
            return (
                f'set src_file "{src}"\n'
                f'set out_png "{png}"\n'
                f'set out_txt "{curve_txt}"\n'
                f'set axis_x "{axis_x}"\n'
                f'set axis_y "{axis_y}"\n'
                'if {[catch {load_file $src_file -name D0} err]} {puts \"ERROR: $err\"; exit 1}\n'
                'set p [create_plot -1d]\n'
                f'if {{[catch {{create_curve -plot $p -dataset D0 -axisX {{{x_region} {x_field}}} -axisY {{{y_region} {y_field}}}}} err]}} {{puts \"ERROR: $err\"; exit 2}}\n'
                'if {[catch {set xs [get_variable_data $axis_x -dataset [list D0]]} err]} {puts \"ERROR: $err\"; exit 4}\n'
                'if {[catch {set ys [get_variable_data $axis_y -dataset [list D0]]} err]} {puts \"ERROR: $err\"; exit 5}\n'
                'set n [llength $xs]\n'
                'set ny [llength $ys]\n'
                'if {$ny < $n} {set n $ny}\n'
                'if {$n < 1} {puts \"ERROR: no points\"; exit 6}\n'
                'set fd [open $out_txt \"w\"]\n'
                'puts $fd \"# x\\ty\"\n'
                'for {set i 0} {$i < $n} {incr i} { puts $fd \"[lindex $xs $i]\\t[lindex $ys $i]\" }\n'
                'close $fd\n'
                'catch {set_axis_prop -plot $p -axis y -type log}\n'
                'if {[catch {export_view $out_png -plots [list $p] -format png -resolution 1400x900} err]} {puts \"ERROR: $err\"; exit 3}\n'
                'exit 0\n'
            )

        return (
            f'set src_file "{src}"\n'
            f'set out_png "{png}"\n'
            'if {[catch {load_file $src_file -name D0} err]} {puts \"ERROR: $err\"; exit 1}\n'
            'set p [create_plot -dataset D0]\n'
            'if {[catch {export_view $out_png -plots [list $p] -format png -resolution 1400x900} err]} {puts \"ERROR: $err\"; exit 2}\n'
            'exit 0\n'
        )
