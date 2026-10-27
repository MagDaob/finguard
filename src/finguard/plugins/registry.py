"""只管理插件元数据；启用声明不等于获得业务授权。"""

from dataclasses import dataclass

from finguard_plugin_contracts.contracts import PluginManifest

from finguard.backend.catalog import ACTIONS
from finguard.backend.errors import require


@dataclass(frozen=True)
class Registration:
    manifest: PluginManifest
    enabled: bool = False


class PluginRegistry:
    def __init__(self):
        self._entries: dict[str, Registration] = {}

    def register(self, manifest: PluginManifest) -> None:
        # 再校验并复制，防止调用方后续修改嵌套字典。
        manifest = PluginManifest.model_validate_json(manifest.model_dump_json())
        require(
            manifest.plugin_id not in self._entries,
            "PLUGIN_CONFLICT",
            "插件已经注册，请先停用并移除旧声明。",
            409,
        )
        require(
            not any(tool.name in ACTIONS for tool in manifest.tools),
            "PLUGIN_TOOL_CONFLICT",
            "插件不能覆盖核心工具。",
            409,
        )
        self._entries[manifest.plugin_id] = Registration(manifest)

    def describe(self) -> list[dict]:
        return [
            {
                "manifest": entry.manifest.model_dump(mode="json"),
                "enabled": entry.enabled,
                "runtime_available": False,
                "permissions_granted": False,
            }
            for entry in self._entries.values()
        ]

    def get(self, plugin_id: str) -> Registration:
        entry = self._entries.get(plugin_id)
        require(entry is not None, "PLUGIN_NOT_REGISTERED", "插件声明不存在。", 404)
        return Registration(entry.manifest.model_copy(deep=True), entry.enabled)

    def enable(self, plugin_id: str) -> None:
        entry = self.get(plugin_id)
        self._entries[plugin_id] = Registration(entry.manifest, True)

    def disable(self, plugin_id: str) -> None:
        entry = self.get(plugin_id)
        self._entries[plugin_id] = Registration(entry.manifest, False)

    def unregister(self, plugin_id: str) -> None:
        entry = self.get(plugin_id)
        require(not entry.enabled, "PLUGIN_ENABLED", "请先停用插件声明。", 409)
        del self._entries[plugin_id]

    def invoke(self, plugin_id: str, invocation) -> None:
        self.get(plugin_id)
        require(False, "PLUGIN_RUNTIME_UNAVAILABLE", "本期仅提供契约，不执行插件。", 503)
