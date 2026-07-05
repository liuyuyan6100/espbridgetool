"""bridge_client 单元测试：fmt、rebuild_client、call 的成功与各类错误分支。

这里不 mock bridge_client 自身（被测对象），而是：
  - fmt: 纯函数，直接断言输出
  - rebuild_client: 改变模块级配置，断言状态变化
  - call: 用 monkeypatch 替换底层 httpx 客户端的 request，注入各类异常
"""

import json

import httpx
import pytest

import tool_module.bridge_client as bc


# ---- fmt ----

def test_fmt_returns_indented_json_string():
    out = bc.fmt({"ok": True, "port": "COM6"})
    assert isinstance(out, str)
    assert json.loads(out) == {"ok": True, "port": "COM6"}


def test_fmt_keeps_chinese_unescaped():
    out = bc.fmt({"error": "无法连接"})
    assert "无法连接" in out  # ensure_ascii=False
    assert "\\u" not in out


def test_fmt_handles_list():
    out = bc.fmt([1, 2, 3])
    assert json.loads(out) == [1, 2, 3]


# ---- rebuild_client ----

def test_rebuild_client_updates_config():
    orig_host, orig_port, orig_url = bc.BRIDGE_HOST, bc.BRIDGE_PORT, bc.BRIDGE_URL
    try:
        bc.rebuild_client("10.0.0.1", 9999)
        assert bc.BRIDGE_HOST == "10.0.0.1"
        assert bc.BRIDGE_PORT == 9999
        assert bc.BRIDGE_URL == "http://10.0.0.1:9999"
        assert bc._client is not None
    finally:
        bc.rebuild_client(orig_host, orig_port)


# ---- call: 成功 ----

def test_call_success_returns_json(monkeypatch):
    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"status": "connected", "port": "COM6"}

    fake_client = type("C", (), {"request": lambda self, *a, **k: FakeResp()})()
    monkeypatch.setattr(bc, "_client", fake_client)

    r = bc.call("GET", "/api/status")
    assert r == {"status": "connected", "port": "COM6"}


def test_call_passes_kwargs_to_request(monkeypatch):
    from unittest.mock import MagicMock

    class FakeResp:
        def json(self):
            return {"ok": True}

    mock_client = MagicMock()
    mock_client.request.return_value = FakeResp()
    monkeypatch.setattr(bc, "_client", mock_client)

    bc.call("POST", "/api/serial/open", json={"port": "COM7"}, timeout=12.0)
    mock_client.request.assert_called_once_with(
        "POST", "/api/serial/open", timeout=12.0, json={"port": "COM7"}
    )


# ---- call: 非 JSON 响应 ----

def test_call_non_json_response(monkeypatch):
    class FakeResp:
        status_code = 500
        text = "Internal Server Error"
        def json(self):
            raise ValueError("not json")

    fake_client = type("C", (), {"request": lambda self, *a, **k: FakeResp()})()
    monkeypatch.setattr(bc, "_client", fake_client)

    r = bc.call("GET", "/api/status")
    assert r["ok"] is False
    assert "非 JSON 响应" in r["error"]
    assert "HTTP 500" in r["error"]


# ---- call: 连接错误 ----

def test_call_connect_error(monkeypatch):
    def raise_connect(*a, **k):
        raise httpx.ConnectError("connection refused")

    fake_client = type("C", (), {"request": raise_connect})()
    monkeypatch.setattr(bc, "_client", fake_client)

    r = bc.call("GET", "/api/status")
    assert r["ok"] is False
    assert "无法连接 Serial Bridge" in r["error"]


# ---- call: 超时 ----

def test_call_timeout_error(monkeypatch):
    def raise_timeout(*a, **k):
        raise httpx.TimeoutException("timed out")

    fake_client = type("C", (), {"request": raise_timeout})()
    monkeypatch.setattr(bc, "_client", fake_client)

    r = bc.call("GET", "/api/status", timeout=5.0)
    assert r["ok"] is False
    assert "请求超时" in r["error"]
    assert "5.0s" in r["error"]


# ---- call: 其它异常 ----

def test_call_generic_exception(monkeypatch):
    def raise_runtime(*a, **k):
        raise RuntimeError("boom")

    fake_client = type("C", (), {"request": raise_runtime})()
    monkeypatch.setattr(bc, "_client", fake_client)

    r = bc.call("GET", "/api/status")
    assert r["ok"] is False
    assert "请求异常" in r["error"]
    assert "RuntimeError" in r["error"]
    assert "boom" in r["error"]
