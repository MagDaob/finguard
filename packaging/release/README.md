# 发布材料

本目录保存交付模板。当前 Windows 桌面构建由 `packaging/windows/` 维护；发布版本取自根目录 `pyproject.toml`，准备脚本检查其他清单中的版本是否一致。

在根目录运行 `uv run --locked python scripts/prepare-release.py --self-test`，生成 `dist/releases/<版本>/windows-x64/`。其中只包含本次交付文件、校验清单和与压缩包绑定的验收结果。本机配置、日志、数据库、缓存和旧构建不进入发布目录。

准备产物不等于创建标签或发布 GitHub Release。操作边界、验收方法和待决事项见[发布流程](../../docs/releasing.md)。
