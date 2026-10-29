# 发布准备与文件规范

FinGuard 当前准备 0.3.0 Windows x64 预览候选，采用 Apache-2.0。本流程整理和验证交付附件，不创建 Git 标签、GitHub Release，也不上传用户数据。

本次实际整理、归档和验证结果见[发布准备记录](release-preparation-validation.md)。

## 目录职责

| 路径 | 职责与交付规则 |
|---|---|
| `src/`、`frontend/`、`services/`、`packages/` | 项目源码、前端、独立模拟银行和共享契约，进入版本控制 |
| `packaging/windows/` | EXE 构建入口、资源和独立锁文件，进入版本控制 |
| `packaging/release/` | 发布附件模板，进入版本控制 |
| `docs/releases/`、`CHANGELOG.md` | 版本变化、兼容性和当前发布状态，进入版本控制 |
| `dist/FinGuard/` | 本机可运行的完整桌面目录，不提交 Git |
| `dist/python/<版本>/` | 本次 Python 安装包，按版本分开，不与桌面交付混放 |
| `dist/releases/<版本>/windows-x64/` | 对外交付的唯一附件集合，不提交 Git，作为构建产物保存 |
| `artifacts/release-preparation/` | 随机目录中的解压、自检、日志和模拟数据库，不交付 |
| `artifacts/archive/` | 本机旧构建和测试目录，保留恢复映射，不交付 |
| `%LOCALAPPDATA%\FinGuard` | 实际桌面用户数据，不移动、不打包、不作为清理对象 |

`dist` 根目录保留当前桌面程序及其构建 ZIP，兼容已有本机入口。正式交付使用版本目录，避免把旧版本安装包、工作目录或测试库一起上传。

## 版本与许可

版本以根目录 `pyproject.toml` 为准。发布准备脚本核对共享包、模拟银行、桌面构建项目、前端清单与锁文件、桌面运行常量，以及 Windows 文件资源中的版本。

根目录 [LICENSE](../LICENSE) 使用 Apache 官方原文，[NOTICE](../NOTICE)记录项目声明。独立 Python 包分别携带相同的许可文本和声明；前端与 Python 元数据使用 `Apache-2.0`。第三方许可继续分别保留，不能用项目许可覆盖依赖条款。

软件包必须同时包含项目 `LICENSE`、`NOTICE` 和第三方 `licenses/`。本次新增许可后，旧构建作为历史保留；对外交付只能选择更新后的构建。

## 准备顺序

以下 PowerShell 命令在项目根目录执行。

检查发布规则与格式：

```powershell
uv run --locked pytest -q tests/test_release.py tests/test_desktop.py
uv run --locked ruff check .
uv run --locked ruff format --check .
```

构建 Windows 软件：

```powershell
uv run --locked python scripts/build-windows.py
```

构建 Python 安装包时使用独立位置：

```powershell
uv build --all --no-sources --out-dir dist/python/0.3.0
```

检查并准备 Windows 附件：

```powershell
uv run --locked python scripts/prepare-release.py --self-test
uv run --locked python scripts/prepare-release.py --verify dist/releases/0.3.0/windows-x64
```

准备脚本检查完整文件清单、SHA-256、许可、构建输入和本机已知秘密。它拒绝路径穿越、Windows 非法文件名、重复文件及符号链接。解压后，在随机的中文程序目录和中文数据目录中运行实际 EXE 自检；报告通过后才生成附件集合。自检只使用虚构数据，不调用付费模型。

## 附件集合

```text
dist/releases/0.3.0/windows-x64/
├── FinGuard-0.3.0-windows-x64.zip
├── README.md
├── RELEASE-NOTES.md
├── LICENSE
├── NOTICE
├── THIRD-PARTY-NOTICES.md
├── DEPENDENCIES.json
├── VERIFICATION.json
├── release.json
└── SHA256SUMS
```

`SHA256SUMS` 覆盖其余全部附件。`VERIFICATION.json` 将压缩包摘要、包内文件检查、构建输入匹配及实际 EXE 自检关联起来。校验值用于完整性检查，不能替代代码签名和可信下载来源。

`release.json` 标记 `prepared-not-published`、预览渠道、计划标签及源码记录。`build_source_dirty` 为真时，说明构建时有未提交修改；公开交付优先使用干净提交上的云端构建。该文件不自动宣称 Release 已存在。

`DEPENDENCIES.json` 包含构建环境中的 Python 发行包、前端生产依赖、数据库版本及外部 WebView2 需求。它可能包含构建工具，不宣称是完整的运行时软件物料清单。

已有发布目录不覆盖。尚未公开的候选需要修订时，先保留旧候选及摘要，再选择新的候选目录；已经公开的版本需要变更时，应升级版本号并重新验证。

## 发布前核对

1. 版本、许可、变化说明及支持系统与实际产物一致。
2. 常规检查和 Windows 打包工作流通过，附件二次校验通过。
3. 确认选择的是预览发布还是稳定发布。当前为模拟业务、未签名便携版，完整目标系统 GUI 兼容性尚未验收。
4. 保留上一版本程序包和交付摘要。说明用户数据位置、升级限制及故障保留方式。
5. 用户明确要求公开发布后，再创建对应标签或 GitHub Release，并核对线上附件摘要。本次整理不执行这一步。

Windows 工作流只上传上述附件集合，保留 14 天。它不会上传随机自检目录、数据库或日志，也不会修改仓库发布设置。

## 历史归档

本次旧 Python 构建和桌面测试目录保存在 `artifacts/archive/release-preparation-2026-10-27/`。本机 `archive-map.local.json` 记录原路径与归档位置。此前的 Windows ZIP 另存为许可更新前的快照；当前桌面入口继续保留。

归档仅改变工作区内的历史文件位置。正在使用的开发数据库、独立服务环境、配置和桌面用户数据不在归档范围内。后续清理档案时应先核对内容和所有权，不按模糊目录名批量删除。

### 2026 年 10 月 29 日清理记录

旧归档已压缩为本机 `artifacts/archive/release-preparation-2026-10-27-compressed-20261029.zip`。共 16,766 个文件，逐文件 SHA-256 校验通过后才移除展开副本。原目录保留 `archive-map.local.json`，另加 `CONTENT-ARCHIVED.local.json` 指向压缩归档；ZIP 内路径相对于工作区根目录，恢复时可还原原有归档位置。

同时清理 PyInstaller 中间缓存、预览版后端重复副本、一次性 wheel 验收环境、Python 与前端缓存。临时脚本、旧日志和界面验收文件先压缩到本机 `artifacts/cleanup-20261029/temporary-scripts-and-qa.zip`，再移除展开文件。清理结果与校验明细保存在该目录的本机 JSON 记录中。

本次共移除 19,406 个展开文件。保留压缩归档及校验记录后，净释放约 850 MB。源码、开发和构建环境、当前数据库、配置、可运行的桌面程序与预览目录、发布附件及参赛交付文件均保留。未修改用户目录内的数据，也未清空业务数据库。
