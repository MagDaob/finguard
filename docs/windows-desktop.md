# Windows 桌面版

FinGuard 0.3.0 提供 Windows x64 便携软件。解压后双击 `FinGuard.exe`，在独立窗口中使用任务工作台。软件随包提供 Python 运行环境、项目后端、模拟银行和 PostgreSQL；使用者无需安装 Python、Node.js 或数据库。

## 使用

1. 将 `FinGuard-0.3.0-windows-x64.zip` 解压到可读目录，保留整个 `FinGuard/` 文件夹。
2. 双击 `FinGuard.exe`。首次启动自动建立本机数据库；后续启动保留账户、任务与凭证。
3. 点击“进入本机工作台”，无需手动复制访问令牌。
4. 关闭窗口后等待“安全退出”完成。重新启动后可继续查询已有任务。

目标环境为 Windows 10 1903 及之后版本、Windows 11 的 x64 环境。窗口使用 [Microsoft Edge WebView2 Runtime](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)；本机没有该运行环境时，需从微软官方渠道安装。当前便携包未做代码签名，尚未在所有目标系统和全新虚拟机上验证。

默认使用确定性模拟规划器和虚构账户，不调用付费模型。包内没有开发机 `.env`、API key、演示令牌或业务数据库。桌面本机会话使用当前 Windows 用户拥有的模拟数据，不能替代正式身份登录。

复杂多步骤编辑、额外身份验证和扩展连接的限制与网页版一致，见[前端说明](../frontend/README.md)。本轮未新增桌面模型密钥管理、外部连接激活或第三方插件执行功能。

## 数据与隔离

默认数据目录为 `%LOCALAPPDATA%\FinGuard`，与安装目录、源码工作区和现有开发数据库分开。升级程序目录或重新解压不会重置用户数据；删除数据目录会失去本机账户、任务和凭证，不作为常规故障处理步骤。

| 位置 | 内容 |
|---|---|
| `database/` | 本机 PostgreSQL 数据，应用和模拟银行使用不同数据库、不同账号 |
| `settings.local.json` | 使用 Windows DPAPI 保护的数据库密码、服务密钥及窗口尺寸 |
| `desktop.log` | 组件启动、退出及经过凭据遮蔽的错误；文件轮换限制容量 |
| `backend.log`、`bank.log` | 独立业务服务日志，关闭访问日志，限制保留文件 |
| `database.log` | 本机数据库诊断日志 |

受保护配置绑定当前 Windows 用户，不能直接复制到另一台电脑或另一个用户后当作可迁移配置。跨电脑的数据导出与恢复需另行设计；不要将正在运行的 `database/` 目录直接复制成备份。

所有服务只监听 `127.0.0.1`。程序选择可用端口，并通过每次启动的挑战应答确认服务身份。模拟银行不会继承项目数据库凭据，项目后端不会继承银行数据库凭据；开发机模型环境变量也不会传入桌面服务。

窗口通过受限桥接取得服务端签发的会话，只接受当前本机页面。令牌保存在窗口内存中，退出时请求撤销。业务写入仍经过后端权限、授权、幂等和证据校验，桌面外壳不签发业务授权。

## 优化与退出

- 复用系统 WebView2，不在包内重复附带完整浏览器。
- 业务、设置和诊断页面按需加载，保留金额与会话校验。
- 数据库共享缓冲区设为 32 MB，连接上限为 30，适用于本机模拟工作台。
- 单实例锁防止重复启动同一份数据；重复打开时提示使用已有窗口。
- 记住窗口尺寸，提供中文启动进度、软件图标和版本信息。
- 使用独立停止信号等待后端和模拟银行退出，再关闭数据库。
- Windows Job Object 限制本程序的子进程。启动器异常退出后，由系统回收其后代；下次由 PostgreSQL 执行必要的崩溃恢复。

停止软件不会撤销已发生的业务。未知结果继续按原任务编号查询，不能通过重启创建第二笔付款。

## 从源码构建

在 Windows x64 上安装 uv、Python 3.12 和 Node.js 24.12.0，在项目根目录运行：

```powershell
uv run --locked python scripts/build-windows.py
```

脚本使用 [packaging/windows/](../packaging/windows/) 中独立的 Python 3.12 环境和锁文件，保留项目开发环境。PyInstaller 按[官方冻结方式](https://pywebview.flowrl.com/guide/freezing)分别构建三个可执行文件。数据库二进制来自固定版本的 EDB 官方下载，执行 SHA-256 校验后才提取允许的运行文件；不复制开发机数据库。

Windows PostgreSQL 工具在部分中文路径下存在启动编码问题。构建时为随包的未签名工具加入[微软支持的进程级 UTF-8 声明](https://learn.microsoft.com/windows/apps/design/globalizing/use-utf8-code-page)，保留原有权限清单。原下载档案保持不变，包内修改后的文件另行记录摘要。发现签名二进制时，构建停止；不覆盖签名资源。数据库继续使用 UTF-8，系统全局区域设置保持原配置。

| 产物 | 用途 |
|---|---|
| `dist/FinGuard/FinGuard.exe` | 桌面主程序，需保留同目录依赖 |
| `dist/FinGuard/services/backend/` | 独立项目后端与运行依赖 |
| `dist/FinGuard/services/bank-simulator/` | 独立模拟银行与运行依赖 |
| `dist/FinGuard/postgresql/` | 固定版本的数据库程序，不含数据目录 |
| `dist/FinGuard/manifest.json` | 构建输入与包内文件的 SHA-256 清单 |
| `dist/FinGuard/LICENSE`、`dist/FinGuard/NOTICE` | 项目的 Apache-2.0 许可与声明 |
| `dist/FinGuard/licenses/` | 第三方许可和依赖版本 |
| `dist/FinGuard-0.3.0-windows-x64.zip` | 可复制的完整便携包 |
| `dist/FinGuard-0.3.0-windows-x64.zip.sha256` | 压缩包校验值 |

`--reuse-services` 仅供本地调试外壳时复用已构建的业务引擎。正式交付应运行完整构建，避免使用旧引擎。

## 验证与排障

基础检查：

```powershell
uv run --locked pytest -q tests/test_desktop.py
uv run --locked ruff check .
uv run --locked ruff format --check .
```

实际 EXE 自检必须使用独立目录。以下 PowerShell 示例在项目根目录运行，结果写入忽略的 `artifacts/`：

```powershell
$fgData = Join-Path $PWD 'artifacts/desktop-self-test'
$fgReport = Join-Path $PWD 'artifacts/desktop-self-test-report.json'
$fgProcess = Start-Process -FilePath (Join-Path $PWD 'dist/FinGuard/FinGuard.exe') -ArgumentList @('--self-test', '--data-dir', ('"' + $fgData + '"'), '--report', ('"' + $fgReport + '"')) -WindowStyle Hidden -Wait -PassThru
if ($fgProcess.ExitCode -ne 0) { throw '桌面自检失败，请检查本次报告。' }
Get-Content $fgReport -Raw
```

自检检查确认前不扣款、转账与证据、重复请求、单实例、重启后的余额与凭证，以及退出后的端口。它使用模拟规划器，不调用付费模型。一次自检会在指定目录完成一笔 300 元模拟转账；再次测试应选择新的独立目录，以免消耗同一份测试余额。

发生启动错误时，先保留数据，查看用户目录中的 `desktop.log`。提示文件不完整时重新解压完整便携包；提示受保护配置不可读时，确认使用原 Windows 用户。端口被其他程序占用、安装文件缺失或服务身份不匹配时，程序阻止进入工作台。不要删除数据库、修改交易状态或关闭系统安全保护来绕过故障。

本次实际结果见[桌面验收记录](windows-desktop-validation.md)。[Windows 构建工作流](../.github/workflows/windows-desktop.yml)会在 GitHub Actions 构建便携包并运行实际 EXE 自检，通过后保留产物 14 天。

对外交付使用[发布流程](releasing.md)生成的 `dist/releases/<版本>/windows-x64/` 附件集合。该集合包含软件、说明、项目许可、摘要和与实际包绑定的 EXE 验证结果；本机运行目录和历史测试数据分开保存。
