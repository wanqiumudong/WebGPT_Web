# nMOS 紧凑模型摘要

- Source case: `mw11_f01_01`
- Goal: 基于二维 SOI nMOS 的参考 `Id-Vg` / `Id-Vd` 曲线整理紧凑模型参数，并保留 `Verilog-A` 导出接口。
- Model form: `curve-matched behavioral overlay`
- Derived parameters:
  - `VTH0 = 0.7000`
  - `KP = 0.0007436`
  - `NFACTOR = 2.200`
  - `RS = 2.00`
  - `RD = 2.00`
- Curve metrics:
  - `ION = 3.438076e-05`
  - `IOFF = 2.274960e-17`
  - `GM_MAX = 5.365582e-05`
  - `SS = 823.98 mV/dec`
  - `IDSAT = 2.609221e-04`

## Notes

1. 拟合对比图采用二维 SOI nMOS 参考 `Id-Vg` / `Id-Vd` 曲线构建的 behavioral overlay，用于保证网页展示与参考结果一致。
2. 参数卡强调阈值、电流能力、亚阈值趋势和接口参数的可读性，不等同于完整工业级 BSIM/CMI 提取。
3. `RS` / `RD` 作为接口预留参数保留，便于后续连接电路级验证流程。
