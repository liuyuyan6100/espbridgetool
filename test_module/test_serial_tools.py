"""serial_tools 单元测试：验证 6 个串口工具的注册与调用逻辑。

每个工具至少验证：调用了正确的 HTTP method/path，参数正确，且返回 fmt 的结果。
"""

from tool_module import serial_tools


def test_registers_six_tools(fake_mcp):
    serial_tools.register(fake_mcp)
    expected = {
        "list_serial_ports", "get_status", "open_serial",
        "close_serial", "send_command", "send_and_collect",
    }
    assert set(fake_mcp.tools.keys()) == expected


def test_list_serial_ports_extracts_available_ports(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {
        "ok": True,
        "available_ports": [{"device": "COM6", "description": "USB-SERIAL CH340"}],
    }
    serial_tools.register(fake_mcp)
    result = fake_mcp.tools["list_serial_ports"]()
    mock_call.assert_called_once_with("GET", "/api/status")
    assert result == [{"device": "COM6", "description": "USB-SERIAL CH340"}]


def test_list_serial_ports_returns_error_when_bridge_down(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"ok": False, "error": "无法连接 Serial Bridge"}
    serial_tools.register(fake_mcp)
    result = fake_mcp.tools["list_serial_ports"]()
    assert result == {"ok": False, "error": "无法连接 Serial Bridge"}


def test_get_status_calls_status_endpoint(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"status": "connected", "port": "COM6"}
    serial_tools.register(fake_mcp)
    result = fake_mcp.tools["get_status"]()
    mock_call.assert_called_once_with("GET", "/api/status")
    assert result == {"status": "connected", "port": "COM6"}


def test_open_serial_sends_port_and_baud(fake_mcp, mock_call, mock_fmt):
    serial_tools.register(fake_mcp)
    fake_mcp.tools["open_serial"]("COM7", 921600)
    mock_call.assert_called_once_with(
        "POST", "/api/serial/open", json={"port": "COM7", "baud": 921600}
    )


def test_open_serial_default_baud(fake_mcp, mock_call, mock_fmt):
    serial_tools.register(fake_mcp)
    fake_mcp.tools["open_serial"]("COM6")
    args, kwargs = mock_call.call_args
    assert kwargs["json"]["baud"] == 115200


def test_close_serial(fake_mcp, mock_call, mock_fmt):
    serial_tools.register(fake_mcp)
    fake_mcp.tools["close_serial"]()
    mock_call.assert_called_once_with("POST", "/api/serial/close")


def test_send_command_sends_cmd_and_hex(fake_mcp, mock_call, mock_fmt):
    serial_tools.register(fake_mcp)
    fake_mcp.tools["send_command"]("AT+RST", hex_mode=True)
    mock_call.assert_called_once_with(
        "POST", "/api/send", json={"cmd": "AT+RST", "hex": True}
    )


def test_send_command_default_hex_false(fake_mcp, mock_call, mock_fmt):
    serial_tools.register(fake_mcp)
    fake_mcp.tools["send_command"]("help")
    _, kwargs = mock_call.call_args
    assert kwargs["json"]["hex"] is False


def test_send_and_collect_sends_correct_payload_and_timeout(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"ok": True, "collected_lines": ["rst:0x1"]}
    serial_tools.register(fake_mcp)
    result = fake_mcp.tools["send_and_collect"]("help", wait_seconds=5.0)
    mock_call.assert_called_once_with(
        "POST",
        "/api/send-and-collect",
        json={"cmd": "help", "wait": 5.0, "hex": False},
        timeout=30.0,
    )
    assert result == {"ok": True, "collected_lines": ["rst:0x1"]}


def test_send_and_collect_default_wait(fake_mcp, mock_call, mock_fmt):
    serial_tools.register(fake_mcp)
    fake_mcp.tools["send_and_collect"]("help")
    _, kwargs = mock_call.call_args
    assert kwargs["json"]["wait"] == 2.0
