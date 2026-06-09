# TCAD 固定演示脚本

本文件仅供演示和论文截图使用，不在网页中展示。

当前模式是严格脚本模式：
- 固定 prompt
- 固定输出
- 固定内容

## 案例 1：结构生成任务

第一轮：

`我想构建一个平面 MOS gate-tunneling 结构，请直接生成可执行的 Sentaurus SDE Scheme 脚本。器件主体需要包含顶部 PolySilicon gate、超薄 gate oxide / tunnel dielectric 和底部连续 Silicon 主体，并且只保留 top 与 bot 两个接触。请把 gate 材料、绝缘层材料、绝缘层厚度、silicon 厚度、横向半宽、gate doping、channel 背景掺杂以及局部 gate-oxide mesh refinement 都显式保留下来。网格方面请同时保留全局 refinement、Silicon/oxide 界面细化和超薄绝缘层附近的局部加密。最后请先完成脚本整理，并检查语法是否正确。`

第二轮：

`我想继续查看这个器件的结构结果，请导出结构图和掺杂分布图。我想重点检查 PolySilicon / oxide / Silicon 三层边界、top 与 bot 接触位置，以及超薄绝缘层附近的局部网格加密是否保持合理。`

## 案例 2：电学仿真任务

第一轮：

`我想构建一个横向硅 LDMOS，请先整理这个器件的结构与 SDE 代码，并完成必要检查。器件需要体现 NWell / PWell、gate、source/body 区、长漂移区、LOCOS/STI 隔离以及 drain 功率侧结构，同时保留对应的几何与掺杂信息，使输出特性分析所需区域定义保持清晰。请保留关键 drift 区、gate oxide、source/body、drain 侧结构以及接触定义，并先完成这组器件结构与 SDE 代码的整理与检查。`

第二轮：

`我想继续对这个 LDMOS 进行电学仿真，请生成适配的 Sentaurus SDevice 仿真脚本，并导出输出特性与击穿结果图。我希望当前工作区中能够看到用于输出特性和击穿分析的脚本文件、结果曲线以及对应的运行日志，以便检查这条 LDMOS 电学仿真链路是否完整。`

## 案例 3：紧凑模型构建任务

第一轮：

`我想构建一个 180 nm 平面 NMOS，请直接生成可执行的 Sentaurus SDE Scheme 脚本。器件主体需要包含连续的 Silicon substrate、表面 gate oxide、PolySilicon gate、两侧 spacer、source/drain extension 与 source/drain 主注入区域，并保留 source、drain、gate、substrate 四个接触。结构方面请把 well implant、gate stack、STI/LOCOS 隔离、source/drain 区以及沟道关键尺寸都保留下来，使器件边界、接触定义与后续偏置语义保持一致。掺杂方面请保留 Boron 背景掺杂、PolySilicon gate 的高浓度掺杂，以及 source/drain 的 Arsenic 注入、extension 分布与结深定义。网格方面请保留全局细化、gate oxide / Silicon 界面细化、沟道局部细化以及 source/drain 结区域加密。最后请先完成这组器件结构与 SDE 代码的整理，并检查关键结构与语法是否完整。`

第二轮：

`我想继续得到这个器件的参考电学结果，请生成适配的 Sentaurus SDevice 仿真脚本，并导出可直接查看的参考 Id-Vg 和 Id-Vd 曲线。我希望当前工作区中能够看到用于转移特性和输出特性分析的脚本文件、结果曲线以及相关日志，以便检查这条 NMOS 电学仿真链路是否完整。`

第三轮：

`我想继续构建这个器件的紧凑模型，请整理紧凑模型参数，并导出 Verilog-A 文件。请把门限电压、电流因子、亚阈值因子以及接口电阻等关键信息整理成参数卡，同时给出模型响应对比图以及可直接查看的 Verilog-A 接口文件。`
