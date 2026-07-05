"""register_all 完整性测试：验证一次性注册全部工具与 resources。

这是回归测试：以后新增工具若忘记在 register_all 里串接，这里会失败。
同时与 mcp_server 顶层的真实 FastMCP 注册结果交叉验证，确保数量一致。
"""

from tool_module import register_all
import mcp_server


EXPECTED_TOOLS = {
    # serial
    "list_serial_ports", "get_status", "open_serial",
    "close_serial", "send_command", "send_and_collect",
    # log
    "get_logs", "get_logs_since", "get_last_seq", "clear_logs",
    "dump_logs", "list_log_sessions", "rotate_log_session",
    # idf
    "get_idf_config", "set_idf_config", "list_idf_versions",
    "list_idf_projects", "list_boards", "select_board",
    "build", "flash", "clean_build",
}

EXPECTED_RESOURCES = {
    "esp32://status", "esp32://logs/recent", "esp32://config",
}


def test_register_all_registers_all_tools(fake_mcp):
    register_all(fake_mcp)
    assert set(fake_mcp.tools.keys()) == EXPECTED_TOOLS


def test_register_all_registers_all_resources(fake_mcp):
    register_all(fake_mcp)
    assert set(fake_mcp.resources.keys()) == EXPECTED_RESOURCES


def test_register_all_tool_count(fake_mcp):
    register_all(fake_mcp)
    assert len(fake_mcp.tools) == 22


def test_register_all_resource_count(fake_mcp):
    register_all(fake_mcp)
    assert len(fake_mcp.resources) == 3


def test_real_mcp_server_registers_same_tools():
    """与 mcp_server 顶层真实 FastMCP 实例交叉验证。

    mcp_server 在 import 时已创建 mcp 并调用 register_all(mcp)，
    通过其内部工具管理器确认注册的工具集合与预期一致。
    若未来升级 mcp SDK 改了内部结构，本测试需相应调整。
    """
    try:
        tools = mcp_server.mcp._tool_manager._tools
    except AttributeError:
        # SDK 结构变更时的兜底：跳过而非误报
        import pytest
        pytest.skip("FastMCP 内部结构已变更，需更新本测试")
    real_names = set(tools.keys())
    assert real_names == EXPECTED_TOOLS
