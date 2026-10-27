# 源码组织

项目使用 `finguard` Python 包，版本为 0.3.0。后端运行、配置与管理入口见[运行说明](../docs/backend.md)。

| 位置 | 职责 |
|---|---|
| [finguard/domain/](finguard/domain/) | 金额、引用、核心对象和状态迁移规则 |
| [finguard/tools/](finguard/tools/) | 首个转账切片的工具契约与能力检查 |
| [finguard/backend/catalog.py](finguard/backend/catalog.py) | 多业务工具参数、作用域、规范化及摘要 |
| [finguard/backend/database.py](finguard/backend/database.py)、[migrations](finguard/backend/migrations/) | PostgreSQL 事务、迁移和任务互斥 |
| [finguard/backend/bank.py](finguard/backend/bank.py)、[demo.py](finguard/backend/demo.py) | 银行 HTTP 客户端与项目侧演示主体 |
| [finguard/backend/security.py](finguard/backend/security.py)、[authorization.py](finguard/backend/authorization.py) | 身份、权限、单次授权和受限委托 |
| [finguard/backend/planning.py](finguard/backend/planning.py) | 有限自然语言解析、模型网关和计划编译 |
| [finguard/backend/service.py](finguard/backend/service.py) | 任务、调度、停止、核实、证据与恢复 |
| [finguard/backend/api.py](finguard/backend/api.py) | HTTP 入口、认证、查询、任务和运营接口 |
| [finguard/backend/evaluation.py](finguard/backend/evaluation.py)、[policies.py](finguard/backend/policies.py) | 回放请求契约、可选离线入口、候选与发布回退 |
| [finguard/backend/config.py](finguard/backend/config.py)、[commands.py](finguard/backend/commands.py) | 配置、迁移、演示数据、服务及回放命令 |
| [finguard/scenarios.py](finguard/scenarios.py)、[cli.py](finguard/cli.py) | 原有场景草案校验和统一命令入口 |

`domain/models.py` 保留首个单转账切片的绑定模型。多业务请求与执行记录使用后端契约，状态迁移继续复用领域守卫。真实服务适配与前端继续作为独立阶段。

模拟业务与测试回放实现位于独立的 [bank-simulator](../services/bank-simulator/)，共享契约位于 [bank-contracts](../packages/bank-contracts/)。在线应用环境不安装模拟器，见[服务隔离](../docs/service-isolation.md)。

DeepSeek 官方接口与工具循环位于 [deepseek.py](finguard/backend/deepseek.py)，统一选择入口为 [model_factory.py](finguard/backend/model_factory.py)。真实吞吐及 Agent 对照测试位于 [benchmark.py](finguard/backend/benchmark.py)，按 [专项协议](../docs/deepseek-design.md)显式启动。
