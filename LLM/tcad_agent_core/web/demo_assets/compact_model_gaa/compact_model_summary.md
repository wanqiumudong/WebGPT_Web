# 三维 GAA 紧凑模型摘要

- Source case: `mw92_f01_01`
- Goal: 基于三维 stacked nanosheet GAA 的参考 `Id-Vg` / `Id-Vd` 曲线整理紧凑模型参数，并保留 `Verilog-A` 导出接口。
- Model form: `curve-matched behavioral overlay`
- Derived parameters:
  - `VTH0 = 0.1880`
  - `KP = 0.0014758`
  - `NFACTOR = 1.600`
  - `LAMBDA = 0.1088`
  - `RS = 2.00`
  - `RD = 2.00`
- Curve metrics:
  - `ION = 2.687011e-04`
  - `IOFF = 2.819848e-09`
  - `GM_MAX = 6.221276e-04`
  - `SS = 103.78 mV/dec`
  - `IDSAT = 3.064388e-04`

## Notes

1. 拟合对比图采用三维 GAA 参考 `Id-Vg` / `Id-Vd` 曲线构建的 behavioral overlay，用于保证网页展示与参考结果一致。
2. 参数卡强调阈值、电流能力、沟道调制和接口参数的可读性，不等同于完整工业级 BSIM/CMI 提取。
3. `RS` / `RD` 作为接口预留参数保留，便于后续连接电路级验证流程。
