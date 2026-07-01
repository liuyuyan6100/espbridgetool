"""日志环形缓冲 — 保存最近 N 行日志，支持过滤和回看"""

import re
from collections import deque
from typing import List, Optional


class LogBuffer:
    """环形缓冲区，保存最近 N 行日志"""

    # ANSI 转义序列模式（用于日志渲染时的颜色还原）
    ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

    def __init__(self, max_lines: int = 10000):
        self._max_lines = max_lines
        self._buffer: deque[str] = deque(maxlen=max_lines)
        # 原始数据（用于 WebSocket 恢复，含不可见字符）
        self._raw_buffer: deque[bytes] = deque(maxlen=max_lines)

    def append(self, line: str, raw: Optional[bytes] = None) -> None:
        """追加一行日志"""
        self._buffer.append(line)
        if raw:
            self._raw_buffer.append(raw)

    def get_history(self, last_n: int = 100) -> List[str]:
        """获取最近 N 行历史日志"""
        if last_n <= 0:
            last_n = self._max_lines
        return list(self._buffer)[-last_n:]

    def get_filtered(self, keyword: str, last_n: int = 500) -> List[str]:
        """关键字过滤（大小写不敏感）"""
        if not keyword:
            return self.get_history(last_n)
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        return [line for line in list(self._buffer)[-last_n:] if pattern.search(line)]

    def clear(self) -> None:
        """清空缓冲"""
        self._buffer.clear()
        self._raw_buffer.clear()

    @property
    def count(self) -> int:
        return len(self._buffer)

    @property
    def max_lines(self) -> int:
        return self._max_lines

    # ---- 日志分析增强 ----

    @staticmethod
    def classify_line(line: str) -> str:
        """根据日志内容分类"""
        ansi_free = LogBuffer.ANSI_PATTERN.sub("", line)
        if re.match(r"^E\s*\(", ansi_free):
            return "error"
        if re.match(r"^W\s*\(", ansi_free):
            return "warning"
        if re.match(r"^I\s*\(", ansi_free):
            return "info"
        if "heap" in ansi_free.lower():
            return "memory"
        if re.search(r"wifi:", ansi_free, re.IGNORECASE):
            return "wifi"
        if "writing at 0x" in ansi_free.lower():
            return "flash_progress"
        return "normal"