"""resources 单元测试：验证 3 个只读 resources 的注册与调用逻辑。"""

from tool_module import resources


def test_registers_three_resources(fake_mcp):
    resources.register(fake_mcp)
    expected = {"esp32://status", "esp32://logs/recent", "esp32://config"}
    assert set(fake_mcp.resources.keys()) == expected


def test_resource_status(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"status": "connected"}
    resources.register(fake_mcp)
    result = fake_mcp.resources["esp32://status"]()
    mock_call.assert_called_once_with("GET", "/api/status")
    assert result == {"status": "connected"}


def test_resource_recent_logs(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"lines": ["l1", "l2"]}
    resources.register(fake_mcp)
    result = fake_mcp.resources["esp32://logs/recent"]()
    mock_call.assert_called_once_with(
        "GET", "/api/log/history", params={"lines": 50}
    )
    assert result == {"lines": ["l1", "l2"]}


def test_resource_config(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"board": "lckfb_szpi_esp32s3"}
    resources.register(fake_mcp)
    result = fake_mcp.resources["esp32://config"]()
    mock_call.assert_called_once_with("GET", "/api/config")
    assert result == {"board": "lckfb_szpi_esp32s3"}
