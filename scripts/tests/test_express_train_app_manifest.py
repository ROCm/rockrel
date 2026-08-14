import json
from pathlib import Path


MANIFEST = Path(__file__).parents[2] / "config/express-train-github-app-manifest.json"


def test_app_manifest_is_private_webhookless_and_least_privilege():
    assert MANIFEST.exists()
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["public"] is False
    assert manifest["hook_attributes"]["active"] is False
    assert manifest["default_events"] == []
    assert manifest["default_permissions"] == {
        "administration": "read",
        "contents": "write",
        "issues": "write",
        "pull_requests": "write",
    }
    assert "workflows" not in manifest["default_permissions"]
    assert "actions" not in manifest["default_permissions"]
