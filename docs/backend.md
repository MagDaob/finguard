# 后端运行说明

适用版本：0.2.0。当前服务使用虚构主体和模拟银行，数据保存在 PostgreSQL。接口默认监听 `127.0.0.1:8000`。

## 配置和身份

配置从当前工作目录的 `.env` 读取，同名环境变量优先。[配置示例](../.env.example)不包含可用凭据。访问令牌只在数据库中保存 SHA-256 摘要，明文只写入指定本地文件。

| 配置 | 用途与默认值 |
|---|---|
| `FINGUARD_DATABASE_URL` | 必填 PostgreSQL 连接地址 |
| `FINGUARD_DATABASE_SCHEMA` | 业务命名空间，默认 `finguard` |
| `FINGUARD_WORKER_ENABLED` | 服务内置恢复循环，默认 `true` |
| `FINGUARD_WORKER_INTERVAL_SECONDS` | 核实间隔，默认 1 秒，允许 0.1 秒至 60 秒 |
| `FINGUARD_CALLBACK_SECRET` | 银行回调 HMAC 密钥；未配置时回调关闭 |
| `FINGUARD_CORS_ORIGINS` | 跨域来源 JSON 数组，默认空数组 |
| `FINGUARD_EVALUATION_ENABLED` | HTTP 离线评估入口，默认 `false`，仍需运营权限 |
| `FINGUARD_MODEL_URL` | 可选候选计划网关，未配置时使用确定性基线 |
| `FINGUARD_MODEL_API_KEY` | 仅发送给已配置网关的访问密钥 |
| `FINGUARD_MODEL_ALLOW_LOCAL_HTTP` | 允许本机 HTTP 网关，默认 `false` |

使用 `finguard create-user --subject user-001 --name Alice --output artifacts/alice.local.json` 在可信本机创建主体和令牌。`--admin` 额外授予评估管理权限。此命令不创建银行账户，真实身份开户需另行实现。演示令牌通过 `seed-demo` 取得。

每次业务调用重新检查主体、令牌、权限和资源归属。单次操作确认属于另一层业务授权；访问令牌本身不能替代写操作确认。委托仅支持指定账户及白名单收款人的转账。

## Windows 本机数据库

[local-postgres.ps1](../scripts/local-postgres.ps1)首次可下载 EDB 发布的 PostgreSQL 18.6 Windows x64 二进制包，约 328 MiB。归档 SHA-256 固定在脚本内，来源为 [EDB 下载服务](https://get.enterprisedb.com/postgresql/postgresql-18.6-1-windows-x64-binaries.zip)。脚本不安装系统服务，不修改已有 `.env`，不删除数据库。

```powershell
.\scripts\local-postgres.ps1 start -Download
.\scripts\local-postgres.ps1 status
```

端口首次自动选择，保存在 `artifacts/postgresql/port.local`；密码保存在同目录的 `password.local`。首次生成的 `.env` 使用该端口。该目录包含本机秘密和数据库文件，已整体忽略。

需要停止数据库时，先停止后端，再执行：

```powershell
.\scripts\local-postgres.ps1 stop
```

保留数据目录和 `.env` 即可再次启动。不要把工作副本的运行目录作为版本控制备份。

## 容器部署

需要 Docker Engine 和 Compose。以下命令在项目根目录运行，`.env` 中必须设置随机的十六进制 `FINGUARD_POSTGRES_PASSWORD`。可以用 `uv run python -c "import secrets; print(secrets.token_hex(32))"` 在本机生成，再写入 `.env`；不要提交该值。

```powershell
docker compose up -d --build
docker compose exec backend uv run --locked --no-sync finguard seed-demo
New-Item -ItemType Directory -Force artifacts | Out-Null
docker compose cp backend:/app/artifacts/demo-tokens.local.json artifacts/demo-tokens.local.json
```

数据库不发布主机端口，后端仅发布本机端口 8000。容器以普通用户运行，命名卷保存数据库和令牌文件。后端启动时自动执行尚未应用的迁移；已有版本的迁移不重复执行。

```powershell
docker compose logs --tail 100 backend
docker compose stop
```

`stop` 保留数据卷。升级前备份数据库，检查新增迁移；禁止通过重写迁移文件修改已部署结构。当前配置使用单个后端和内置恢复循环，也可部署独立恢复进程。

## 恢复与备份

运行 `finguard worker --once` 核实一次待处理任务，或运行 `finguard worker` 持续处理。独立进程共享同一数据库；服务可将内置循环关闭。PostgreSQL 会话锁确保同一任务只有一个调度器推进，银行操作锁和幂等记录确保并发请求不重复记账。

恢复先查询原操作。模拟银行支持对不存在的操作写入失败栅栏，阻断迟到提交；这项能力不能直接假定真实银行也提供。已提交任务即使收到停止请求或原令牌失效，仍继续核实；新业务步骤需要有效执行身份。

备份使用 PostgreSQL 的 `pg_dump`，恢复使用 `pg_restore`，同时备份本机配置和访问密钥。通过数据库工具的受保护凭据文件或交互提示提供密码。恢复环境应与线上环境隔离，先核对账本及未完成操作，再开放调度。

## 模型网关

网关采用供应商无关协议：后端向配置地址发送 `POST`，正文包含 `goals`、`constraints`、`tools`、`policy` 和 `prompt_candidate`。可选 API 密钥通过 `Authorization: Bearer ...` 发送。只有已校验的业务目标和约束进入模型请求，不发送访问令牌、授权记录或数据库地址。

响应形状如下，`steps` 使用 `/v1/tools` 和任务接口中的同一结构：

```json
{
  "planner_version": "your-planner-v1",
  "steps": [
    {
      "step_ref": "balance",
      "action": "accounts.balance",
      "arguments": {"account_ref": "account-demo-001"},
      "depends_on": [],
      "funds_available_after": []
    }
  ]
}
```

响应必须完整保留输入目标的编号、操作、参数及必要条件。控制程序拒绝增删目标、修改金额、移除条件和循环依赖。网关只提供候选计划；主体解析、授权、业务规则和证据核验仍由程序执行。默认要求 HTTPS，不跟随重定向，超时为 20 秒，响应上限为 1 MiB。

当前自然语言理解使用有限的确定性解析器，网关参与结构化目标的候选编排。更广泛的模型意图理解需要单独设计确认边界和评估集。本版本已用模拟 HTTP 网关验证协议和控制规则，尚未验证任何真实模型的任务完成效果。

## 回放与优化

```powershell
uv run --locked finguard replay --suite tests/scenarios/backend-replay.json
uv run --locked finguard replay --suite tests/scenarios/backend-replay.json --model --output artifacts/model-replay.json
```

第一条使用套件中的固定结构化目标，第二条重新调用已配置模型网关。每个用例创建随机 PostgreSQL 命名空间，初始化虚构数据，结束后仅清理该命名空间。回放权限需要允许创建命名空间；生产业务角色应与评估角色分离。HTTP 入口最多包含 5 个用例，命令行最多 20 个。

报告包含预期与实际结果、轨迹、耗时和失败层级。`business_completion_rate` 的分母只包含预期应成功的用例；`correct_handling_rate` 包含拒绝、停止和等待授权等用例。两者都不代表真实模型或真实银行的成功率。

运营接口可以根据失败回放生成提示词草案，也可以提交人工候选。候选必须经过当前基线与候选的隔离对照评估，再显式审核发布。未配置模型时，固定回放即使通过，也不能批准模型策略发布。发布绑定评估摘要和当前基线；基线变化后必须重评。回退恢复上次发布版本。

优化只改变模型提示词。它不会修改授权、权限、限额、账本或控制代码。测试中的模拟网关只证明流程可运行，真实策略质量仍需真实模型和代表性数据评估。

## 故障定位

| 现象 | 检查与处理 |
|---|---|
| `DATABASE_UNAVAILABLE` | 检查数据库状态、`.env` 地址与本机端口；错误响应不包含连接地址 |
| `WAITING_APPROVAL` | 查看操作预览、单次授权期限，或委托对象、单笔限额、预算和频次 |
| `UNKNOWN` | 查看操作错误码与轨迹，保持恢复任务运行；不要另建同义付款尝试掩盖未知结果 |
| 证据核实失败 | 检查权威记录、流水和引用关系，证据不完整时不会报告成功 |
| `IMMUTABLE_OPERATION` | 重规划须保留已完成及待核实事实 |
| `TERMINAL_OPERATION` | 已失败或提交前取消的节点不重新激活；确需新尝试时建立新节点并重新授权 |
| `BASELINE_CHANGED` | 优化发布基线已变化，重新进行对照评估 |
| 访问返回 401 | 令牌到期、撤销或主体停用；演示环境可重新签发本地令牌 |

结构化轨迹记录状态和业务引用，不保存模型隐藏推理。对外部署还需增加正式身份服务、TLS 入口、集中日志、请求限流和监控；这些属于真实服务部署阶段。
