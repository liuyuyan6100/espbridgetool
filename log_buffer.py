"""日志环形缓冲 — 保存最近 N 行日志，支持过滤和回看

支持基于序列号的增量获取（get_after_seq），用于 MCP agent
"发命令后等待并收集新日志"的场景，避免重复读取历史。
"""

import re
from collections import deque
from typing import List, Optional, Tuple


class LogBuffer:
    """环形缓冲区，保存最近 N 行日志"""

    # ANSI 转义序列模式（用于日志渲染时的颜色还原）
    ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

    def __init__(self, max_lines: int = 10000):
        self._max_lines = max_lines
        self._buffer: deque[str] = deque(maxlen=max_lines)
        # 原始数据（用于 WebSocket 恢复，含不可见字符）
        self._raw_buffer: deque[bytes] = deque(maxlen=max_lines)
        # 全局递增序列号：每 append 一行 +1，用于增量获取
        # 注意：deque 是环形的，满了会丢最早的行，但序列号始终递增
        self._seq: int = 0
        self._line_seqs: deque[int] = deque(maxlen=max_lines)

    def append(self, line: str, raw: Optional[bytes] = None) -> None:
        """追加一行日志"""
        self._seq += 1
        self._buffer.append(line)
        self._line_seqs.append(self._seq)
        if raw:
            self._raw_buffer.append(raw)

    def get_after_seq(self, seq: int) -> Tuple[List[str], int]:
        """获取序列号严格大于 seq 的所有日志行

        用于增量获取场景（如 MCP agent 发命令后收集响应）。
        即使环形缓冲已满、早期行被丢弃，也只返回 seq 之后的新行。

        Returns:
            (lines, latest_seq): 新行列表 + 当前最新序列号
        """
        result: List[str] = []
        latest = seq
        for line, s in zip(self._buffer, self._line_seqs):
            if s > seq:
                result.append(line)
                if s > latest:
                    latest = s
        return result, latest

    @property
    def last_seq(self) -> int:
        """当前最新序列号（初始为 0，表示无日志）"""
        return self._seq

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