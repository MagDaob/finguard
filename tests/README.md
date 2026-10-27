# 验证要求

扩展契约检查位于 `tests/contracts/`，认证 API 检查位于 `tests/backend/test_integration_api.py`。它们使用本地声明、测试替身和随机数据库命名空间，不调用真实银行或新增模型服务。具体边界见[扩展接口](../docs/extension-interfaces.md)。

当前覆盖领域规则、模拟银行、持久化授权与执行、HTTP 接口、计划控制、回放和策略发布。全部业务输入为虚构数据。实际执行记录见[后端验收](../docs/backend-validation.md)。

模拟器通过开发依赖显式安装。普通回归使用随机同库夹具；双进程恢复测试使用分开的随机命名空间，通过认证 HTTP 通信。新增检查覆盖服务密钥、参数校验、超时不重提和旧账本归档，见[服务隔离](../docs/service-isolation.md)。

## 执行方式

在项目根目录安装开发依赖，配置可创建命名空间的 PostgreSQL 测试数据库，然后运行：

```powershell
uv sync --locked --group dev
$env:FINGUARD_REQUIRE_POSTGRES = '1'
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked finguard replay --suite tests/scenarios/backend-replay.json
uv build --all --no-sources
```

Windows 可使用[本机 PostgreSQL 脚本](../scripts/local-postgres.ps1)。未配置数据库时默认跳过标记为 `postgres` 的测试；强制模式将缺少配置视为失败。已配置但连接失败不会被静默跳过。

只执行无数据库测试：`uv run --locked pytest -q -m "not postgres"`。只执行数据库测试：`uv run --locked pytest -q -m postgres`。

每个数据库测试创建随机 `fg_test_` 命名空间，每个回放用例创建随机 `fg_replay_` 命名空间。清理只针对本次创建的目标，不重置运行中的演示库。测试角色需要相应建表权限，应使用专用测试实例。

## 检查分布

| 文件 | 验证内容 |
|---|---|
| `test_values.py`、`test_models.py`、`test_states.py` | 金额、对象关系、计划图和状态迁移 |
| `test_tools.py`、`test_scenarios.py` | 工具契约、输入注入、场景和退出码 |
| [backend/test_bank.py](backend/test_bank.py) | 真实 PostgreSQL 并发、同操作幂等、账户竞争、账单和产品语义 |
| [backend/test_service.py](backend/test_service.py) | 授权、停止、参数变更、部分完成、故障恢复和证据 |
| [backend/test_controls.py](backend/test_controls.py) | 委托频次、撤销、到期、预算释放和旧授权失效 |
| [backend/test_api.py](backend/test_api.py) | 认证、端到端任务、归属、文档、请求限制和评估开关 |
| [backend/test_planning.py](backend/test_planning.py) | 模型网关协议、非法候选、依赖和目标范围 |
| [backend/test_evaluation.py](backend/test_evaluation.py) | 回放隔离、不变量、失败草案、模拟模型对照评估和回退 |
| [backend/test_process_recovery.py](backend/test_process_recovery.py) | 实际 HTTP 进程停止与重启，核实已生效付款且只记账一次 |

## 系统不变量与故障

检查无授权写入、参数绑定、重复记账、余额与预算竞争、主体隔离、证据完整、权限注入和恢复身份。回放额外比较期初余额、流水净额与当前余额。

故障包括发送前失败、银行已生效但响应丢失、提交前后进程中断、响应结构异常、查询暂时未知、重复回调、授权到期撤销、金额修改、结算后余额条件变化及证据缺失。单元故障使用持久化脚本；HTTP 进程测试实际重启服务。

测试并非生产正确性的穷尽证明。当前没有随机属性测试、大规模压力测试、真实银行合同测试或真实模型质量评估。FastAPI 的 TestClient 目前发出 HTTPX 适配弃用警告，不影响当前检查结果。

## 回放和指标

[backend-replay.json](scenarios/backend-replay.json)包含有效确认、缺少授权、响应丢失、提交前中断及赎回到账后转账。回放事件仅作用于隔离的虚构数据。

固定目标回放检查控制与执行行为；`replay --model` 重新调用配置的模型网关。合法任务完成率只包含预期应完成的用例，正确处理率包括等待和拒绝。优化对照评估检查候选与当前基线，发布仍需明确审核；模拟网关结果不能表述为真实模型效果。

## 持续集成

[checks.yml](../.github/workflows/checks.yml)在 Windows、Linux 和 Python 3.12 至 3.14 上检查无数据库部分；Linux 的 Python 3.12、3.14 使用 PostgreSQL 18 检查后端，并单独构建容器。CI 数据库是隔离的临时实例，仅用于虚构数据。

本地与远端结果分别核实。配置存在不代表执行成功，提交对应结果见 GitHub Actions。

## DeepSeek 专项

[backend/test_deepseek.py](backend/test_deepseek.py)使用模拟 HTTP 响应检查官方协议、推理字段、候选修正、并发、循环预算和权限边界。普通测试不会调用真实模型，也不会读取本机真实密钥用于网络请求。

真实测试单独执行 `uv run --locked finguard model-benchmark --live`，最多 32 次付费请求、并发最高 4。过程、指标及限制见 [Agent 设计](../docs/deepseek-design.md)，结果见 [专项验收](../docs/deepseek-validation.md)。
