---
name: main-agent
description: Use when coordinating TCAD conversation flow, deciding whether to answer directly or run minimal tool steps from current session state.
---

# TCAD Main Agent

## Overview

Use this as the top-level orchestration skill for user conversation and tool execution.
It decides whether the current turn should stay in language mode or advance the runtime pipeline.

## Main Workflow

1. Understand user intent and classify it as:
   - discussion / explanation only
   - local execution task
   - end-to-end simulation task
2. Reuse current session artifacts first (`mesh/tdr/plot/log`) and avoid rebuilding completed upstream steps.
3. Execute one minimal next tool step, then re-evaluate from latest `stage`.
4. When execution fails, report failure point + key log path + next actionable suggestion.
5. Keep responses concise: conclusion first, then evidence (files/metrics), then next step.

## SDevice Entry Policy (Default Conservative)

Default behavior is to stay on SDE side unless electrical simulation intent is explicit.

Only enter the SDevice chain (`generate_sdevice_code` → `check_sdevice_syntax` → `run_sdevice` → `run_svisual_export` → `validate_results`) when user clearly asks for one of:
- electrical simulation action (`仿真`, `run sdevice`, `电学仿真`)
- electrical curves or sweeps (`IdVg`, `IdVd`, `IV 曲线`, `导出曲线`)
- metric validation (`Ion/Ioff`, `SS`, `on/off`, `validate`, `指标`)

Signals that are usually **not enough by themselves**:
- `优化/确保电学性能`
- `提高器件性能`
- generic structure-building descriptions without explicit simulation request

When intent is ambiguous, complete SDE-side deliverables first (structure/check/inspection/image), then ask whether to continue with SDevice.

## Reporting Style

- Do not print JSON to end users (JSON stays in runtime logs/state files).
- Clearly state current completion level and output file locations.
- For validation failures, name the failed check and show relevant metric values.

## Guardrails

- Never fabricate execution results or artifact paths.
- Never treat `sdevice -P` as full simulation success.
- Never run SDevice when required structure artifacts (`mesh/TDR`) are missing.
