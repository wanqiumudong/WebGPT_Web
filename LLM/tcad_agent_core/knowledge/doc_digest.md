# Sentaurus TCAD 核心知识摘要

> 本文档为 TCAD Agent 的领域知识基线，基于 Sentaurus™ 2024.03 官方 User Guide 与 Training 文档整理。
> 供开发者理解系统设计决策，以及为 LLM prompt 工程提供上下文参考。

---

## 1. SDE（Structure Editor）—— 器件结构与网格定义

### 1.1 坐标系与几何约定

- 2D 结构使用 `z=0` 平面，必须调用 `(sde:set-process-up-direction "+z")` 确保正方向一致。
- 坐标体系：X 为水平方向（源→漏），Y 为垂直方向（表面→衬底深处）。
- 几何操作推荐使用 `sdegeo:create-rectangle`（2D）或 `sdegeo:create-cuboid`（3D）。
- 布尔操作模式通过 `(sdegeo:set-default-boolean "ABA")` 设定——后创建的几何体在重叠区域覆盖先创建的。
- 每个 deck 的开头必须有 `(sde:clear)` 清空前序状态。

### 1.2 材料与区域命名规范

| 标准材料名    | 注意事项                                      |
|--------------|---------------------------------------------|
| `Silicon`    | 首字母大写                                   |
| `SiO2`       | 标准氧化层                                   |
| `PolySilicon`| 大小写严格，`Polysilicon`/`Poly` 均不合法     |
| `Nitride`    | Si3N4                                        |
| `GaN`/`AlGaN`| III-V 材料                                   |

区域命名应语义化：`Body`、`Channel`、`Source`、`Drain`、`Gateoxide`、`Gate` 等。

**命名隔离策略（推荐后缀，避免名称冲突）：**
- `_Reg` → 区域名（`sdegeo:create-rectangle` 的第 4 参数）
- `_Prof` → 掺杂分布名（`define-constant-profile` 的第 1 参数）
- `_Win` → 网格窗口名（`define-refeval-window` 的第 1 参数）
- `_Place` → 放置关联名（`define-refinement-placement` / `define-constant-profile-region` 的第 1 参数）

### 1.3 接触定义

- **正确 API**：`(sdegeo:set-contact (find-edge-id (position x y z)) "contact_name")`
- 2D 用 `find-edge-id`，3D 用 `find-face-id`。
- 标准接触名（小写）：

| 器件类型    | 接触集合                                    |
|-----------|-------------------------------------------|
| NMOS/PMOS | `source`、`drain`、`gate`、`substrate`      |
| 二极管      | `anode`、`cathode`                         |
| BJT/HBT   | `emitter`、`base`、`collector`             |

- ⚠ `sdegeo:create-contact` **不存在**，LLM 常误生成此 API，必须在 sanitize 阶段自动修正为 `set-contact`。
- ⚠ `find-edge-id` 只接受 `(position ...)` 一个参数，不能在前面多传区域名。

### 1.4 掺杂定义

**常量掺杂（两步式）：**
```scheme
(sdedr:define-constant-profile "Prof_Name" "Species" Value)
(sdedr:define-constant-profile-region "Place_Name" "Prof_Name" "Region_Name")
```

**高斯掺杂（带空间分布）：**
```scheme
(sdedr:define-gaussian-profile "Prof_Name" "Species" PeakConc PeakPos StdDev)
(sdedr:define-refinement-window "Win_Name" "Line" (position ...) (position ...))
(sdedr:define-analytical-profile-placement "Place_Name" "Prof_Name" "Win_Name" ...)
```

**常见掺杂物种：**

| 类型   | 物种名                          | 用途          |
|-------|-------------------------------|-------------|
| N 型  | `PhosphorusActiveConcentration` | NMOS 源漏     |
| N 型  | `ArsenicActiveConcentration`    | 重掺杂替代     |
| P 型  | `BoronActiveConcentration`      | PMOS 源漏/NMOS衬底 |

- NMOS：源漏用 `PhosphorusActiveConcentration`（n 型），衬底/沟道用 `BoronActiveConcentration`（p 型）。
- PMOS：反之。

### 1.5 网格策略

**三步式定义：**
```scheme
; 1. 定义评估窗口（区域范围）
(sdedr:define-refeval-window "Win" "Rectangle" (position x1 y1 0) (position x2 y2 0))
; 2. 定义网格尺寸（最大/最小）
(sdedr:define-refinement-size "Def" maxX maxY maxZ minX minY minZ)
; 3. 绑定窗口与尺寸
(sdedr:define-refinement-placement "Place" "Def" "Win")
```

**自适应加密函数（可选，推荐）：**
```scheme
; 界面加密：Si/SiO2 界面两侧自动加密
(sdedr:define-refinement-function "Def" "MaxLenInt" "Silicon" "SiO2" 0.002 1.3 "DoubleSide")
; 掺杂梯度加密：按浓度梯度自适应
(sdedr:define-refinement-function "Def" "DopingConcentration" "MaxTransDiff" 1)
```

**最终构建：**
```scheme
(sde:build-mesh "sde_result")
; → 输出 sde_result_msh.tdr（网格）+ sde_result_bnd.tdr（边界）
```

### 1.6 LLM 常见错误清单（sanitize 目标）

| 错误类型               | 修正方式                                          |
|-----------------------|--------------------------------------------------|
| 调用 `create-contact`  | 替换为 `set-contact + find-edge-id`               |
| `set-contact` 参数反转  | 交换为 `(set-contact (finder) "name")`            |
| `find-edge-id` 多传参   | 去掉区域名参数，只保留 `(position ...)`            |
| 材料名 `Poly`/`Polysilicon` | 统一为 `PolySilicon`                          |
| `define-constant-profile` 多参数 | 拆分为标准 3 参数 + region 绑定            |
| 缺少初始化头部          | 补充 `sde:clear` + `set-process-up-direction` + `set-default-boolean` |
| 不稳定的网格定义        | 替换为标准化全局+沟道两级网格块                     |

---

## 2. SDevice —— 器件仿真求解

### 2.1 输入文件结构（六大 Section）

```tcl
File {
  Grid    = "sde_result_msh.tdr"   # 必须引用 SDE 输出的网格
  Plot    = "IdVg_des.tdr"         # 场分布输出
  Current = "IdVg_des.plt"         # I-V 曲线输出
  Output  = "IdVg_des.log"         # 仿真日志
}

Electrode {
  { Name="source"    Voltage=0.0 }
  { Name="drain"     Voltage=0.0 }
  { Name="gate"      Voltage=0.0  WorkFunction=4.8 }   # gate 需要设置工作函数
  { Name="substrate" Voltage=0.0 }
}

Physics {
  EffectiveIntrinsicDensity(OldSlotboom)                # 带隙收窄
  Mobility(DopingDep eHighFieldSaturation hHighFieldSaturation)  # 迁移率模型
  Recombination(SRH(DopingDep TempDependence))          # 复合模型
}

Math {
  Number_Of_Threads=4
  Extrapolate       # 偏置扫描外推加速
  Derivatives
  RelErrControl     # 相对误差控制
  Digits=5
  Iterations=60     # 每步最大迭代数
  LineSearchDamping=1e-3
  NotDamped=100
}

Plot {
  eDensity hDensity
  TotalCurrent/Vector eCurrent/Vector hCurrent/Vector
  eMobility/Element hMobility/Element
  ElectricField/Vector Potential SpaceCharge
  Doping DonorConcentration AcceptorConcentration
}

Solve { ... }
```

### 2.2 求解顺序（Solve Block）

```
1. 初始化    → Coupled(Iterations=100){ Poisson }         # 先求静电平衡
2. 耦合求解  → Coupled{ Poisson Electron Hole }            # 加入载流子输运
3. 第一段扫描 → Quasistationary(...Goal{...})              # 施加第一个偏压
4. 前缀重置  → NewCurrentPrefix="result_"                  # 第二段曲线独立命名
5. 第二段扫描 → Quasistationary(... CurrentPlot(...))       # 施加第二个偏压并记录曲线
```

### 2.3 IdVg vs IdVd 扫描差异

| 仿真类型 | 第一段扫描      | 第二段扫描（含 CurrentPlot） |
|---------|----------------|--------------------------|
| IdVg    | drain → Vd     | gate → Vg（输出曲线）      |
| IdVd    | gate → Vg      | drain → Vd（输出曲线）     |

### 2.4 收敛控制关键参数

| 参数                | 推荐范围        | 说明                          |
|--------------------|----------------|-------------------------------|
| `InitialStep`      | ≤ 0.02         | 初始步长，过大导致发散           |
| `MaxStep`          | ≤ 0.05         | 最大步长                      |
| `MinStep`          | ≥ 1e-5         | 最小步长，过小导致无限循环       |
| `Increment`        | 1.2 ~ 2.0      | 步长增长因子                   |
| `Iterations`       | 60 ~ 100       | 每步最大迭代数                  |
| `LineSearchDamping` | 1e-3           | 线搜索阻尼                    |

### 2.5 物理模型安全策略

- **默认启用**：`EffectiveIntrinsicDensity`、`Mobility(DopingDep)`、`Recombination(SRH)`
- **按需启用**：`eQuantumPotential`/`hQuantumPotential`（仅在用户明确要求量子效应时）
- **默认禁止**：`Hydrodynamic`（不稳定，需用户明确指定）

### 2.6 电极名称一致性

- SDevice 中的 `Name="..."` 必须与 SDE 中的 `set-contact ... "..."` **完全匹配**（大小写敏感）。
- 常见别名需在 sanitize 阶段统一：`Bulk`/`bulk` → `substrate`，`Source`→`source`。

---

## 3. SVisual —— 结果可视化与数据导出

### 3.1 批处理脚本流程（PLT 模式）

```tcl
load_file "result.plt" -name D0
set p [create_plot -1d]
create_curve -plot $p -dataset D0 -axisX {gate OuterVoltage} -axisY {drain TotalCurrent}
set_axis_prop -plot $p -axis y -type log              # 对数坐标显示电流
export_view "output.png" -plots [list $p] -format png -resolution 1400x900
```

### 3.2 数据提取（供自动验证）

```tcl
set xs [get_variable_data "gate OuterVoltage" -dataset [list D0]]
set ys [get_variable_data "drain TotalCurrent" -dataset [list D0]]
# 输出为制表符分隔的文本文件，供 Python 端解析
```

### 3.3 PLT 文件格式

```
datasets = [ "gate OuterVoltage" "drain TotalCurrent" "source TotalCurrent" ... ]
Data {
  0.000  1.234e-12  -1.234e-12 ...
  0.050  2.345e-11  -2.345e-11 ...
  ...
}
```

### 3.4 TDR 可视化

- TDR 用于结构/场分布的 2D/3D 可视化。
- 使用 `create_plot -dataset D0`（不带 `-1d` 标志）。

---

## 4. TDX —— 结构检查工具

### 4.1 `tdx -info` 输出格式

```
Dimension : 2
Vertices  : 15432
Elements  : 30200
Regions   : 8
States    : 1
  1: Silicon     <region>
  2: SiO2        <region>
  3: PolySilicon  <region>
  4: gate         <contact>
  5: source       <contact>
  6: drain        <contact>
  7: substrate    <contact>
```

### 4.2 在自动化中的用途

1. **维度验证**：确认 `Dimension` 与用户需求匹配（2D/3D）。
2. **接触提取**：从 `<contact>` 标记行提取接触名，与 SDevice Electrode 段对照。
3. **材料检测**：排查不合法材料别名（如 `Poly` 应为 `PolySilicon`）。
4. **区域计数**：确认区域数量与预期结构复杂度匹配。

---

## 5. Agent 自动化判据

### 5.1 SDE 阶段通过条件

1. `sde -S`（语法检查）返回码 = 0
2. `sde -e -l`（执行 + 产出网格）返回码 = 0
3. `sde_result_msh.tdr` 存在且非空
4. `tdx -info` 显示正确的接触集合（如 MOS 需有 source/drain/gate/substrate 四个）
5. 无已知问题材料名

### 5.2 SDevice 阶段通过条件

1. `sdevice -P`（参数预检查）返回码 = 0
2. `sdevice --exit-on-failure`（真实仿真）返回码 = 0
3. 输出 `.plt` 文件存在且非空

### 5.3 最终验证通过条件

1. PLT 可解析，含电压列和电流列
2. 曲线点数 ≥ 5
3. X/Y 跨度非零
4. 动态范围有效（Ion/Ioff > 1.001）
5. SDE ↔ SDevice 接触名对齐
6. 材料与用户需求匹配
7. 维度与用户需求匹配
8. 若用户设定目标阈值（Ion/Ioff/SS），需满足

### 5.4 容错策略

- LLM 生成最多重试 `TCAD_LLM_MAX_ATTEMPTS`（默认 2）次。
- 每次重试会将上次的失败日志（语法错误 + 运行日志 + 接触检查结果）回灌给 LLM。
- 重试用尽后进入 **fallback deck**（硬编码的已验证模板），支持 NMOS/PMOS/Diode × IdVg/IdVd。
- **sanitize 阶段**在每次 LLM 输出后自动修复已知错误模式（详见 `_sanitize_sde`/`_sanitize_sdevice`）。

### 5.5 SVisual 双通道策略（基于 user_guide/svisual_ug.pdf）

1. **结构通道（SDE）**  
   - 输入：`.tdr`/`.msh.tdr`  
   - 执行：`load_file -> create_plot -dataset -> export_view`  
   - MCP：`run_svisual_sde_export(source_file, mode=\"tdr\")`
2. **曲线通道（SDevice）**  
   - 输入：`.plt`  
   - 执行：`load_file -> create_plot -1d -> create_curve -> export_view`  
   - MCP：`run_svisual_export(source_file, mode=\"plt\")`
3. **批处理运行约束**  
   - 图形导出必须使用虚拟 X：`svisual -batchx/-bx script.tcl`
- 仅脚本计算可用 `svisual -batch script.tcl`

### 5.6 其他 user_guide 约束（SDE/SDevice/TDX）

1. **SDE（sde_ug.pdf）**  
   - 结构脚本必须包含可落地网格构建：`sde:build-mesh`
   - 网格控制建议显式包含 `sdedr:define-refinement-size` / `sdedr:define-refinement-function`
   - 电极接触建议显式使用 `sdegeo:set-contact` 或 `sdegeo:define-contact-set`
2. **SDevice（sdevice_ug.pdf）**  
   - deck 必须具备 `File/Grid`、`Electrode`、`Physics`、`Solve` 主段
   - 曲线导出建议在 `Solve` 中配置 `CurrentPlot`（便于 PLT 曲线验证）
3. **TDX（tdx_ug.pdf）**  
   - `tdx -info` 是结构维度/区域/接触的标准只读检查入口
   - 若仅 `sde -S` 或 `sdevice -P` 通过，仍不能替代真实运行与结果验证

---

## 6. 工具链版本与环境

| 组件            | 版本/配置                                        |
|----------------|------------------------------------------------|
| Sentaurus TCAD | 2024.03（Synopsys）                              |
| 必需二进制      | `sde`、`sdevice`、`svisual`、`tdx`（需在 PATH 中）|
| LLM 后端       | `Qwen/Qwen2.5-72B-Instruct`（SiliconFlow API）   |
| Python 依赖    | `requests`、`mcp`（FastMCP）                      |
| 环境变量       | `TCAD_LLM_API_KEY`、`TCAD_DEBUG`（可选覆盖）      |

---

## 7. 项目目录结构

```
code/
├── main.py                  # 统一入口（默认交互）
├── src/
│   ├── core.py              # 数据结构、需求解析、追踪器、技能加载
│   ├── agent_system.py      # 主流程编排（10 步标准管线）
│   ├── llm_engine.py        # LLM 生成引擎（prompt组装→调用→sanitize→fallback）
│   ├── sentaurus_ops.py     # Sentaurus 二进制执行封装
│   └── validate.py          # 物理级联合验证（结构+曲线+指标）
├── mcp/
│   ├── stdio_server.py      # MCP 协议启动层
│   ├── tool_service.py      # MCP 工具实现层
│   └── start_mcp.sh         # Shell 启动脚本
├── skills/                  # LLM 角色提示词
│   ├── sde_codegen/SKILL.md
│   ├── sdevice_codegen/SKILL.md
│   └── ...
├── knowledge/
│   └── doc_digest.md        # ← 本文档
└── runtime/default/         # 运行时产物（不入版本控制）
    ├── run/                 # SDE/SDevice 输入输出
    ├── logs/                # 执行日志 + prompt 存档
    └── reports/             # 验证报告 + 可视化输出
```

---

## 8. User Guide 到 MCP 能力映射（2026-02-25 增补）

本轮对 `doc/user_guide` 的 6 本手册逐项梳理后，新增/强化了以下 MCP 能力：

### 8.1 `sde_ug.pdf` 对应能力

- `check_sde_syntax`：`sde -S` 纯语法检查
- `check_and_run_sde`：`sde -Sl`（语法通过后直接执行）
- `run_sde`：`sde -e -l` 执行并生成网格

### 8.2 `sdevice_ug.pdf` 对应能力

- `check_sdevice_syntax`：`sdevice -P` 预检查
- `dump_sdevice_parameters`：`sdevice -P / -P:Material / -P:All / -P filename`
- `dump_sdevice_library`：`sdevice -L` 参数库导出
- `list_sdevice_parameter_names`：`sdevice --parameter-names`
- `list_sdevice_field_names`：`sdevice --field-names`
- `list_sdevice_versions`：`sdevice -versions`

### 8.3 `svisual_ug.pdf` 对应能力

- `run_svisual_sde_export`：结构图导出（TDR/mesh）
- `run_svisual_export`：电学曲线导出（PLT）
- `run_svisual_tcl_script`：自定义 Tcl 批处理（`svisual -bx -tcl -s`）
- `run_svisual_cutline_export`：cutline 数据导出（CSV + PNG）
  - 对 3D TDR 自动尝试 `cutplane -> cutline` 回退路径

### 8.4 `tdx_ug.pdf` 对应能力

- `inspect_tdr`：`tdx -info`
- `tdx_convert`：`tdx --<command>` 通用转换（tdr/tif/dfise/plx/ivl/tdf）
- `tdx_change_coordinate_system`：`tdx --tdr-change-cs`
- `tdx_mirror_tdr`：`tdx --mirr-tdr`
- `tdx_tclcmd`：`tdx -tclcmd`

### 8.5 `utilities_ug.pdf` 对应能力

- `run_boxmethod`：`boxmethod` 网格质量分析
- `run_logbrowser`：`logbrowser` XML 日志解析

### 8.6 `smesh_ug.pdf` 状态说明

- 手册已梳理 `snmesh` 命令与章节能力（AxisAligned/Offsetting/Tensor/Tools）。
- 当前机器 `PATH` 中无 `snmesh` 二进制，因此本轮未把 `smesh` 执行型 MCP 设为默认可用工具。
- 后续如环境提供 `snmesh`，可按现有模式直接增补 `run_smesh`/`run_smesh_tools` MCP。
