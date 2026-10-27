# API 使用说明

适用版本：0.3.0。默认地址为 `http://127.0.0.1:8000`，交互文档为 `/docs`，机器可读描述为 `/openapi.json`。所有业务均在模拟银行执行。

模式目录和本地校验入口位于 `/v1/integrations/`，见[扩展接口](extension-interfaces.md)。这些入口不保存或激活连接、不发起外部请求，也不授予插件权限。

任务支持可选 `context_task_ref`；账单支持明确日期区间和分类；预览新增 `authentication_policy`，高金额操作可能返回 `STEP_UP_REQUIRED`。导出将 `contains_credentials` 改为 `null` 并返回脱敏规则状态。字段、兼容性和示例见[自然语言与隐私说明](understanding-and-privacy.md)。

## 鉴权和数据约定

除健康检查、接口文档和签名回调外，接口需要 `Authorization: Bearer <本地访问令牌>`。令牌来自可信本机管理命令；请求中的用户编号、`authorized` 字段或模型文本不会建立有效授权。

金额单位为分，币种为 `CNY`。金额、费用、限额和版本号使用整数，拒绝字符串、浮点数和布尔值。多余字段返回 422。请求体上限为 1 MiB。资源只能由所属主体访问，跨主体引用返回 404。

列表接口默认返回数组。任务、委托和流水按引用编号降序分页；`limit` 最大 100，`before` 使用上页最后一条的引用编号。该顺序用于稳定分页，不表示业务时间。轨迹按 `sequence` 升序分页，`after` 为上页最后一个序号，`limit` 最大 1000。

## 业务查询

| 方法与路径 | 内容 | 所需权限 |
|---|---|---|
| `GET /health/live`、`GET /health/ready` | 存活、项目数据库、独立银行服务和恢复循环状态 | 无 |
| `GET /v1/me` | 当前主体与权限 | 有效令牌 |
| `POST /v1/tokens/revoke-current` | 撤销当前令牌 | 有效令牌 |
| `GET /v1/accounts`、`GET /v1/recipients` | 本人账户和确认收款人 | `accounts.read` |
| `GET /v1/accounts/{id}/ledger` | 本人账户流水及退款来源 | `accounts.read`、`transactions.read` |
| `GET /v1/cards`、`GET /v1/subscriptions` | 本人卡片和订阅 | `services.read` |
| `GET /v1/products` | 模拟产品、交易窗口和本人风险匹配结果 | `products.read` |
| `GET /v1/positions` | 本人持仓 | `products.read`、`accounts.read` |
| `GET /v1/tools` | 工具参数模式、权限和执行元数据 | `tasks.read` |

## 创建和推进任务

`POST /v1/tasks` 返回 201 和任务视图。同一主体重复提交相同的 `client_request_ref` 与完整请求，返回同一任务；请求内容不同则返回 409。使用同一请求编号查询重试结果，避免人为创建重复业务意图。

以下是完整的结构化转账请求：

```json
{
  "client_request_ref": "transfer-example-1",
  "user_request": "向小王转300元，至少保留1000元",
  "constraints": {
    "minimum_remaining_balance_minor": 100000,
    "maximum_fee_minor": 0
  },
  "steps": [
    {
      "step_ref": "transfer",
      "action": "transfer.create",
      "arguments": {
        "source_account_ref": "account-demo-001",
        "recipient_ref": "recipient-demo-wang",
        "amount_minor": 30000,
        "currency": "CNY",
        "fee_minor": 0,
        "minimum_remaining_balance_minor": 100000
      },
      "depends_on": [],
      "funds_available_after": []
    }
  ]
}
```

省略 `steps` 时使用有限自然语言解析。歧义进入 `WAITING_CLARIFICATION`，返回 `questions`。客户端通过 `POST /v1/tasks/{id}/plans` 提交明确步骤和 `expected_version` 补充计划；尚未建立计划时版本为 0。

计划最多包含 32 个步骤。`depends_on` 表示依赖节点成功；`funds_available_after` 必须属于显式依赖，且引用同一账户的赎回节点。“申赎受理”不等于“资金到账”。

| 方法与路径 | 行为 |
|---|---|
| `GET /v1/tasks`、`GET /v1/tasks/{id}` | 本人任务列表、当前状态、全部计划、操作及证据 |
| `POST /v1/tasks/{id}/plans` | 提交新计划版本，参数为 `expected_version`、`steps` |
| `POST /v1/tasks/{id}/run` | 请求推进任务；缺少授权时等待，重复运行不重复提交 |
| `POST /v1/tasks/{id}/stop` | 取消未提交节点，已提交节点继续核实 |
| `GET /v1/tasks/{id}/trace` | 状态变更、计划和授权事件 |
| `GET /v1/metrics` | 本人任务及操作的状态计数 |
| `GET /v1/export/{id}` | 任务、证据和首批最多 1000 条轨迹；完整长轨迹另用分页接口 |

`run` 同步推进当前可执行步骤，也会记录后台推进请求。客户端可轮询任务视图；待授权、待到账或结果未知时先返回当前状态。已请求任务在后续授权及到账后可由恢复循环继续推进。相同任务并发运行时，可能返回 `run_in_progress: true`。

任务视图包含 `task_ref`、`status`、`current_version`、`constraints`、`questions`、`plans`、`operations`、`evidence` 和 `verified_success`。成功判断应同时使用 `status: COMPLETED` 和 `verified_success: true`。部分完成和结果未知都不能显示为任务成功。

重规划保留已完成和待核实节点的编号及业务参数。修改未提交操作会撤销原单次授权，新操作须重新确认。已失败或提交前取消的节点不能再次激活；新的业务尝试必须使用新的节点编号并重新授权。

## 授权确认与委托

先查询 `GET /v1/operations/{id}/preview`，向用户展示操作参数、当前业务条件和 `digest`。用户确认后，发送：

```json
{"expected_digest":"使用预览返回的64位摘要","ttl_seconds":300}
```

请求地址为 `POST /v1/operations/{id}/approvals`。其中摘要必须替换为实际值；示例文本不能直接提交。授权由服务端签发，期限为 1 秒至 3600 秒，绑定主体、操作和完整业务参数。提交前仍会复检余额、资源、令牌和授权。

| 方法与路径 | 行为 |
|---|---|
| `GET /v1/operations/{id}/approvals` | 本人操作的单次授权记录 |
| `POST /v1/approvals/{id}/revoke` | 撤销尚未消费的单次授权 |
| `GET /v1/delegations`、`POST /v1/delegations` | 查询和建立受限委托 |
| `POST /v1/delegations/{id}/revoke` | 撤销委托，阻断后续消费 |
| `POST /v1/operations/{id}/delegation` | 以 `{"delegation_ref":"实际委托编号"}` 绑定本人转账操作 |
| `GET /v1/operations/{id}/compensation-preview` | 查询补偿可用性及候选步骤，不执行补偿 |

委托请求字段为 `source_account_ref`、`recipient_refs`、`per_operation_limit_minor`、`total_limit_minor`、`maximum_operations`、`period_seconds` 和 `ttl_seconds`。所有条件同时成立才可使用。预算消费与提交意图在同一事务保存；明确失败释放预算，但保留尝试频次记录；未知结果继续占用预算。

已消费授权不能撤销已提交操作。停止任务与撤销授权都不能把已付款项当作未发生。临时锁卡可返回恢复步骤；客户端须创建新的任务，重新预览并确认。转账与订阅取消没有自动反向操作。

## 工具参数与业务语义

精确字段和校验规则以 `/v1/tools` 为准。

| 操作 | 参数 | 结果与边界 |
|---|---|---|
| `accounts.balance` | `account_ref` | 当前现金余额及币种；读操作仍需权限 |
| `bills.analyze` | `account_ref`、`days`（1 至 366，默认 30） | 按入账日期统计支出、退款、净额及分类，退款计入入账期 |
| `transfer.create` | 付款账户、收款人、金额、币种、费用、最低保留余额 | 原子更新双方余额，生成可核对流水 |
| `product.subscribe` | `account_ref`、`product_ref`、`amount_minor`、`currency`（默认 `CNY`）、`fee_minor`、`minimum_remaining_balance_minor` | 风险与交易窗口检查；固定价值即时确认 |
| `product.redeem` | 与申购相同 | 先减少可用持仓，等待模拟结算；到账才确认成功，结算失败恢复持仓 |
| `card.set_lock` | `card_ref`、`locked` | 临时锁定或恢复，不代表永久挂失或销卡 |
| `subscription.cancel` | `subscription_ref` | 立即取消模拟商户合同，退款为零，代扣授权保持原状 |

当前产品等待期由模拟产品配置给出，演示产品为 5 秒。模拟报价费用固定为零，提交其他费用会要求更新操作。账单统计排除内部转账和理财资金流，退款带来源流水引用。

## 签名回调

`POST /v1/callbacks/bank` 的 JSON 包含 `event_ref`、`operation_ref` 和可选 `reported_status`。客户端提供 `X-FinGuard-Timestamp`（Unix 秒）与 `X-FinGuard-Signature`。

签名为 `HMAC-SHA256(secret, timestamp + "." + 原始请求体字节)` 的十六进制值。时间偏差允许 300 秒。同一事件编号与相同请求重复到达时返回 `duplicate: true`；相同编号绑定不同内容时返回 409。

回调只触发权威状态查询。`reported_status` 不能直接改变操作结果，迟到或乱序消息不能回退已确认结果。未配置回调密钥时拒绝接收。

## 离线评估与策略

HTTP 入口需要启用 `FINGUARD_EVALUATION_ENABLED` 并持有 `evaluation.manage`。所有业务回放在新建的随机命名空间中执行，使用虚构账户，不改动运行中的业务任务。

| 方法与路径 | 行为 |
|---|---|
| `POST /v1/evaluation/replays` | 提交 `ReplaySuite`，执行固定目标回放 |
| `POST /v1/evaluation/candidates` | 以 `prompt` 创建人工草案 |
| `POST /v1/evaluation/candidates/from-failures` | 提交回放套件，按失败层级生成待评估草案 |
| `GET /v1/evaluation/candidates/{id}` | 查看候选和对照评估记录 |
| `POST /v1/evaluation/candidates/{id}/evaluate` | 当前基线与候选分别回放同一套件 |
| `POST /v1/evaluation/candidates/{id}/release` | 提交 `expected_evaluation_digest`、`reviewed: true` 审核发布 |
| `POST /v1/evaluation/candidates/{id}/rollback` | 撤回当前发布版本，恢复上次版本 |

套件样本见 [backend-replay.json](../tests/scenarios/backend-replay.json)。HTTP 最多 5 个用例；CLI 最多 20 个用例。没有失败时不生成失败候选。自动草案只整理失败层级及保守改进要求，不代表模型已经优化。

发布要求已配置模型网关、至少 2 个用例、包含合法可完成任务、基线与候选都通过、指标不退化，并由运营人员明确审核。候选评估摘要、当前基线或审核条件不匹配时拒绝发布。该流程只管理提示词，不能改变业务权限和授权规则。

## 错误响应

错误形状为 `{"error":{"code":"稳定错误码","message":"说明"}}`；参数错误还包含字段位置及校验类型，不回显输入值和凭据。

| 状态码 | 含义 |
|---|---|
| 401 | 缺少令牌、令牌无效，或回调签名不适用 |
| 403 | 缺少所需权限 |
| 404 | 资源不存在或不属于当前主体 |
| 409 | 请求内容冲突、计划版本变化、业务条件变化或不适用的状态操作 |
| 413 | 请求体超过 1 MiB |
| 422 | 字段、金额、依赖或计划内容不合法 |
| 503 | 数据库、模型、恢复循环不可用，或评估入口未启用 |

业务执行失败也可能通过任务视图中的 `error_code` 和状态表达，HTTP 请求成功并不意味着业务成功。结果未知时继续核实原操作。

配置 DeepSeek 后，创建任务会经过有界候选规划循环。`model_planning` 轨迹记录轮次、耗时、用量和工具状态，不包含推理内容或密钥。配置与真实测试边界见 [DeepSeek Agent 设计](deepseek-design.md)。
