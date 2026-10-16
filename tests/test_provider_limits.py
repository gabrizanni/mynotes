import pytest

from mynotes import provider_limits


def test_large_utf8_request_is_rejected_without_exposing_content(monkeypatch):
    monkeypatch.setattr(provider_limits.os, "sysconf", lambda _: 1000)
    monkeypatch.setattr(provider_limits.sys, "platform", "darwin")
    private = "秘密" * 200
    credential = "hidden-api-credential"
    with pytest.raises(provider_limits.CommandSizeError) as caught:
        provider_limits.validate_command_size(["claude", private], {"API_KEY": credential})
    assert private not in str(caught.value) and credential not in str(caught.value)
    assert "provider was not started" in str(caught.value)


def test_linux_single_string_limit_is_checked_even_with_large_total_limit(monkeypatch):
    monkeypatch.setattr(provider_limits.sys, "platform", "linux")
    monkeypatch.setattr(provider_limits.os, "sysconf", lambda name: 10000000 if name == "SC_ARG_MAX" else 4096)
    with pytest.raises(provider_limits.CommandSizeError):
        provider_limits.validate_command_size(["codex", "x" * (32 * 4096)], {})


def test_unknown_platform_limit_does_not_invent_an_input_budget(monkeypatch):
    def unavailable(_):
        raise ValueError("not available")
    monkeypatch.setattr(provider_limits.os, "sysconf", unavailable)
    result = provider_limits.validate_command_size(["claude", "test"], {})
    assert result["safe_launch_limit_bytes"] is None
    assert result["single_string_limit_bytes"] is None
    assert result["estimated_launch_bytes"] > 0


def test_environment_bytes_count_toward_the_launch_limit(monkeypatch):
    monkeypatch.setattr(provider_limits.os, "sysconf", lambda _: 1000)
    monkeypatch.setattr(provider_limits.sys, "platform", "darwin")
    with pytest.raises(provider_limits.CommandSizeError):
        provider_limits.validate_command_size(["claude", "small"], {"LARGE_SETTING": "x" * 1000})
