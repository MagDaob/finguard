# 答辩演示设计

2026 年 10 月 28 日修订。内容对应 FinGuard 0.3.0，共 12 页，建议汇报 8 分 50 秒。逐页内容和时间见[答辩讲稿](defense-script.md)。

## 参考与取舍

视觉方向参考 [Apple 官方发布会](https://www.apple.com/apple-events/)和 [Keynote 官方展示页](https://www.apple.com/ca/keynote/)。大标题、留白、单页重点、产品画面和黑白页面交替，是本次对参考页面的设计归纳，不是 Apple 发布的设计规范。本稿不使用 Apple 标识，也不表示与 Apple 存在合作关系。

每页保留一个主要阅读重点。详细原理、限定条件和来源放入演讲者备注。影响结论的限制继续显示在正文中，包括模拟业务状态、额外验证尚未接入、小样本条件及扩展接口尚未激活。

## 版式与内容

画幅为 16:9。中文使用 Microsoft YaHei，英文大标题与主要数字使用 Segoe UI。背景使用白色、`#F5F5F7` 和 `#050607`，正文使用 `#1D1D1F`。重点使用 FinGuard 绿色 `#087F6A`，深色页面使用浅绿色 `#75E1C4`。

| 页码 | 阅读重点 | 呈现形式 |
|---|---|---|
| 1 | 产品名称与模拟预览版定位 | 黑色封面、抽象玻璃背景 |
| 2 | 转账目标与余额约束 | 两行大标题、三个金额 |
| 3 | 模型与执行控制的职责 | 可编辑架构图、独立数据库 |
| 4 | 单次候选与有界循环 | 两条路径、默认 4 轮 |
| 5 | 执行前的授权边界 | 分层控制、额外验证门槛 |
| 6 | 未知结果的恢复 | 原操作编号与核实流程 |
| 7 | 实际工作台 | 完整界面截图 |
| 8 | 短计划输入开销 | 65.4% 与可编辑柱状图 |
| 9 | 真实模型接口测试 | 5.49 次/秒与原生表格 |
| 10 | 软件包验证 | 发布规则检查与实际 EXE 自检 |
| 11 | 扩展接口的当前状态 | 银行、模型、插件对照表 |
| 12 | 当前成果和下一步 | 模拟预览版定位与公开源码入口 |

指标继续使用[模型验收记录](../deepseek-validation.md)和[发布准备记录](../release-preparation-validation.md)中的历史结果。本次只重做演示设计，没有重新执行模型吞吐或业务测试。图表保留源数据，讲稿明确区分正确处理、等待授权和合法任务完成。

## 素材来源

- [工作台截图](../assets/screenshots/frontend-workbench.png)：项目实际前端画面，使用虚构账户和余额。没有对业务数据进行合成或替换。
- [封面背景](../assets/presentations/finguard-glass-hero.png)：通过内置图像生成工具制作的抽象装饰图。它不表示实际产品硬件，也不承担架构或测试证据。

封面生成模式为内置工具，未使用命令行 API。最终提示词如下：

```text
Use case: stylized-concept. Asset type: keynote presentation cover background for an original financial-agent software project named FinGuard. Create a premium minimalist abstract studio 3D photograph/render, widescreen 16:9 composition. Pure near-black (#050607) background. One beautifully formed thick translucent smoked-glass ribbon folded into a protective arch or open oval, located entirely in the right 45% of frame; polished edges catching restrained emerald and icy cyan light, hint of titanium sheen. Cinematic side light, physically realistic optical refraction, extremely clean and quiet, luxurious product-launch art direction. Leave the left 55% almost perfectly black and empty for large editable white typography to be added later. The glass occupies roughly 65% height, floating with very soft low reflection on black floor. No words, no letters, no logo, no Apple logo, no lock icon, no shield pictogram, no people, no charts, no interface, no neon circuit grid, no busy particles. This is an abstract decorative visual, not a product screenshot.
```

## 交付与维护

本机新版材料保存在 `dist/submissions/0.3.0/2026-10-28-apple/`。该目录包含 PPTX、PDF 预览、讲稿、使用说明和摘要清单。旧版参赛材料继续保留。

PPTX 保留可编辑文字、流程图、表格和带内嵌工作簿的数据图表。PDF 使用高清页面图像固定版式，适合预览和上传；后续改稿使用 PPTX。字体未嵌入，换电脑放映前需要检查字体和换行。

本次已检查 12 页文件结构、备注、图表数据和渲染画面。未在原生 PowerPoint 中验证播放行为。生成材料不表示已上传参赛平台，也不改变软件的发布状态。
