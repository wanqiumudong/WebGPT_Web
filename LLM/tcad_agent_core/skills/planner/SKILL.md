---
name: tcad-planner
description: Use when selecting the next minimal MCP tool step from current TCAD stage and artifacts, while avoiding unnecessary full-flow execution.
---

# TCAD Planner

## Overview

Plan the next executable MCP step from user intent + current `stage` + available artifacts.
Prefer the smallest valid step set, not a mechanical full pipeline.

## Available Operations

```
create_session
show_state
generate_sde_code
check_sde_syntax
run_sde
run_svisual_sde_export
inspect_tdr
generate_sdevice_code
check_sdevice_syntax
run_sdevice
run_svisual_export
validate_results
```

## Planning Workflow

1. Determine intent type:
   - language-only question → no tool call
   - local task (for example structure image only) → local chain only
   - explicit electrical simulation task → full chain up to validation
2. Respect dependencies:
   - `generate_sdevice_code` usually after `inspect_tdr`
   - `run_sdevice` after `check_sdevice_syntax`
   - `run_svisual_export` after `run_sdevice`
   - `validate_results` after `run_svisual_export`
3. Resume from checkpoint:
   - skip already-completed steps when artifacts are reusable
   - avoid restarting upstream flow without a concrete reason

## SDevice Trigger Guidance

Use SDevice-related tools only when simulation intent is explicit (for example `IdVg/IdVd/IV`, electrical metrics, `validate`, direct request to run SDevice).

If user input mainly describes structure/process parameters, or only mentions performance in generic terms, keep planning on SDE-side by default.

When uncertain, prefer:
- SDE completion (`generate/check/run/inspect/export-structure`)
- then a short clarification on whether to continue with electrical simulation

## Quality Bar

- Do not always start from `generate_sde_code`.
- Do not always drive to `validate_results`.
- Do not output steps without dependency safety.
- Keep reason text short, traceable, and aligned with the current user turn.
