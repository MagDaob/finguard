# FinGuard Bank Simulator

独立的虚构银行服务，用于 FinGuard 的开发和集成验证。账户、账本、产品、订阅、卡片和延迟结算均为模拟数据。

本目录包含独立的 Python 包、配置示例、迁移和容器入口。服务只依赖中立契约包，不安装 FinGuard 应用包。配置从当前目录 `.env` 的 `SIMBANK_` 字段读取；默认端口为 `8001`。

推荐在仓库根目录执行 [sync-services.ps1](../../scripts/sync-services.ps1) 和 [local-services.ps1](../../scripts/local-services.ps1)，分别安装独立运行环境、启动两边服务。完整数据库初始化、迁移和隔离说明见[服务隔离](../../docs/service-isolation.md)。

手动启动时，在本目录使用已配置的独立环境：

```powershell
.\.venv\Scripts\finguard-bank-simulator.exe migrate
.\.venv\Scripts\finguard-bank-simulator.exe seed-demo
.\.venv\Scripts\finguard-bank-simulator.exe serve
```

业务接口要求服务密钥。故障注入与回放仅在本机测试模块提供，不开放远程管理接口。`seed-demo` 保留已有余额和流水；调用前应确认目标为开发库。
