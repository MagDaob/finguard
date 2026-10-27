# 自然语言、关联追问与脱敏

适用版本：0.3.0。修订日期：2026 年 10 月 27 日。本轮修复参考项目对照发现的后端问题，前端继续单独设计。

## 完整目标与有限语法

入口位于 [understanding.py](../src/finguard/backend/understanding.py)。每个分句须完整匹配支持的语法；存在未理解的分句时，整个请求进入 `WAITING_CLARIFICATION`，不创建可执行操作。否定、未支持的条件及要求绕过控制的指令也进入澄清。

支持余额查询、明确收款人的转账、账单、产品申赎、临时锁卡及订阅取消。常见转账表达包括“转给小王 100 块”“给小王转一百”和“帮我打款 100 元给小王”。金额使用整数分；“一百二”等含糊表达会被拒绝。

“给小王转100元，再给小王转200元”保留两个目标，分别绑定操作和授权，并按顺序执行。“给小王转100元，再修改密码”无法完整理解，会要求澄清全部目标，不执行第一笔。明确取消全部订阅时，逐项建立操作和授权。

该入口采用有限语法，并非开放文本理解模型。DeepSeek 继续编排已经验证的结构化目标，无法修改金额、目标或授权。使用结构化 `steps` 时，仍由调用者明确提供全部目标，并经过原有校验。

## 账单日期与分类

`bills.analyze` 兼容原有 `days` 参数；不填日期时仍默认为最近 30 天。新增字段如下。

| 字段 | 含义 |
|---|---|
| `start_date`、`end_date` | ISO 日期，开始日期包含，结束日期不包含 |
| `timezone` | 明确日期区间要求 `Asia/Shanghai`，按 UTC+08:00 处理 |
| `category` | 可选 `food`、`transport` 或 `shopping` |

日期须成对出现，区间为 1 至 366 天，不能与 `days` 混用。旧参数序列化形态保持不变，不重写已有操作摘要。

“上个月”使用上一个自然月；“本月”从月初至当日结束；“最近7天”保留滚动查询。“看看我上个月吃饭花了多少钱”生成自然月区间和 `food` 分类。分类以银行记录的分类码为准，没有匹配记录时返回零，不推测商户类别。

统计仍采用已入账现金流，分别计算支出、退款和净支出，排除内部转账与理财流动。模拟器按明确时间边界查询，返回区间和分类，证据继续关联原操作。

## 关联前一任务的追问

`POST /v1/tasks` 新增可选 `context_task_ref`，指向同一用户可访问的前一任务。迁移 `003-task-context.sql` 仅新增关联列，不改写既有任务、授权或业务记录。

```json
{
  "client_request_ref": "followup-unique-ref",
  "user_request": "还剩多少",
  "context_task_ref": "task-reference-returned-by-server"
}
```

此前任务涉及唯一账户时，追问可以关联该账户，并新建余额查询任务。执行时重新读取银行余额，生成新的业务证据。没有关联或账户不明确时继续澄清；其他用户的任务返回不可访问。

关联不继承金额、收款人、约束或授权，不把历史结果当作当前余额。“再给他转一样的金额”等付款指代仍要求明确目标。这是受限任务上下文，不是任意多轮聊天记忆。前端可保留上一个任务编号，并显式提交关联。

## 分级验证边界

服务端配置 `FINGUARD_STEP_UP_THRESHOLD_MINOR` 控制额外验证门槛，默认 100,000 分，即单笔超过 1,000 元时触发。该配置是模拟开发策略，不代表任何银行标准；金额包括费用，等于门槛时仍走普通确认。

预览返回 `authentication_policy`：`level` 为 `read`、`confirmation` 或 `step_up`，并明确 `step_up_required` 和 `step_up_available`。当前验证服务不可用，超过门槛的普通授权返回 `STEP_UP_REQUIRED`；运行保持等待且没有扣款。

执行时再次检查策略。旧确认和委托不能绕过门槛；已提交的未知操作继续核实，不因新门槛重复提交。短信、验证器或其他实际身份验证服务仍需独立接入，不能由模型声明验证成功。

## 任务文字与导出

[privacy.py](../src/finguard/backend/privacy.py)处理已知密钥格式、带标签的密码或密钥、Bearer 令牌、连续银行卡号和手机号。新任务原话先脱敏再持久化；历史原话在读取、列表和导出时脱敏。历史数据库内容不自动回写。

请求身份仍使用原始请求的摘要校验，重试不能把不同原始内容当作同一请求。任务与授权中的操作编号、金额和绑定不受脱敏影响。

导出执行递归脱敏，`contains_credentials` 改为 `null`，并增加 `redaction.applied`、`redaction.policy` 和 `redaction.complete_detection_guaranteed: false`。它表示应用了已知规则，不保证发现任意秘密。前端应显示脱敏状态，不把该字段解释为可直接公开分享。

## 验证

专项入口为 [test_understanding.py](../tests/backend/test_understanding.py)、[test_privacy.py](../tests/backend/test_privacy.py)、[test_api.py](../tests/backend/test_api.py)及 [test_bank.py](../tests/backend/test_bank.py)。

```powershell
uv run --locked pytest -q tests/backend/test_understanding.py tests/backend/test_privacy.py
uv run --locked pytest -q tests/backend/test_api.py tests/backend/test_bank.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv build --all --no-sources
```

检查覆盖同义表达、中文金额、目标遗漏、月界与退款分类、账户归属、最新余额、脱敏、独立授权以及额外验证拦截。测试使用虚构数据，不调用真实银行或付费模型。
