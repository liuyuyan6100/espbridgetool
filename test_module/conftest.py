"""共享 fixtures：FakeMCP、mock_call、mock_fmt。

FakeMCP 模拟 mcp.server.fastmcp.FastMCP 的 tool()/resource() 装饰器，
把被装饰的函数按名字/URI 记录到字典里，测试可直接调用，不依赖 FastMCP 内部实现。
"""

import pytest


class FakeMCP:
    """记录 tool/resource 装饰的函数，便于测试直接调用。

    - tools: {工具名: 函数}
    - resources: {uri: 函数}
    调用方式：fake_mcp.tools["get_status"]()
    """

    def __init__(self):
        self.tools = {}
        self.resources = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco

    def resource(self, uri):
        def deco(fn):
            self.resources[uri] = fn
            return fn
        return deco


@pytest.fixture
def fake_mcp():
    """一个干净的 FakeMCP 实例，每个测试独立。"""
    return FakeMCP()


@pytest.fixture
def mock_call(monkeypatch):
    """mock tool_module.bridge_client.call。

    默认返回 {"ok": True}；测试可在用例里设 .return_value 或 .side_effect。
    通过 .call_args / .call_args_list 断言工具传给 HTTP 层的参数。
    """
    import tool_module.bridge_client as bc
    from unittest.mock import MagicMock
    m = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(bc, "call", m)
    return m


@pytest.fixture
def mock_fmt(monkeypatch):
    """mock tool_module.bridge_client.fmt，透传原值（不做 JSON 序列化）。

    这样测试可直接断言工具返回的结构，而不必解析 JSON 字符串。
    """
    import tool_module.bridge_client as bc
    from unittest.mock import MagicMock
    m = MagicMock(side_effect=lambda x: x)
    monkeypatch.setattr(bc, "fmt", m)
    return m
