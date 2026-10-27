# FinGuard

FinGuard 是一个面向金融任务的可恢复后端。它把用户目标转换为可校验的计划，在服务端授权范围内执行，并用业务记录、流水和余额证据核实结果。

**当前版本：0.3.0，模拟业务后端。** 已实现转账、账单、理财申赎、临时锁卡、订阅取消和条件组合任务。接口、持久化执行、授权、后台恢复、轨迹、回放和策略发布流程均可运行。现已加入 DeepSeek 官方 API、自适应简单任务规划和有界工具循环，见 [Agent 设计](docs/deepseek-design.md)。全部业务使用虚构数据；小规模真实调用及 Agent 对照已通过，结果见 [DeepSeek 验收](docs/deepseek-validation.md)。真实银行接入和前端工作台属于后续范围。

项目位于私有仓库 [MagDaob/finguard](https://github.com/MagDaob/finguard)。后续开发按可验证的改动持续同步。

项目本体位于 `src/finguard/`，模拟银行位于独立的 `services/bank-simulator/`。两边分开运行环境、配置、进程、数据库和账号，通过认证 HTTP 接口通信，见[服务隔离说明](docs/service-isolation.md)。

## 设计与能力

系统显式关联任务、计划、授权、操作和证据。模型只能提出候选计划，确定性程序负责权限、参数、依赖、限额和业务规则。

```mermaid
flowchart LR
    U["用户目标与约束"] --> P["解析、澄清与候选计划"]
    P --> C["编译、权限与授权控制"]
    C --> I["持久化提交意图"]
    I --> B["模拟银行独立事务"]
    B --> V["查询核实与证据验证"]
    V --> R["任务结果与轨迹"]
    I --> W["后台恢复"]
    W --> V
    R --> E["隔离回放与候选评估"]
```

| 能力 | 当前行为 |
|---|---|
| 转账 | 整数分金额、人民币、确认收款人、最低保留余额、并发扣款和稳定操作编号 |
| 账单 | 已入账现金流统计，分别返回支出、退款、净支出和分类；排除内部转账与理财流动 |
| 理财 | 固定价值模拟产品，检查风险和交易窗口；赎回受理与到账分开，到账后才解锁后续步骤 |
| 服务管理 | 临时锁卡与恢复；订阅合同取消，明确代扣授权未变更、退款为零 |
| 授权 | 服务端访问令牌、单次参数授权、受限转账委托、累计预算、频次、到期与撤销 |
| 任务 | 依赖图、计划版本、澄清、局部重规划、部分完成、停止和重新授权 |
| 可靠执行 | 提交意图持久化、未知结果核实、重复回调处理、重启恢复和证据校验 |
| 调试与改进 | 轨迹、报告导出、故障回放、失败归因、候选生成、对照评估、审核发布与回退 |

## 本机启动

需要 Python 3.12 至 3.14 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。默认 Python 为 3.14，依赖由 `uv.lock` 固定。以下 PowerShell 命令在项目根目录执行。

安装依赖：

```powershell
uv sync --locked --group dev
```

Windows 本机 PostgreSQL：

```powershell
.\scripts\local-postgres.ps1 start -Download
.\scripts\local-services.ps1 stop
uv run --locked python scripts/isolate-local.py
.\scripts\sync-services.ps1
uv run --locked finguard seed-demo
```

首次运行会下载经过 SHA-256 校验的 PostgreSQL 18.6 二进制包，建立隔离数据目录和随机密码。数据、端口和日志位于忽略的 `artifacts/postgresql/`。脚本仅监听本机地址，已有 `.env` 保留。使用自行安装的数据库时，按 [.env.example](.env.example) 配置 `.env`，跳过下载脚本。

启动独立模拟银行、项目接口和内置恢复任务：

```powershell
.\scripts\local-services.ps1 start
```

打开 [API 文档](http://127.0.0.1:8000/docs)；[就绪检查](http://127.0.0.1:8000/health/ready)应返回 `ready`。演示访问令牌保存在本地 `artifacts/demo-tokens.local.json`，有效期为 24 小时。到期后执行 `seed-demo --rotate`；旧令牌如未撤销，会按原期限到期。

在另一终端运行模拟转账：

```powershell
$fgTokens = Get-Content artifacts/demo-tokens.local.json -Raw | ConvertFrom-Json
$fgHeaders = @{ Authorization = "Bearer $($fgTokens.user.token)" }
$fgBody = @{ client_request_ref = "demo-$([guid]::NewGuid().ToString('N'))"; user_request = "给小王转300元，至少保留1000元" } | ConvertTo-Json
$fgTask = Invoke-RestMethod http://127.0.0.1:8000/v1/tasks -Method Post -Headers $fgHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($fgBody))
$fgOperation = $fgTask.operations[0]
$fgApproval = @{ expected_digest = $fgOperation.digest } | ConvertTo-Json
Invoke-RestMethod "http://127.0.0.1:8000/v1/operations/$($fgOperation.operation_ref)/approvals" -Method Post -Headers $fgHeaders -ContentType application/json -Body $fgApproval | Out-Null
$fgResult = Invoke-RestMethod "http://127.0.0.1:8000/v1/tasks/$($fgTask.task_ref)/run" -Method Post -Headers $fgHeaders
$fgResult | Select-Object status, verified_success
```

新建演示库的初始余额为 1,500 元。首次转账后余额为 1,200 元，任务返回 `COMPLETED`、`verified_success: true`。演示库保留历史，多次执行会继续消耗余额；`seed-demo` 不重置已有交易。更多接口与约束见 [API 使用说明](docs/api.md)。

## 验证

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv run --locked finguard replay --suite tests/scenarios/backend-replay.json
uv build --all --no-sources
```

数据库测试使用随机命名空间，结束后仅清理本次命名空间。未配置数据库时，相关测试会跳过；设置 `FINGUARD_REQUIRE_POSTGRES=1` 可要求数据库检查必须执行。纯单元测试使用 `pytest -m "not postgres"`。

回放报告写入 `artifacts/replay-report.json`，独立检查授权、参数绑定、账本、重复记账、归属和证据。固定输出回放与模型回放分别报告，合法任务完成率与正确处理率分别计算。验证入口和实际记录见[测试说明](tests/README.md)及[后端验收记录](docs/backend-validation.md)。

## 部署与文档

容器启动配置见 [compose.yml](compose.yml)和 [Dockerfile](Dockerfile)。数据库备份、独立恢复进程、模型网关协议、跨域设置及故障处理见[后端运行说明](docs/backend.md)。

| 路径 | 内容 |
|---|---|
| [src/finguard/backend/](src/finguard/backend/) | 项目接口、存储、执行控制、规划、银行客户端与评估入口 |
| [services/bank-simulator/](services/bank-simulator/) | 独立模拟银行、账本、业务数据、故障与离线回放 |
| [packages/bank-contracts/](packages/bank-contracts/) | 双方共享的参数、摘要与错误契约 |
| [src/finguard/domain/](src/finguard/domain/) | 金额、引用、核心对象及状态迁移守卫 |
| [src/README.md](src/README.md) | 源码职责索引 |
| [docs/architecture.md](docs/architecture.md) | 数据流、事务和信任边界 |
| [docs/domain-model.md](docs/domain-model.md) | 核心对象、状态和持久化关联 |
| [docs/decisions.md](docs/decisions.md) | 已采用的选型与代价 |
| [docs/roadmap.md](docs/roadmap.md)、[docs/implementation-plan.md](docs/implementation-plan.md) | 当前阶段与剩余工作 |
| [docs/integration-platform-plan.md](docs/integration-platform-plan.md) | 国内个人银行、通用模型 API 和用户插件的下一阶段规划，待实施 |
| [tests/backend/](tests/backend/) | PostgreSQL、并发、授权、恢复、HTTP 和优化流程测试 |
| [docs/sources/project-framework.txt](docs/sources/project-framework.txt) | 用户提供的完整框架原文 |

## 使用边界

自然语言基线只解析明确的有限表达；歧义、否定或尚未支持的条件进入澄清，可通过结构化计划补充。DeepSeek 官方接口和自定义模型网关可提供候选编排，不能修改业务目标和关键参数，也不能签发授权。

金额只支持人民币整数分，模拟报价费用为零。理财没有净值波动、收益计算或真实交易日历。卡片只支持临时锁定，订阅取消不撤销代扣协议。转账没有自动反向付款；卡片补偿需创建新任务并重新授权。

本版本用于后端开发、集成和模拟验收。真实服务必须重新验证其幂等、查询、回调和授权机制。许可证尚未确定。
