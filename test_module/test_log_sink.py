"""log_sink 单元测试：用真实临时目录验证文件落盘行为。

不 mock LogSink 本身（被测对象），用 tempfile.mkdtemp() 自建临时目录。
（不用 pytest 的 tmp_path fixture —— 在本机 Windows 环境下它每次约 55s，
疑似 Defender 实时扫描 AppData/Local/Temp 所致；tempfile 直接在工作目录
所在盘创建，不受影响。）
"""

import os
import shutil
import tempfile
import threading

import pytest

from log_sink import LogSink


@pytest.fixture
def sink_dir():
    """每个测试一个独立临时目录，测试结束自动清理。"""
    d = tempfile.mkdtemp(prefix="logsink_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_disabled_sink_write_is_noop(sink_dir):
    sink = LogSink(sink_dir=sink_dir, enabled=False)
    assert sink.open_session() is None
    sink.write("hello")  # 不应抛异常
    assert sink.current_path is None


def test_open_session_creates_file_with_meta(sink_dir):
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    path = sink.open_session(meta={"串口": "COM6", "波特率": 115200})
    assert path is not None
    assert os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "Serial Bridge 会话日志" in content
    assert "COM6" in content
    assert "115200" in content
    sink.close()


def test_write_appends_timestamped_lines(sink_dir):
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    sink.open_session()
    sink.write("line1")
    sink.write("line2")
    sink.close()
    files = os.listdir(sink_dir)
    assert len(files) == 1
    content = open(os.path.join(sink_dir, files[0]), encoding="utf-8").read()
    assert "line1" in content
    assert "line2" in content
    # 每行有时间戳前缀
    assert content.count("[") >= 2


def test_write_raw_no_timestamp(sink_dir):
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    sink.open_session()
    sink.write_raw("raw block\n")
    sink.close()
    content = open(sink.current_path, encoding="utf-8").read()
    assert "raw block" in content


def test_rotate_creates_new_file(sink_dir):
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    p1 = sink.open_session()
    sink.write("first")
    p2 = sink.rotate(meta={"轮转": True})
    sink.write("second")
    sink.close()
    assert p1 != p2
    assert os.path.exists(p1)
    assert os.path.exists(p2)
    assert "first" in open(p1, encoding="utf-8").read()
    assert "second" in open(p2, encoding="utf-8").read()
    assert "轮转: True" in open(p2, encoding="utf-8").read()


def test_list_sessions_returns_sorted(sink_dir):
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    sink.open_session()
    sink.write("a")
    sink.rotate()
    sink.write("b")
    sink.close()
    sessions = sink.list_sessions()
    assert len(sessions) == 2
    # 倒序：最新的在前
    assert os.path.getmtime(sessions[0]) >= os.path.getmtime(sessions[1])


def test_write_exception_swallowed(sink_dir):
    """写入时文件句柄被外部关闭，write 不应抛异常。"""
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    sink.open_session()
    sink._fp.close()  # 模拟异常
    sink.write("should not raise")  # 必须不抛
    sink.close()


def test_thread_safety_concurrent_writes(sink_dir):
    """多线程并发 write 不丢行不崩溃。"""
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    sink.open_session()
    N_THREADS, N_LINES = 8, 200

    def worker(tid):
        for i in range(N_LINES):
            sink.write(f"t{tid}-l{i}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sink.close()
    content = open(sink.current_path, encoding="utf-8").read()
    # 每个线程写了 N_LINES 行，首尾行都应在
    for tid in range(N_THREADS):
        assert content.count(f"t{tid}-l0") == 1
        assert content.count(f"t{tid}-l{N_LINES - 1}") == 1


def test_enabled_setter_closes_file(sink_dir):
    sink = LogSink(sink_dir=sink_dir, enabled=True)
    sink.open_session()
    path = sink.current_path
    assert path is not None
    sink.enabled = False
    assert sink.enabled is False
    sink.write("ignored")  # 禁用后写入无效
    content = open(path, encoding="utf-8").read()
    assert "ignored" not in content


def test_invalid_dir_disables_sink(sink_dir):
    """目录不可创建时自动禁用，不抛异常。"""
    # 用一个文件路径作为目录，makedirs 必失败
    bad_dir = os.path.join(sink_dir, "afile")
    open(bad_dir, "w").close()  # 这是一个文件，不是目录
    sink = LogSink(sink_dir=bad_dir, enabled=True)
    # 构造时不抛，open_session 时降级
    assert sink.open_session() is None or sink.enabled is False
