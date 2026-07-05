"""Serial Bridge HTTP 客户端封装。

集中管理对 serial_bridge.py Web 服务的 HTTP 调用，供 tool_module 内各工具模块使用。
所有工具不直接持有 httpx 客户端，统一通过本模块的 call() / fmt() 完成请求与格式化。

模块级单例 _client 在 import 时按 .env/环境变量创建，主文件 main() 里再用
rebuild_client() 按命令行参数重建一次。
"""

import json
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

# ---- 配置加载 ----
# .env 与本包同级的上一级目录（即 espbridgetool 根目录）
_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)

BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8080"))
BRIDGE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

# httpx 客户端单例（复用连接池）
_client: Optional[httpx.Client] = httpx.Client(base_url=BRIDGE_URL, timeout=30.0)


def rebuild_client(host: str, port: int) -> None:
    """根据命令行/最终配置重建 httpx 客户端。"""
    global _client, BRIDGE_HOST, BRIDGE_PORT, BRIDGE_URL
    BRIDGE_HOST, BRIDGE_PORT = host, port
    BRIDGE_URL = f"http://{host}:{port}"
    _client = httpx.Client(base_url=BRIDGE_URL, timeout=30.0)


def call(method: str, path: str, *, timeout: float = 30.0, **kwargs) -> dict:
    """统一 HTTP 调用封装。

    成功时返回 bridge 返回的 JSON；失败时返回
    {"ok": False, "error": "<可读的错误说明>"}，绝不抛异常给上层。
    """
    try:
        r = _client.request(method, path, timeout=timeout, **kwargs)
        try:
            return r.json()
        except Exception:
            return {
                "ok": False,
                "error": f"非 JSON 响应 (HTTP {r.status_code}): {r.text[:300]}",
            }
    except httpx.ConnectError:
        return {
            "ok": False,
            "error": (
                f"无法连接 Serial Bridge ({BRIDGE_URL})。"
                f"请先运行 serial_bridge.py（双击 start.bat）启动桥接服务。"
            ),
        }
    except httpx.TimeoutException:
        return {
            "ok": False,
            "error": f"请求超时（{timeout}s）。长耗时操作（build/flash）请确认是否正常执行。",
        }
    except Exception as e:
        return {"ok": False, "error": f"请求异常: {type(e).__name__}: {e}"}


def fmt(data) -> str:
    """把任意结果格式化为 MCP 工具返回文本（JSON）。"""
    return json.dumps(data, indent=2, ensure_ascii=False)
