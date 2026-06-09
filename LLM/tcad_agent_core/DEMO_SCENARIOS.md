# Demo Scenarios（非固定流程）

这个文件只给出“可复制的用户输入场景”，
不绑定固定 workflow。主 Agent 会按当前状态与用户意图自主决策是否调用工具。

## 使用方式

```bash
cd /data/yphu/TCAD_Agent/code
python3 main.py
```

然后把下面任意需求直接粘贴给 client。

---

## 场景 A：NMOS 全流程

```text
参考 Dataset 的 2D NMOS 风格，生成包含 Si 衬底、SiO2 栅氧和多晶硅栅的器件，源漏高掺杂、沟道轻掺杂并进行沟道/界面网格加密；随后生成 SDevice 完成 IdVg 仿真并验证。
```

预期：
- 触发工具链：SDE -> SDevice -> SVisual -> Validate
- 最终 `stage` 进入 `validated` 或给出失败原因与日志路径。

---

## 场景 B：只做结构生成与检查

```text
我现在只需要 SDE 结构，不做电学仿真。请生成并检查 SDE，重点确认材料、接触和网格加密是否合理。
```

预期：
- 主 Agent 可能只调用 `generate_sde_code/check_sde_syntax/run_sde/inspect_tdr`。
- 不一定进入 SDevice。

---

## 场景 C：已有结构，仅继续仿真

```text
我已经有可用 mesh，请继续完成 SDevice 仿真、导出曲线并验证。
```

预期：
- 主要触发 `generate_sdevice_code/check_sdevice_syntax/run_sdevice/run_svisual_export/validate_results`。

---

## 场景 D：纯问答（不调用工具）

```text
请先解释一下 IdVg 和 IdVd 曲线在器件物理上分别反映什么。
```

预期：
- Agent 可以只做语言回答（`assistant_reply`），不触发工具。

---

## 场景 E：二极管 I-V

```text
生成 2D PN 二极管，anode/cathode 接触，P/N 区掺杂各 1e18，完成 I-V 仿真并验证。
```

---

## 场景 F：FinFET 截面

```text
参考 Dataset FinFET 风格，生成 2D 双栅 FinFET 截面：左右源漏扩展区、中间沟道、上下栅氧+栅金属，定义源漏高掺杂并完成 IdVg 仿真与验证。
```

---

## 结果查看

运行后主要看：
- `runtime/default/state.json`
- `runtime/default/logs/debug_trace.jsonl`
- `runtime/default/reports/validation.json`
- `runtime/default/reports/*.png`
- `runtime/default/reports/*_curve.txt`
