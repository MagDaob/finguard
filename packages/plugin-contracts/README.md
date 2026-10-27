# FinGuard 插件契约

独立的插件清单、配置模式、调用上下文、结果及宿主协议。不依赖 FinGuard 应用或模拟银行，也不加载或执行用户代码。

协议版本为 `1.0`，包版本为 `0.3.0`。当前只接受 `contract_only` 入口，提供格式与模式检查。权限、隔离和业务结果仍由未来宿主核实。

定义见 [contracts.py](src/finguard_plugin_contracts/contracts.py)，模式检查见 [schemas.py](src/finguard_plugin_contracts/schemas.py)。完整说明见[扩展接口](../../docs/extension-interfaces.md)，示例见[个人报表清单](../../plugins/examples/personal-report.json)。
