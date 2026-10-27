# FinGuard {{VERSION}} Windows 便携版

这是用于开发和验收的模拟金融任务软件。全部账户、产品和交易均为虚构数据，默认使用模拟规划器。程序没有内置真实模型 API key。

## 启动

1. 解压 `{{ARCHIVE}}`。
2. 保留完整 `FinGuard/` 目录，双击其中的 `FinGuard.exe`。
3. 等待初始化，点击“进入本机工作台”。

不要只复制主程序。目标环境为 Windows 10 1903 及之后版本、Windows 11 x64，需要 [Microsoft Edge WebView2 Runtime](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)。当前软件为未签名便携版，尚未完成所有目标系统的兼容性验收。

## 数据与升级

数据保存在 `%LOCALAPPDATA%\FinGuard`，与程序目录分开。关闭窗口后等待安全退出，再更换程序目录。保留用户数据目录；跨电脑迁移和数据库版本回退尚未提供自动工具。

保留上一版本的完整程序包。若新版本启动失败，先保留数据和日志；存在数据库迁移时，不要直接使用旧程序写入新数据。

## 核对交付文件

`SHA256SUMS` 记录发布目录中其他文件的 SHA-256。`VERIFICATION.json` 将实际 EXE 自检结果关联到本压缩包；`release.json` 记录版本、准备来源、许可状态和未发布状态。`DEPENDENCIES.json` 是构建环境和前端生产依赖清单，不宣称覆盖系统上安装的全部组件。

本目录准备于 {{PREPARED_DATE}}，尚未创建公开 Release。项目许可状态：{{LICENSE_STATUS}}。第三方许可位于压缩包的 `FinGuard/licenses/`，说明见 `THIRD-PARTY-NOTICES.md`。

## 当前范围

支持任务计划、逐项确认、证据、账户账单、受限委托、模拟产品、卡片和订阅管理。正式身份、真实额外验证、模型密钥管理、外部连接激活和插件执行仍待后续开发。具体变化见 `RELEASE-NOTES.md`。

项目源码与问题记录：[MagDaob/finguard](https://github.com/MagDaob/finguard)。反馈问题时提供版本和错误现象，先移除密钥、令牌、个人信息及业务数据；不要公开上传完整用户数据目录。
