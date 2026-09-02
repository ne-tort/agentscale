from prodavan.application.agent.runtime_model import sanitize_runtime_model, sdk_fallback_model
from prodavan.domain.ai_keys import ApiKind


def test_sanitize_runtime_model_strips_legacy_http_defaults() -> None:
    assert sanitize_runtime_model("gpt-4o-mini") is None
    assert sanitize_runtime_model("  gpt-4o-mini  ") is None
    assert sanitize_runtime_model("composer-2.5") == "composer-2.5"
    assert sanitize_runtime_model(None) is None
    assert sanitize_runtime_model("") is None


def test_sdk_fallback_model_cursor() -> None:
    assert sdk_fallback_model(ApiKind.CURSOR_SDK) == "default"
    assert sdk_fallback_model("platform_openclaw") is None
