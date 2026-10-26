# FinGuard

FinGuard 是面向金融任务的智能体系统，目标是在明确授权内规划和执行业务，并通过可追溯证据核实结果。

输入包括用户目标、约束和授权范围。系统拟将输入转换为可校验的计划，经执行控制调用业务工具，最后依据业务记录报告结果。

**当前状态：领域基础已实现。** 已建立五个核心对象、严格参数校验、状态迁移守卫、工具契约和本地场景校验命令。模拟银行、持久化执行和真实交易入口仍待实现。

项目仓库：[MagDaob/finguard](https://github.com/MagDaob/finguard)（私有）。后续开发按可验证的改动逐次提交，并同步文档与实际实现状态。

## 设计主线

系统以任务、计划、授权、操作和证据为核心对象。认知系统提出计划，控制系统决定执行条件，执行系统提交请求并核实结果。

- 所有工具调用均经过权限检查；写操作还需验证业务约束和适用授权。
- 任务状态与操作状态分开持久化。
- 重试和恢复沿用稳定操作编号；未知结果先核实原操作。
- 成功报告必须有业务证据支撑。
- 策略优化经过离线评估、回归验证和审核发布。

执行、授权、恢复和证据验证属于后续业务阶段的验收要求。当前测试主要验证领域对象和工具契约。

## 从这里开始

1. 阅读[原始框架](docs/sources/project-framework.txt)，了解完整范围。
2. 阅读[系统架构](docs/architecture.md)和[领域模型](docs/domain-model.md)，明确模块边界。
3. 按[开发路线](docs/roadmap.md)和[实施步骤](docs/implementation-plan.md)推进，使用[验收要求](tests/README.md)检查结果。
4. 阅读[设计决策](docs/decisions.md)，了解已采用的工程工具和待定项。

## 安装与验证

需要 Python 3.12 至 3.14，以及 uv。项目默认使用 Python 3.14，依赖由 `uv.lock` 固定。uv 安装方式见[官方说明](https://docs.astral.sh/uv/getting-started/installation/)。

在项目根目录执行以下命令。Windows PowerShell 和常见 Linux Shell 均适用。

```powershell
uv sync --locked --group dev
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

校验首个转账场景：

```powershell
uv run --locked finguard validate-scenario --config configs/development.example.toml
```

输出应包含 `status: VALIDATED`、`projected_balance_minor: 120000` 和 `operation_status: PREPARING`。`execution_performed` 与 `authorization_created` 均为 `false`。预期余额只是场景数据计算，业务操作仍处于草案状态。

也可以通过 `--scenario tests/scenarios/transfer-basic.json` 直接指定输入。配置中的相对路径以配置文件所在目录为基准。无效输入返回非零退出码。

构建源码包和安装包：

```powershell
uv build --no-sources
```

产物位于 `dist/`，不进入版本控制。验证范围与当前记录见[测试说明](tests/README.md)。

## 项目结构

| 路径 | 职责 |
|---|---|
| [AGENTS.md](AGENTS.md) | 后续开发约束和文档维护要求 |
| [docs/architecture.md](docs/architecture.md) | 职责划分、执行链路和信任边界 |
| [docs/domain-model.md](docs/domain-model.md) | 核心对象、状态和关联规则 |
| [docs/roadmap.md](docs/roadmap.md) | 开发顺序、阶段产物和验收条件 |
| [docs/implementation-plan.md](docs/implementation-plan.md) | 具体步骤、首批编码任务和里程碑 |
| [docs/decisions.md](docs/decisions.md) | 已确定原则和待定工程选型 |
| [docs/sources/project-framework.txt](docs/sources/project-framework.txt) | 用户提供的完整原文 |
| [pyproject.toml](pyproject.toml)、[uv.lock](uv.lock) | 项目入口、依赖约束和锁定结果 |
| [src/README.md](src/README.md) | 已实现源码及后续模块职责 |
| [configs/development.example.toml](configs/development.example.toml) | 无凭据的开发配置示例 |
| [tests/README.md](tests/README.md) | 实际测试入口、系统不变量和待覆盖故障 |
| [tests/scenarios/transfer-basic.json](tests/scenarios/transfer-basic.json) | 可加载的虚构输入与预期结果 |
| [.github/workflows/checks.yml](.github/workflows/checks.yml) | Windows、Linux 和多个 Python 版本的检查配置 |

## 当前边界

转账、账单、理财、卡片、订阅和跨服务任务均属于拟实现范围。当前领域参数仅覆盖人民币单次转账；场景命令只检查数据和对象关联。

授权记录的数据类型不承担可信签发、有效性判定或消费。证据类型不承担下游真实性核验。工具注册表只描述和校验契约，不调用业务接口。

数据库、服务接口、模型服务、界面和真实业务适配器尚未接入。许可证尚未确定。
