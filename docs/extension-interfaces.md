# 扩展接口 1.0

适用项目版本：0.3.0。接口准备层已实现，不包含新增真实银行、模型厂商适配或插件运行平台。

## HTTP 入口

以下接口要求有效访问令牌及 `tasks.read` 权限。

| 方法与路径 | 行为 |
|---|---|
| `GET /v1/integrations/contracts` | 返回银行、模型和插件的数据模式与实现边界 |
| `GET /v1/integrations/providers` | 返回受信注册目录及已有模型配置的非秘密描述 |
| `POST /v1/integrations/bank-connections/validate` | 本地检查银行连接格式、提供方与环境 |
| `POST /v1/integrations/model-connections/validate` | 本地检查模型地址、协议、模型、密钥引用与预算 |
| `POST /v1/integrations/plugins/validate` | 本地检查插件清单、模式、配置和核心工具冲突 |

校验不保存或激活连接、不解析密钥、不发起网络请求。`valid: true` 只代表本地格式通过，不表示远端可用、插件获得权限或业务成功。

连接创建、按用户读取、版本更新和停用已定义为 `ModelConnectionRepository` 协议，本期没有持久化实现或对应的管理操作处理器。

## 银行协议

类型位于 [connections.py](../packages/bank-contracts/src/finguard_bank_contracts/connections.py)，调用协议位于 [bank.py](../src/finguard/backend/bank.py)。主要对象为 `BankConnection`、`BankCapabilities`、`BankOperationResult` 和 `BankEvidence`。

`BankRegistry` 只供受信代码注册工厂，不按用户输入导入模块。未知提供方、环境不匹配和缺少所需能力均明确失败。能力默认关闭，模拟器单独声明查询栅栏、证据及最低余额保证。

默认配置为 `FINGUARD_BANK_PROVIDER=simulated-bank`、`FINGUARD_BANK_ENVIRONMENT=simulation`。本期只注册模拟提供方；其他配置不会回退到模拟银行。模拟服务新增认证入口 `GET /v1/capabilities`，客户端通过 `get_capabilities()` 读取，能力声明不能代替用户权限或操作授权。

原字典回执继续兼容。新类型通过 `to_backend_receipt()` 显式转换：内部 `transaction_ref` 绑定项目操作编号，真实外部流水另存于 `bank_record_ref`，允许数字编号。业务数据不能覆盖回执身份、状态和错误字段。

`UNKNOWN` 转换为既有 `ResultUnknown`，继续核实。`REQUIRES_USER_ACTION` 只预留格式；现有内核尚未实现该银行交互，转换会明确返回不支持。成功回执仍需独立业务证据，不推断到账，也不重写旧操作或授权。

## 模型协议

[contracts.py](../src/finguard/integrations/models/contracts.py)定义连接、消息、工具调用、用量、响应和流式事件。`ModelClient` 约定 `complete()`、`stream()` 和 `close()`，本期没有通用网络客户端。

本地校验示例，不包含真实密钥：

```json
{
  "connection_ref": "my-model",
  "provider": "custom-model",
  "protocol": "chat_completions",
  "base_url": "https://model.example/v1",
  "model_id": "my-model-name",
  "credential_ref": "secret:my-model-key"
}
```

通过校验后，未注册提供方仍显示 `provider_registered: false`、`protocol_implemented: false`，连接不会生效。凭据只接受 `env:ENVIRONMENT_VARIABLE` 或 `secret:opaque-reference` 引用。HTTP 入口拒绝明文 `api_key` 和调用者指定的 `owner_ref`，归属由服务端确定。密钥存储与解析留作扩展。

`ModelRegistry` 的受信工厂构建要求 `ModelCredential` 与连接的用户、密钥引用、地址和配置版本完全匹配。密钥使用 `SecretStr`，不进入对象表示或序列化结果。用户自报的能力不能覆盖提供方声明。

地址拒绝内嵌凭据、查询参数、片段及控制字符；HTTP 仅允许显式启用的本机开发地址。这不是未来网络出口隔离的替代品。未知 Token 用量使用 `null`，与零用量区分。响应和事件检查工具调用、结束原因与请求身份，不保存推理过程。

现有 DeepSeek 和自定义计划网关仍走原路径。`legacy_model_connection()` 仅描述配置，不创建客户端；两者分别标识为 `deepseek_legacy` 和 `planning_gateway`，不冒充通用聊天协议。流式传输等仍需未来适配器实现。

## 插件契约

独立包 [finguard-plugin-contracts](../packages/plugin-contracts/)包含清单、工具模式、调用、结果及未来 `PluginHost` 协议，不依赖项目本体或模拟银行。1.0 版本入口固定为 `contract_only`。

清单校验命名空间、严格版本、工具权限与插件申请范围；只读工具不能申请写权限。宿主拒绝重复注册和核心工具冲突。这些检查只验证声明一致性，不根据声明授予权限或推断用户代码行为。

`PluginRegistry` 提供注册、读取、启用、停用和移除元数据的操作，并复制清单以隔离后续修改。启用只是目录状态，不提供执行能力；`invoke()` 明确返回 `PLUGIN_RUNTIME_UNAVAILABLE`。

模式使用 `jsonschema` 校验的 JSON Schema Draft 2020-12 内联子集，不接受引用、`pattern` 或 `patternProperties`。模式上限为 32 KiB、16 层、2,048 个节点；配置和调用数据限制为 64 KiB、16 层、512 个节点及单容器 128 项。`format` 默认只作注释。

`validate_invocation()` 校验身份、版本、截止时间、工具及参数；`validate_result()` 校验调用关联与输出。两者都不核发权限，也不核实业务证据。未来宿主仍须实现权限与隔离控制。

[personal-report.json](../plugins/examples/personal-report.json)是可校验的只读月报清单，不含执行代码。可将它作为插件校验接口的 `manifest`，并传入 `configuration: {"title": "个人月报"}`。清单申请读取交易，不代表权限已获批准。

## 验证

```powershell
uv sync --locked --group dev
uv run --locked pytest -q tests/contracts tests/backend/test_integration_api.py
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --all --no-sources
```

测试检查提供方缺失、环境与能力不匹配、密钥绑定、地址限制、插件冲突、过期调用、结果关联及校验不构建网络客户端。原有业务回归继续检查授权、幂等、并发、恢复与证据。

2026 年 10 月 27 日，本机 50 项专项测试通过，包含 44 项契约测试和 6 项数据库及 HTTP 测试。Ruff 检查和格式检查通过；项目本体、模拟银行及两个契约包的源码包与 wheel 构建成功。

同日完整回归 266 项通过，无跳过。代码提交 `7e28922` 的 [GitHub Actions 验收](https://github.com/MagDaob/finguard/actions/runs/40471440227)共 9 个任务，全部成功：Windows 和 Linux 的 Python 3.12、3.13、3.14 检查，Python 3.12、3.14 数据库测试，以及容器构建、包隔离和 Compose 双服务启动。

分离运行环境下，实际 HTTP 检查确认模式目录、本地模型校验、完整插件示例和认证边界正常。模拟银行能力声明可读取，账户余额保持 150,000 分。此次验收未调用真实银行或付费模型。
