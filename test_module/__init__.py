r"""test_module —— tool_module 的单元测试集合。

测试策略
--------
1. 不依赖真实 serial_bridge 服务，全部离线可运行。
2. 用 FakeMCP 替代 FastMCP：记录 @mcp.tool() / @mcp.resource() 装饰的函数，
   测试可直接按名字调用，验证其内部对 bridge_client.call 的调用参数是否正确。
3. mock bridge_client.call 返回预设值；mock bridge_client.fmt 透传原值，
   便于断言工具返回的结构。
4. bridge_client 自身（fmt / rebuild_client / call 错误分支）用真实代码 + 异常注入测试。

运行测试
--------
    cd D:\code\espclaw\espbridgetool
    .venv\Scripts\python.exe -m pytest test_module/ -v

新增功能时的测试约定
--------------------
在 tool_module/ 下新增工具后，在 test_module/ 下对应文件里补测试：
  - 新增串口工具 → test_serial_tools.py
  - 新增日志工具 → test_log_tools.py
  - 新增 IDF 工具 → test_idf_tools.py
  - 新增 resource → test_resources.py
  - 新增独立模块 → 新建 test_<模块名>.py
每个新工具至少 1 个用例：验证调用了正确的 HTTP method/path/参数。
"""
