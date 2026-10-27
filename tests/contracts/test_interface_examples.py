from pathlib import Path

from finguard_plugin_contracts.contracts import PluginManifest

from finguard.plugins.registry import PluginRegistry


def test_published_example_validates_without_running_a_plugin():
    manifest = PluginManifest.model_validate_json(
        Path("plugins/examples/personal-report.json").read_text(encoding="utf-8")
    )
    registry = PluginRegistry()
    registry.register(manifest)
    assert registry.describe()[0]["runtime_available"] is False
    assert registry.describe()[0]["permissions_granted"] is False
