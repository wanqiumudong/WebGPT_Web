from __future__ import annotations

"""LLM 生成引擎（纯模型驱动版）。

设计目标：
- 不做关键词规则匹配
- 不做 deck 语法硬修补
- 不做本地片段检索/打分筛选
- 仅执行：提示词组装 -> LLM 生成 -> 真实工具校验 -> 失败日志回灌重试
"""

import json
import base64
import mimetypes
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .core import DebugTracer, SkillLibrary, preview_text


DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-72B-Instruct"
BUILD_MESH_RE = re.compile(r"\(sde:build-mesh\s+\"?([^\")\s]+)\"?\)", re.IGNORECASE)
SDE_GEOMETRY_HINTS = (
    "vertex:fillet",
    "cannot calculate normal vector",
    "could_not_fillet",
    "divide by zero",
    "shortest edge:",
    "pm_unbalanced_states",
    "boolean operation",
    "self-intersection",
    "topology",
)


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_s: int = 120
    total_timeout_s: int = 480
    stream: bool = True


def build_config() -> LLMConfig:
    provider = os.getenv("TCAD_LLM_PROVIDER", "siliconflow").strip().lower()
    base_url = os.getenv("TCAD_LLM_BASE_URL", "").strip()
    if not base_url:
        base_url = os.getenv("WEB_FABGPT_TEXT_API_BASE_URL", DEFAULT_SILICONFLOW_BASE_URL).strip()
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]

    api_key = os.getenv("TCAD_LLM_API_KEY", "").strip() or os.getenv("WEB_FABGPT_SILICONFLOW_API_KEY", "").strip()
    model = (
        os.getenv("TCAD_MODEL_MAIN", "").strip()
        or os.getenv("TCAD_LLM_MODEL", "").strip()
        or os.getenv("WEB_FABGPT_TEXT_MODEL", "").strip()
        or DEFAULT_MODEL
    )

    return LLMConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=float(os.getenv("TCAD_LLM_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("TCAD_LLM_MAX_TOKENS", "4096")),
        timeout_s=int(os.getenv("TCAD_LLM_TIMEOUT_S", "120")),
        total_timeout_s=int(os.getenv("TCAD_LLM_TOTAL_TIMEOUT_S", "480")),
        stream=os.getenv("TCAD_LLM_STREAM", "1").strip().lower() not in {"0", "false", "off", "no"},
    )


class OpenAICompatClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        if not cfg.api_key:
            raise RuntimeError("Missing API key.")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        verbose: bool = False,
        model: str | None = None,
    ) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "stream": self.cfg.stream,
        }
        model_name = str(payload["model"]).lower()
        if model_name.startswith("gpt-5"):
            payload["max_completion_tokens"] = self.cfg.max_tokens
        else:
            payload["max_tokens"] = self.cfg.max_tokens

        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

        if not self.cfg.stream:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.cfg.timeout_s)
            if resp.status_code >= 300:
                raise RuntimeError(f"LLM request failed ({resp.status_code}): {resp.text[:800]}")
            data = resp.json()
            try:
                return str(data["choices"][0]["message"]["content"])
            except Exception as exc:
                raise RuntimeError(f"Unexpected LLM response: {data}") from exc

        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=(30, self.cfg.timeout_s),
            stream=True,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"LLM stream request failed ({resp.status_code}): {resp.text[:800]}")

        chunks: list[str] = []
        start_ts = time.monotonic()
        for line in resp.iter_lines():
            if time.monotonic() - start_ts > self.cfg.total_timeout_s:
                raise RuntimeError(f"LLM stream timed out after {self.cfg.total_timeout_s}s.")
            if not line:
                continue
            text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
            if not text.startswith("data:"):
                continue
            data_str = text[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk_data.get("choices") or []
            if not choices:
                continue
            token = choices[0].get("delta", {}).get("content", "")
            if token:
                chunks.append(token)
                if verbose:
                    print(token, end="", flush=True)

        text = "".join(chunks).strip()
        if not text:
            raise RuntimeError("LLM returned empty response.")
        return text


def _tail(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    txt = path.read_text(encoding="utf-8", errors="ignore")
    if len(txt) <= max_chars:
        return txt
    return txt[-max_chars:]


def _tail_text(text: str, max_chars: int = 6000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _extract_error_excerpt(text: str, max_lines: int = 10) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    if not lines:
        return ""
    trigger_indexes: list[int] = []
    for idx, line in enumerate(lines):
        lower = line.lower()
        if "*** error" in lower or "error" == lower.strip() or any(hint in lower for hint in SDE_GEOMETRY_HINTS):
            trigger_indexes.append(idx)
    if not trigger_indexes:
        tail = [line for line in lines[-max_lines:] if line.strip()]
        return "\n".join(tail).strip()
    start = max(0, trigger_indexes[-1] - 1)
    excerpt = [line for line in lines[start : start + max_lines] if line.strip()]
    return "\n".join(excerpt).strip()


def _expected_sde_outputs(deck: Path, run_dir: Path) -> tuple[Path, Path]:
    default_mesh = run_dir / "sde_result_msh.tdr"
    default_bnd = run_dir / "sde_result_bnd.tdr"
    try:
        code = deck.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        code = ""
    match = BUILD_MESH_RE.search(code)
    if match:
        stem = match.group(1).strip().strip('"').strip("'")
        if stem.lower().endswith(".tdr"):
            stem = stem[:-4]
        if stem:
            return run_dir / f"{stem}_msh.tdr", run_dir / f"{stem}_bnd.tdr"
    return default_mesh, default_bnd


def _run_check(cmd: list[str], cwd: Path, log: Path, timeout_s: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        rc = proc.returncode
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    except subprocess.TimeoutExpired as exc:
        rc = 124
        out = (exc.stdout or "") + "\n" + (exc.stderr or "") + f"\n[timeout] command exceeded {timeout_s}s\n"
    log.write_text(out, encoding="utf-8", errors="ignore")
    return rc, out


def extract_answer(raw: str) -> str:
    text = raw.strip()
    low = text.lower()

    # 兼容模型偶发输出的 <answer>（含缺失闭合标签场景）
    # 规则：
    # 1) 有完整 <answer>...</answer> -> 取中间内容
    # 2) 只有开标签 -> 去掉首个开标签后返回其余文本
    # 3) 含行内标签 -> 全量剥离标签文本
    s = low.find("<answer>")
    if s >= 0:
        e = low.find("</answer>", s + len("<answer>"))
        if e > s:
            start = s + len("<answer>")
            return text[start:e].strip()
        start = s + len("<answer>")
        return text[start:].strip()

    if "<answer>" in low or "</answer>" in low:
        cleaned = (
            text.replace("<answer>", "")
            .replace("</answer>", "")
            .replace("<ANSWER>", "")
            .replace("</ANSWER>", "")
        ).strip()
        if cleaned:
            return cleaned

    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            block = parts[1]
            if "\n" in block:
                first, rest = block.split("\n", 1)
                if first.strip() and " " not in first.strip():
                    return rest.strip()
            return block.strip()

    return text


class LLMDeckEngine:
    def __init__(self, workspace: Path, tracer: DebugTracer | None = None):
        self.workspace = workspace
        self.tracer = tracer
        self.skills = SkillLibrary(workspace)
        self.cfg = build_config()
        self.client = OpenAICompatClient(self.cfg)

        self.model_main = os.getenv("TCAD_MODEL_MAIN", "").strip() or self.cfg.model
        self.model_sde = os.getenv("TCAD_MODEL_SDE", "").strip() or self.cfg.model
        self.model_sdevice = os.getenv("TCAD_MODEL_SDEVICE", "").strip() or self.cfg.model

        self.max_attempts = int(os.getenv("TCAD_LLM_MAX_ATTEMPTS", "2"))
        self.verbose_llm = os.getenv("TCAD_VERBOSE_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}

    def _trace(self, action: str, payload: dict[str, Any], session_id: str) -> None:
        if self.tracer:
            self.tracer.event("LLMDeckEngine", action, payload, session_id=session_id)

    def chat_main(self, messages: list[dict[str, str]], *, verbose: bool = False) -> str:
        return self.client.chat(messages, verbose=verbose, model=self.model_main)

    def chat_with_image(self, *, question: str, image_path: Path) -> str:
        """使用主模型做多模态问答（文本 + 本地图片）。"""
        mime, _ = mimetypes.guess_type(str(image_path))
        if not mime:
            mime = "image/png"
        raw = image_path.read_bytes()
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        return self.client.chat(messages, verbose=False, model=self.model_main)

    def _save_prompt_artifacts(
        self,
        logs_dir: Path,
        prefix: str,
        attempt: int,
        system_prompt: str,
        user_prompt: str,
        raw: str,
    ) -> dict[str, str]:
        pdir = logs_dir / "prompts"
        pdir.mkdir(parents=True, exist_ok=True)
        p_sys = pdir / f"{prefix}_attempt{attempt}_system.txt"
        p_usr = pdir / f"{prefix}_attempt{attempt}_user.txt"
        p_raw = pdir / f"{prefix}_attempt{attempt}_assistant_raw.txt"
        p_sys.write_text(system_prompt, encoding="utf-8", errors="ignore")
        p_usr.write_text(user_prompt, encoding="utf-8", errors="ignore")
        p_raw.write_text(raw, encoding="utf-8", errors="ignore")
        return {
            f"{prefix}_prompt_system": str(p_sys),
            f"{prefix}_prompt_user": str(p_usr),
            f"{prefix}_prompt_raw": str(p_raw),
        }

    def generate_sde(
        self,
        *,
        session_id: str,
        requirement: str,
        reference_context: str,
        device_type: str,
        simulation_type: str,
        parameters: dict[str, float],
        run_dir: Path,
        logs_dir: Path,
    ) -> dict[str, Any]:
        system_prompt = self.skills.load("sde_codegen")
        feedback = ""
        last_failure: dict[str, Any] = {
            "failure_stage": "sde_generation_failed",
            "failure_summary": "SDE 代码生成失败。",
            "error": "SDE 代码生成失败。",
            "debug_artifacts": {},
        }

        for attempt in range(1, self.max_attempts + 1):
            user_prompt = (
                f"需求:\n{requirement}\n\n"
                f"device_type={device_type}\n"
                f"simulation_type={simulation_type}\n"
                f"parameters={json.dumps(parameters, ensure_ascii=False)}\n\n"
                f"参考上下文:\n{preview_text(reference_context or '(未命中参考)', 5000)}\n\n"
                "只输出可运行的 SDE Scheme deck 纯文本。\n"
                "输出必须从 `(sde:clear)` 开始，不要任何 XML/HTML 标签，不要 markdown 代码块。\n"
                "只生成一个方案，不要 A/B 多方案分支，不要 if-else 切换结构方案。\n"
                "必须包含接触、掺杂和网格，并执行 `(sde:build-mesh ...)`。\n"
                "若用户明确指定输出名，必须严格使用该输出名；未指定时再使用 `sde_result`。\n"
                "不要输出解释文字。\n\n"
                f"上次失败日志(如有):\n{feedback}\n"
            )

            self._trace(
                "sde_prompt_ready",
                {
                    "attempt": attempt,
                    "system_prompt": preview_text(system_prompt, 1200),
                    "user_prompt": preview_text(user_prompt, 1800),
                },
                session_id,
            )

            try:
                raw = self.client.chat(
                    [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    verbose=self.verbose_llm,
                    model=self.model_sde,
                )
            except Exception as exc:
                feedback = f"[llm_call_error]\n{exc}\n"
                self._trace("sde_attempt_llm_error", {"attempt": attempt, "error": str(exc)}, session_id)
                last_failure = {
                    "failure_stage": "sde_generation_failed",
                    "failure_summary": f"SDE 代码生成阶段失败：LLM 调用未成功（{exc}）。",
                    "error": f"SDE 代码生成阶段失败：LLM 调用未成功（{exc}）。",
                    "debug_artifacts": {},
                }
                continue

            artifacts = self._save_prompt_artifacts(logs_dir, "sde", attempt, system_prompt, user_prompt, raw)
            code = extract_answer(raw)
            deck = run_dir / "sde_dvs.cmd"
            deck.write_text(code, encoding="utf-8")

            syntax_log = logs_dir / f"sde_attempt{attempt}_syntax.log"
            run_log = logs_dir / f"sde_attempt{attempt}_run.log"
            rc_syntax, _ = _run_check(["sde", "-S", deck.name], run_dir, syntax_log, 300)
            rc_run, _ = _run_check(["sde", "-e", "-l", deck.name], run_dir, run_log, 1800)

            mesh, bnd = _expected_sde_outputs(deck, run_dir)
            mesh_ok = mesh.exists() and mesh.stat().st_size > 0

            self._trace(
                "sde_attempt_done",
                {
                    "attempt": attempt,
                    "rc_syntax": rc_syntax,
                    "rc_run": rc_run,
                    "mesh_ok": mesh_ok,
                    "mesh_path": str(mesh),
                },
                session_id,
            )

            if rc_syntax == 0 and rc_run == 0 and mesh_ok:
                return {
                    "ok": True,
                    "attempt": attempt,
                    "path": str(deck),
                    "debug_artifacts": {
                        **artifacts,
                        "sde_check_log": str(syntax_log),
                        "sde_run_log": str(run_log),
                        "mesh": str(mesh),
                        "bnd": str(bnd),
                    },
                }

            syntax_text = _tail(syntax_log)
            run_text = _tail(run_log)
            feedback = "[syntax]\n" + syntax_text + "\n\n[run]\n" + run_text

            debug_artifacts = {
                **artifacts,
                "sde_cmd": str(deck),
                "sde_check_log": str(syntax_log),
                "sde_run_log": str(run_log),
                "mesh": str(mesh),
                "bnd": str(bnd),
            }
            if rc_syntax != 0:
                excerpt = _extract_error_excerpt(syntax_text) or "请查看语法检查日志。"
                last_failure = {
                    "failure_stage": "sde_check_failed",
                    "failure_summary": "SDE 代码已生成，但语法检查未通过。",
                    "error": (
                        "SDE 代码已生成，但语法检查未通过。\n\n"
                        f"关键报错：\n{excerpt}\n\n"
                        f"语法日志：{syntax_log}"
                    ),
                    "debug_artifacts": debug_artifacts,
                }
                continue

            run_excerpt = _extract_error_excerpt(run_text) or "请查看执行日志。"
            run_lower = run_text.lower()
            if any(hint in run_lower for hint in SDE_GEOMETRY_HINTS):
                failure_summary = "SDE 代码已生成并通过语法检查，但结构执行在几何构造阶段失败。"
            elif rc_run != 0:
                failure_summary = "SDE 代码已生成并通过语法检查，但执行阶段失败。"
            else:
                failure_summary = "SDE 代码已生成并通过语法检查，但未成功产出 mesh 文件。"

            last_failure = {
                "failure_stage": "sde_failed",
                "failure_summary": failure_summary,
                "error": (
                    f"{failure_summary}\n\n"
                    f"关键报错：\n{run_excerpt}\n\n"
                    f"执行日志：{run_log}"
                ),
                "debug_artifacts": debug_artifacts,
            }

        return {
            "ok": False,
            "attempt": self.max_attempts,
            "failure_stage": str(last_failure.get("failure_stage") or "sde_generation_failed"),
            "failure_summary": str(last_failure.get("failure_summary") or "SDE 代码生成失败。"),
            "error": str(last_failure.get("error") or "SDE 代码生成失败。"),
            "debug_artifacts": dict(last_failure.get("debug_artifacts") or {}),
        }

    def generate_sdevice(
        self,
        *,
        session_id: str,
        requirement: str,
        reference_context: str,
        device_type: str,
        simulation_type: str,
        parameters: dict[str, float],
        sde_code: str,
        run_dir: Path,
        logs_dir: Path,
    ) -> dict[str, Any]:
        system_prompt = self.skills.load("sdevice_codegen")
        feedback = ""

        tdx_info = ""
        tdx_log = logs_dir / "sdevice_tdx_info.log"
        deck = run_dir / "sde_dvs.cmd"
        mesh, _ = _expected_sde_outputs(deck, run_dir)
        if mesh.exists():
            _run_check(["tdx", "-info", mesh.name], run_dir, tdx_log, 300)
            tdx_info = _tail(tdx_log, 20000)

        for attempt in range(1, self.max_attempts + 1):
            user_prompt = (
                f"需求:\n{requirement}\n\n"
                f"device_type={device_type}\n"
                f"simulation_type={simulation_type}\n"
                f"parameters={json.dumps(parameters, ensure_ascii=False)}\n\n"
                f"参考上下文:\n{preview_text(reference_context or '(未命中参考)', 4000)}\n\n"
                "请生成可运行的 Sentaurus Device cmd。\n"
                "仅输出纯 cmd 文本，不要任何标签，不要 markdown 代码块，不要解释。\n"
                f"Grid 必须引用当前结构生成得到的网格文件 `{mesh.name}`。\n"
                "Electrode Name 必须与网格接触名完全一致（大小写敏感）。\n\n"
                "Physics 段必须严格限定为：EffectiveIntrinsicDensity(OldSlotboom) + Mobility(DopingDep/eHighFieldSaturation/hHighFieldSaturation) + Recombination(SRH(DopingDep TempDependence))。\n"
                "不要添加任何额外模型行（尤其不要输出 BandGapNarrowing(...)）。\n\n"
                "SDE deck 摘要:\n"
                f"{preview_text(sde_code, 6000)}\n\n"
                "TDR 信息摘要:\n"
                f"{preview_text(tdx_info, 12000)}\n\n"
                f"上次失败日志(如有):\n{feedback}\n"
            )

            self._trace(
                "sdevice_prompt_ready",
                {
                    "attempt": attempt,
                    "system_prompt": preview_text(system_prompt, 1200),
                    "user_prompt": preview_text(user_prompt, 1800),
                },
                session_id,
            )

            try:
                raw = self.client.chat(
                    [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    verbose=self.verbose_llm,
                    model=self.model_sdevice,
                )
            except Exception as exc:
                feedback = f"[llm_call_error]\n{exc}\n"
                self._trace("sdevice_attempt_llm_error", {"attempt": attempt, "error": str(exc)}, session_id)
                continue

            artifacts = self._save_prompt_artifacts(logs_dir, "sdevice", attempt, system_prompt, user_prompt, raw)
            code = extract_answer(raw)
            deck = run_dir / "sdevice_des.cmd"
            deck.write_text(code, encoding="utf-8")

            check_log = logs_dir / f"sdevice_attempt{attempt}_check.log"
            rc_check, _ = _run_check(["sdevice", "-P", deck.name], run_dir, check_log, 900)

            self._trace(
                "sdevice_attempt_done",
                {"attempt": attempt, "rc_check": rc_check, "sdevice_path": str(deck)},
                session_id,
            )

            if rc_check == 0:
                dbg = {**artifacts, "sdevice_check_log": str(check_log)}
                if tdx_info:
                    dbg["sdevice_tdx_info_log"] = str(tdx_log)
                return {
                    "ok": True,
                    "attempt": attempt,
                    "path": str(deck),
                    "debug_artifacts": dbg,
                }

            feedback = _tail(check_log)

        return {
            "ok": False,
            "attempt": self.max_attempts,
            "error": (
                f"SDevice 生成失败（{self.max_attempts} 次尝试均未通过）。\n\n"
                f"最后一次错误：\n{feedback}\n\n"
                f"请检查日志：{logs_dir}"
            ),
            "debug_artifacts": {},
        }
