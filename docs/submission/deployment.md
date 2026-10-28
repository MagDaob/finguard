# 源码与部署材料

适用版本：FinGuard 0.3.0。默认使用虚构数据与确定性规划器。源码包包含项目本体、前端、独立模拟银行、契约包、部署入口、配置示例、测试与文档，不含运行数据库、开发凭据、依赖缓存或 EXE。

## 入口与目录

解压后先阅读根目录 [README.md](../../README.md)。全部命令从解压后的项目根目录执行。依赖锁文件随源码提供，安装时仍需访问对应软件源。

| 位置 | 用途 |
|---|---|
| `src/finguard/`、`frontend/` | 应用控制与工作台 |
| `services/bank-simulator/` | 独立模拟服务与配置示例 |
| `packages/` | 银行和插件中立契约 |
| `scripts/`、`tests/` | 启动、隔离、打包及测试用例 |
| `compose.yml`、各 Dockerfile | 三个服务与双数据库容器部署 |
| `docs/` | 架构、API、运行、验收与参赛材料 |

## Windows 本机启动

需要 Python 3.12 至 3.14、uv 和 Node.js 24.12.0。数据库脚本会取得固定摘要的 PostgreSQL Windows 二进制。先安装依赖，再建立隔离服务：

```powershell
uv sync --locked --group dev
.\scripts\local-postgres.ps1 start -Download
uv run --locked python scripts/isolate-local.py
.\scripts\sync-services.ps1
uv run --locked finguard seed-demo
Set-Location frontend
npm ci
npm run build
Set-Location ..
.\scripts\local-services.ps1 start
```

打开 `http://127.0.0.1:8000/tasks`，使用本机生成的 `artifacts/demo-tokens.local.json` 中的用户令牌。令牌不能提交或公开。就绪地址为 `http://127.0.0.1:8000/health/ready`。

首次演示余额为 1,500 元。输入“给小王转300元，至少保留1000元”，确认前不扣款；确认后应完成并核实为 1,200 元，附业务证据。再次执行会继续消耗余额，不应清库伪造结果。

已有环境升级或重复运行时，先阅读 [服务隔离说明](../service-isolation.md)，保留现有数据库和配置。停止使用 `.\scripts\local-services.ps1 stop`，再执行 `.\scripts\local-postgres.ps1 stop`，二者保留数据。

## 容器启动

需要 Docker Engine 与 Compose。复制 `.env.example` 为本机 `.env`。分别生成三个独立随机值，填入 `FINGUARD_POSTGRES_PASSWORD`、`SIMBANK_POSTGRES_PASSWORD` 和 `FINGUARD_BANK_API_KEY`。数据库密码使用 URL 安全十六进制值，服务密钥至少 32 个字符。保留确定性规划器和关闭评估入口。

```powershell
docker compose up -d --build
docker compose exec bank-simulator /app/.venv/bin/finguard-bank-simulator seed-demo
docker compose exec backend uv run --locked --no-sync finguard seed-demo
New-Item -ItemType Directory -Force artifacts | Out-Null
docker compose cp backend:/app/artifacts/demo-tokens.local.json artifacts/demo-tokens.local.json
```

打开 `http://127.0.0.1:5173`。停止用 `docker compose stop`，保留数据卷。备份、迁移与日志说明见 [后端运行文档](../backend.md)。

## 检查与复现

```powershell
uv run --locked pytest -q -m "not postgres"
uv run --locked ruff check .
uv run --locked ruff format --check .
Set-Location frontend
npm test
npm run build
Set-Location ..
```

数据库检查需要专用测试库与创建命名空间的权限。开发配置完成后执行：

```powershell
$env:FINGUARD_REQUIRE_POSTGRES = '1'
uv run --locked pytest -q
uv run --locked finguard replay --suite tests/scenarios/backend-replay.json
```

用例使用随机命名空间和虚构数据，不应指向真实业务数据库。缺少测试库时，跳过不表示通过。分类见 [tests/README.md](../../tests/README.md)。

## 模型、桌面与许可

普通演示不需 API key。DeepSeek 配置和真实费用边界见 [专项设计](../deepseek-design.md)；只有需要时才在本机填写密钥。通用连接校验不激活新服务。

EXE 构建与独立自检见 [桌面说明](../windows-desktop.md)。源码包不附带便携软件。当前便携版未签名，需要 WebView2；真实银行、正式身份和插件执行尚未实现。

源码采用 [Apache-2.0](../../LICENSE)，项目声明见 [NOTICE](../../NOTICE)，第三方组件保留各自许可。完整提交编号、源码路径与摘要随交付清单提供。仓库链接用于辅助浏览，不替代本次源码快照。
