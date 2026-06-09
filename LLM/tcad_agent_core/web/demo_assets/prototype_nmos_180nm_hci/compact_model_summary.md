# NMOS 紧凑模型摘要

- Source case: `NMOS_180nm_HCI`
- Goal: 基于原型工程中的参考 `Id-Vg` / `Id-Vd` 曲线，整理 compact-model 参数，并保留 `Verilog-A` 导出接口。
- Model form: `iv_curve_behavioral_overlay`
- Derived parameters:
  - `VTH0 = 0.4578`
  - `KP = 0.0003532`
  - `NFACTOR = 1.300`
  - `LAMBDA = 0.0300`
  - `RS = 2.00`
  - `RD = 2.00`
  - `IDSAT = 4.829400e-04`

## Notes

1. 参数提取基于原型工程中的参考 `Id-Vg` / `Id-Vd` 曲线整理，保留网页演示所需的可读性与一致性。
2. 参数卡强调阈值、电流能力、亚阈值趋势和沟道调制信息，不等同于完整工业级 BSIM/CMI 提取。
3. 当前导出的 `Verilog-A` 文件为接口级模型骨架，后续可继续接入外部电路验证流程。
