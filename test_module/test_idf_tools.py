"""idf_tools 单元测试：验证 9 个 ESP-IDF 工具链工具的注册与调用逻辑。"""

from tool_module import idf_tools


def test_registers_nine_tools(fake_mcp):
    idf_tools.register(fake_mcp)
    expected = {
        "get_idf_config", "set_idf_config", "list_idf_versions",
        "list_idf_projects", "list_boards", "select_board",
        "build", "flash", "get_flash_progress", "clean_build",
    }
    assert set(fake_mcp.tools.keys()) == expected


def test_get_idf_config(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["get_idf_config"]()
    mock_call.assert_called_once_with("GET", "/api/config")


def test_set_idf_config_filters_none_values(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["set_idf_config"](project_dir="D:/proj", board="b1")
    mock_call.assert_called_once_with(
        "POST", "/api/config",
        json={"IDF_PROJECT_DIR": "D:/proj", "IDF_BOARD": "b1"},
    )


def test_set_idf_config_returns_error_when_empty(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    result = fake_mcp.tools["set_idf_config"]()
    assert result["ok"] is False
    assert "未提供任何配置字段" in result["error"]
    mock_call.assert_not_called()


def test_list_idf_versions(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["list_idf_versions"]()
    mock_call.assert_called_once_with("GET", "/api/idf-versions")


def test_list_idf_projects(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["list_idf_projects"]()
    mock_call.assert_called_once_with("GET", "/api/idf-projects")


def test_list_boards(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["list_boards"]()
    mock_call.assert_called_once_with("GET", "/api/boards")


def test_select_board(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["select_board"]("lckfb_szpi_esp32s3")
    mock_call.assert_called_once_with(
        "POST", "/api/boards/select", json={"board": "lckfb_szpi_esp32s3"}
    )


def test_build_with_board(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["build"](board="esp32s3")
    mock_call.assert_called_once_with(
        "POST", "/api/build", json={"board": "esp32s3"}, timeout=600.0
    )


def test_build_without_board(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["build"]()
    mock_call.assert_called_once_with(
        "POST", "/api/build", json={}, timeout=600.0
    )


def test_flash_with_port_and_board(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["flash"](port="COM6", board="b1")
    mock_call.assert_called_once_with(
        "POST", "/api/flash", json={"wait": False, "port": "COM6", "board": "b1"}, timeout=30.0
    )


def test_flash_minimal(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["flash"]()
    mock_call.assert_called_once_with(
        "POST", "/api/flash", json={"wait": False}, timeout=30.0
    )


def test_flash_wait_true(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["flash"](wait=True)
    mock_call.assert_called_once_with(
        "POST", "/api/flash", json={"wait": True}, timeout=300.0
    )


def test_clean_build(fake_mcp, mock_call, mock_fmt):
    idf_tools.register(fake_mcp)
    fake_mcp.tools["clean_build"]()
    mock_call.assert_called_once_with("POST", "/api/clean", timeout=300.0)


def test_get_flash_progress(fake_mcp, mock_call, mock_fmt):
    mock_call.return_value = {"active": True, "percent": 45, "phase": "flashing"}
    idf_tools.register(fake_mcp)
    result = fake_mcp.tools["get_flash_progress"]()
    mock_call.assert_called_once_with("GET", "/api/flash/progress")
    assert result["active"] is True
    assert result["percent"] == 45
