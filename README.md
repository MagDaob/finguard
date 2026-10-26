# FinGuard

FinGuard 是面向金融任务的智能体系统，目标是在明确授权内规划和执行业务，并通过可追溯证据核实结果。

输入包括用户目标、约束和授权范围。系统拟将输入转换为可校验的计划，经执行控制调用业务工具，最后依据业务记录报告结果。

**当前状态：项目准备阶段。** 已整理设计文档、开发顺序和模拟场景。业务代码、运行入口、依赖清单和自动化测试尚未实现。

项目仓库：[MagDaob/finguard](https://github.com/MagDaob/finguard)（私有）。后续开发按可验证的改动逐次提交，并同步文档与实际实现状态。

## 设计主线

系统以任务、计划、授权、操作和证据为核心对象。认知系统提出计划，控制系统决定执行条件，执行系统提交请求并核实结果。

- 所有工具调用均经过权限检查；写操作还需验证业务约束和适用授权。
- 任务状态与操作状态分开持久化。
- 重试和恢复沿用稳定操作编号；未知结果先核实原操作。
- 成功报告必须有业务证据支撑。
- 策略优化经过离线评估、回归验证和审核发布。

以上为设计要求，实际保证需要随实现逐项验证。

## 从这里开始

1. 阅读[原始框架](docs/sources/project-framework.txt)，了解完整范围。
2. 阅读[系统架构](docs/architecture.md)和[领域模型](docs/domain-model.md)，明确模块边界。
3. 按[开发路线](docs/roadmap.md)逐阶段实现，使用[验收要求](tests/README.md)检查结果。
4. 首阶段处理[待定决策](docs/decisions.md)，再建立源码和测试入口。

当前无需安装依赖，也没有启动命令。首个开发切片使用本地模拟业务，具体技术栈在开始实现时确定。

## 项目结构

| 路径 | 职责 |
|---|---|
| [AGENTS.md](AGENTS.md) | 后续开发约束和文档维护要求 |
| [docs/architecture.md](docs/architecture.md) | 职责划分、执行链路和信任边界 |
| [docs/domain-model.md](docs/domain-model.md) | 核心对象、状态和关联规则 |
| [docs/roadmap.md](docs/roadmap.md) | 开发顺序、阶段产物和验收条件 |
| [docs/decisions.md](docs/decisions.md) | 已确定原则和待定工程选型 |
| [docs/sources/project-framework.txt](docs/sources/project-framework.txt) | 用户提供的完整原文 |
| [src/README.md](src/README.md) | 源码职责映射，后续逐步建立模块 |
| [tests/README.md](tests/README.md) | 系统不变量、测试分层和故障清单 |
| [tests/scenarios/transfer-basic.json](tests/scenarios/transfer-basic.json) | 首个转账场景的虚构输入和预期结果 |

## 当前边界

转账、账单、理财、卡片、订阅和跨服务任务均属于拟实现范围。当前仅有设计资料和测试场景数据，场景尚不能执行。

框架、数据库、模型服务、界面和真实业务适配器均待选型。许可证尚未确定。
