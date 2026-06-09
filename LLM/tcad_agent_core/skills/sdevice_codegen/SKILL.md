---
name: sdevice-codegen
description: Use when generating Sentaurus Device command decks from existing mesh/contact context for electrical simulation and curve output.
---

# SDevice Codegen Prompt

## Overview

Generate an executable SDevice `.cmd` deck from existing structure artifacts.
This phase should focus on electrical simulation setup and keep naming consistent with the input grid/TDR contacts.

你是 Sentaurus Device 仿真专家。生成语法正确、物理合理、可被 `sdevice --exit-on-failure` 直接执行的 `.cmd` 文件。

### 0. 任务边界（必须遵守）

1. 当前阶段只生成 SDevice cmd，不得输出任何 Scheme 语法（`(sde:`, `(sdegeo:`, `(sdedr:`）。
2. Electrode 名称必须来自输入的 Grid/TDR 接触名列表（若同时给了 SDE 与 TDR，以 TDR 为准），禁止自造名称。
3. 若 SDE 为双栅但只给一个 `gate` 接触，SDevice 也必须只使用 `gate`。

### I. 输出结构要求（严格）

- 只输出 **完整 SDevice cmd 纯文本**（无 markdown、无 Scheme 语法）。
- 不要输出任何标签（包括 `<answer>` / `<thought>` / XML / HTML）。
- 不要输出 markdown 代码块围栏（```）。

**禁止**：
- 任何 Scheme 语法（`(sde:` / `(sdegeo:` / `(sdedr:`）
- `eQuantumPotential` / `hQuantumPotential` / `Quantization`（除非用户明确要求）

### II. 命令文件结构（依据 sdevice_ug）

`sdevice_ug` 说明：单器件命令文件按 section 组织，常见 section 包括  
`File` / `Electrode` / `Thermode` / `Physics` / `Plot` / `CurrentPlot` / `Math` / `Solve`。

对当前流程：
- `File`、`Electrode`、`Physics`、`Math`、`Solve` 是核心 section。
- `Plot`、`CurrentPlot` 按输出需求加入。
- `Thermode` 仅在热学/温度边界条件明确需要时加入。

**File Section**
```
File {
  Grid    = "existing_mesh.tdr"
  Plot    = "device_des.tdr"
  Current = "device_des.plt"
  Output  = "device_des.log"
}
```

约束：
- `Grid` 必须指向当前流程中真实存在的 mesh/TDR 结构文件；在本工程默认链路中通常是 `sde_result_msh.tdr`。
- `Plot` / `Current` / `Output` 文件名应与仿真目标一致，但不是 guide 规定的固定字符串。

**Electrode Section**

⚠️ Name 必须与 Grid/TDR 中接触名**完全一致**（大小写敏感）。
若提供了 `tdx -info` 抽取结果，必须优先使用其中的接触名原文；仅在缺失时再参考 SDE `set-contact`。

```
Electrode {
  { Name="source"    Voltage=0.0 }
  { Name="drain"     Voltage=0.0 }
  { Name="gate"      Voltage=0.0 }
  { Name="substrate" Voltage=0.0 }
}
```

约束：
- `WorkFunction`、Schottky barrier、resistor 等接触属性只在用户明确要求、或器件物理上明确需要时加入。
- 不要因为模板习惯自动给 gate 添加 `WorkFunction`。

**Physics Section**（以 guide 的 section 结构为准，模型选择保持最小且与器件匹配）
```
Physics {
  EffectiveIntrinsicDensity(OldSlotboom)
  Mobility(
    DopingDep
    eHighFieldSaturation(GradQuasiFermi)
    hHighFieldSaturation(GradQuasiFermi)
  )
  Recombination(SRH(DopingDep TempDependence))
}
```

约束：
- 以上只是常见的保守起点，不要把它当成 guide 指定的唯一组合。
- 物理模型必须与器件类型、材料体系、仿真目标一致；不要无依据叠加额外模型。
- `eQuantumPotential` / `hQuantumPotential` / `Quantization` 仅在用户明确要求时加入。

**Math Section**
```
Math {
  Number_Of_Threads = 4
  Extrapolate
  Derivatives
  RelErrControl
  Digits = 5
  Iterations = 60
  LineSearchDamping = 1e-3
  NotDamped = 100
}
```

约束：
- `Math` 负责数值求解设置；具体参数可以保守，但不要把某一组数值阈值描述成 `sdevice_ug` 的硬性规定。

**Plot Section**
```
Plot {
  eDensity hDensity
  TotalCurrent/Vector eCurrent/Vector hCurrent/Vector
  eMobility/Element hMobility/Element
  ElectricField/Vector Potential SpaceCharge
  Doping DonorConcentration AcceptorConcentration
}
```

### III. Solve 路径

`sdevice_ug` 的关键规范是：仿真过程写在 `Solve` section 中，偏压扫描通常通过 `Quasistationary` 完成；是否输出提取曲线由 `Extraction` / `CurrentPlot` 等语句决定。

数值步长应采用保守、可收敛的设置，但不要把某个固定阈值写成 guide 明文规则。

**IdVg（转移特性）——常见模板：先偏置 drain，再扫 gate**
```
Solve {
  Coupled(Iterations=100) { Poisson }
  Coupled { Poisson Electron Hole }
  Quasistationary (
    InitialStep=0.01  Increment=1.2  MinStep=1e-5  MaxStep=0.05
    Goal { Name="drain" Voltage=0.9 }
  ) { Coupled { Poisson Electron Hole } }
  NewCurrentPrefix = "result_"
  Quasistationary (
    InitialStep=0.01  Increment=1.2  MinStep=1e-5  MaxStep=0.05
    Goal { Name="gate" Voltage=1.2 }
  ) { Coupled { Poisson Electron Hole }
      CurrentPlot(Time=(Range=(0 1) Intervals=40))
  }
}
```

**IdVd（输出特性）——常见模板：先偏置 gate，再扫 drain**
```
Solve {
  Coupled(Iterations=100) { Poisson }
  Coupled { Poisson Electron Hole }
  Quasistationary (
    InitialStep=0.01  Increment=1.2  MinStep=1e-5  MaxStep=0.05
    Goal { Name="gate" Voltage=1.2 }
  ) { Coupled { Poisson Electron Hole } }
  NewCurrentPrefix = "result_"
  Quasistationary (
    InitialStep=0.01  Increment=1.2  MinStep=1e-5  MaxStep=0.05
    Goal { Name="drain" Voltage=1.5 }
  ) { Coupled { Poisson Electron Hole }
      CurrentPlot(Time=(Range=(0 1) Intervals=40))
  }
}
```

**二极管 IV——常见模板：扫描 anode**
```
Solve {
  Coupled(Iterations=100) { Poisson }
  Coupled { Poisson Electron Hole }
  NewCurrentPrefix = "result_"
  Quasistationary (
    InitialStep=0.01  Increment=1.2  MinStep=1e-5  MaxStep=0.05
    Goal { Name="anode" Voltage=1.0 }
  ) { Coupled { Poisson Electron Hole }
      CurrentPlot(Time=(Range=(0 1) Intervals=40))
  }
}
```

### IV. 关键约束（易错点）

1. **Grid 来源**：必须引用当前实际存在的结构文件；不得凭空改写路径。
2. **Electrode Name 来源**：优先使用传入的 TDR 接触名（`tdx -info` 结果）原文；缺失时再参考 SDE `set-contact`。
3. **Section 结构**：按 `sdevice_ug` 的 section 结构组织，不要混入 SDE Scheme 语法。
4. **Solve 语义**：若生成扫描曲线，必须把偏压路径写清楚；不要在未说明目标的情况下胡乱生成多段扫描。
5. **Current/Plot 输出**：只有在需要曲线或场分布输出时才添加相应 section/语句，且文件名应与任务一致。
