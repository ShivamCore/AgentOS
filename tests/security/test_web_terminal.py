"""
/ws/terminal runs shell commands on the host. It must be refused unless it is
explicitly enabled, the client is on loopback, and the API key (if configured)
is presented.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from starlette.websockets import WebSocketDisconnect

from backend.api.routers import ws as ws_mod


def fake_ws(
    host: str,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
        query_params=query or {},
    )


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ws_mod.TERMINAL_ENV_FLAG, raising=False)
    assert "disabled" in ws_mod.terminal_refusal(fake_ws("127.0.0.1"))


def test_enabled_requires_loopback(monkeypatch):
    monkeypatch.setenv(ws_mod.TERMINAL_ENV_FLAG, "1")
    monkeypatch.setattr(ws_mod.settings, "API_KEY", None)
    assert ws_mod.terminal_refusal(fake_ws("127.0.0.1")) is None
    assert ws_mod.terminal_refusal(fake_ws("::1")) is None
    assert "loopback" in ws_mod.terminal_refusal(fake_ws("192.168.1.20"))


def test_enabled_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setenv(ws_mod.TERMINAL_ENV_FLAG, "1")
    monkeypatch.setattr(ws_mod.settings, "API_KEY", "s3cret")
    assert "API key" in ws_mod.terminal_refusal(fake_ws("127.0.0.1"))
    assert "API key" in ws_mod.terminal_refusal(
        fake_ws("127.0.0.1", {"x-api-key": "nope"})
    )
    assert (
        ws_mod.terminal_refusal(fake_ws("127.0.0.1", {"x-api-key": "s3cret"})) is None
    )
    assert (
        ws_mod.terminal_refusal(fake_ws("127.0.0.1", query={"api_key": "s3cret"}))
        is None
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_endpoint_closes_without_running_anything(
    test_client, monkeypatch, tmp_path, enabled
):
    # TestClient connects from host "testclient" (not loopback), so it is refused either way.
    if enabled:
        monkeypatch.setenv(ws_mod.TERMINAL_ENV_FLAG, "1")
    else:
        monkeypatch.delenv(ws_mod.TERMINAL_ENV_FLAG, raising=False)
    monkeypatch.setattr(ws_mod, "TERMINAL_CWD", str(tmp_path))
    marker = tmp_path / "pwned"
    with pytest.raises(WebSocketDisconnect) as exc:
        with test_client.websocket_connect("/ws/terminal") as conn:
            conn.send_json({"cmd": f"touch {marker}"})
            conn.receive_json()
    assert exc.value.code == 1008
    assert not marker.exists()
