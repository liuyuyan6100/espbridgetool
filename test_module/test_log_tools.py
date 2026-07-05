"""log_tools 单元测试：验证 4 个日志工具的注册与调用逻辑。"""

from tool_module import log_tools


def test_registers_seven_tools(fake_mcp):
    log_tools.register(fake_mcp)
    expected = {
        "get_logs", "get_logs_since", "get_last_seq", "clear_logs",
        "dump_logs", "list_log_sessions", "rotate_log_session",
    }
    assert set(fake_mcp.tools.keys()) == expected


def test_get_logs_without_keyword(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"ok": True, "lines": ["line1"]}
    log_tools.register(fake_mcp)
    result = fake_mcp.tools["get_logs"](200)
    mock_call.assert_called_once_with(
        "GET", "/api/log/history", params={"lines": 200}
    )
    assert result == {"ok": True, "lines": ["line1"]}


def test_get_logs_default_lines(fake_mcp, mock_call, mock_fmt):
    log_tools.register(fake_mcp)
    fake_mcp.tools["get_logs"]()
    _, kwargs = mock_call.call_args
    assert kwargs["params"]["lines"] == 100
    assert "filter" not in kwargs["params"]


def test_get_logs_with_keyword(fake_mcp, mock_call, mock_fmt):
    log_tools.register(fake_mcp)
    fake_mcp.tools["get_logs"](50, keyword="error")
    mock_call.assert_called_once_with(
        "GET", "/api/log/history", params={"lines": 50, "filter": "error"}
    )


def test_get_logs_since_passes_seq(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"ok": True, "after_seq": 42, "lines": []}
    log_tools.register(fake_mcp)
    result = fake_mcp.tools["get_logs_since"](10)
    mock_call.assert_called_once_with("GET", "/api/log/since", params={"seq": 10})
    assert result == {"ok": True, "after_seq": 42, "lines": []}


def test_get_logs_since_default_seq_zero(fake_mcp, mock_call, mock_fmt):
    log_tools.register(fake_mcp)
    fake_mcp.tools["get_logs_since"]()
    _, kwargs = mock_call.call_args
    assert kwargs["params"]["seq"] == 0


def test_get_last_seq(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"last_seq": 99, "total": 100}
    log_tools.register(fake_mcp)
    result = fake_mcp.tools["get_last_seq"]()
    mock_call.assert_called_once_with("GET", "/api/log/last-seq")
    assert result == {"last_seq": 99, "total": 100}


def test_clear_logs(fake_mcp, mock_call, mock_fmt):
    log_tools.register(fake_mcp)
    fake_mcp.tools["clear_logs"]()
    mock_call.assert_called_once_with("POST", "/api/log/clear")


def test_dump_logs_default(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"ok": True, "path": "/tmp/dump_x.log", "lines": 5}
    log_tools.register(fake_mcp)
    result = fake_mcp.tools["dump_logs"]()
    mock_call.assert_called_once_with("POST", "/api/log/dump", json={})
    assert result == {"ok": True, "path": "/tmp/dump_x.log", "lines": 5}


def test_dump_logs_with_lines_and_path(fake_mcp, mock_call, mock_fmt):
    log_tools.register(fake_mcp)
    fake_mcp.tools["dump_logs"](lines=100, path="D:/out.log")
    mock_call.assert_called_once_with(
        "POST", "/api/log/dump", json={"lines": 100, "path": "D:/out.log"}
    )


def test_dump_logs_partial_args(fake_mcp, mock_call, mock_fmt):
    log_tools.register(fake_mcp)
    fake_mcp.tools["dump_logs"](lines=50)
    mock_call.assert_called_once_with("POST", "/api/log/dump", json={"lines": 50})


def test_list_log_sessions(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"ok": True, "sessions": [{"name": "session_1.log"}]}
    log_tools.register(fake_mcp)
    result = fake_mcp.tools["list_log_sessions"]()
    mock_call.assert_called_once_with("GET", "/api/log/sessions")
    assert result["sessions"] == [{"name": "session_1.log"}]


def test_rotate_log_session(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"ok": True, "path": "/tmp/session_new.log"}
    log_tools.register(fake_mcp)
    result = fake_mcp.tools["rotate_log_session"]()
    mock_call.assert_called_once_with("POST", "/api/log/rotate")
    assert result["ok"] is True
