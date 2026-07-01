/**
 * Serial Bridge — 前端逻辑
 * 支持双模式：日志面板模式 / 终端模式 (xterm.js)
 */

// ---- 状态 ----
let ws = null;
let isTerminalMode = false;
let autoScroll = true;
let cmdHistory = [];
let cmdHistoryIndex = -1;
const MAX_LOG_LINES = 5000;
let logLines = [];

// ---- DOM 引用 ----
const $ = (id) => document.getElementById(id);
const logContent = $('logContent');
const filterInput = $('filterInput');
const logCount = $('logCount');
const portSelect = $('portSelect');
const baudSelect = $('baudSelect');
const cmdInput = $('cmdInput');

// ---- WebSocket 连接 ----
function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/log`;
    ws = new WebSocket(url);

    ws.onopen = () => {
        $('statusWs').textContent = '已连接';
        $('statusWs').style.color = '#4caf50';
    };

    ws.onmessage = (evt) => {
        const text = evt.data;
        if (isTerminalMode && term) {
            term.write(text);
        } else {
            appendLogLine(text);
        }
    };

    ws.onclose = () => {
        $('statusWs').textContent = '断开';
        $('statusWs').style.color = '#f44336';
        ws = null;
        // 自动重连
        setTimeout(connectWs, 3000);
    };

    ws.onerror = () => {
        $('statusWs').textContent = '错误';
        $('statusWs').style.color = '#f44336';
    };
}

// ---- 日志系统 ----
function classifyLine(text) {
    const clean = text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '');
    if (/^E\s*\(/.test(clean)) return 'error';
    if (/^W\s*\(/.test(clean)) return 'warning';
    if (/^I\s*\(/.test(clean)) return 'info';
    if (/heap/i.test(clean)) return 'memory';
    if (/wifi:/i.test(clean)) return 'wifi';
    if (/writing at 0x/i.test(clean)) return 'flash';
    return 'normal';
}

function stripAnsi(text) {
    return text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function appendLogLine(text) {
    logLines.push(text);
    if (logLines.length > MAX_LOG_LINES) {
        logLines = logLines.slice(-MAX_LOG_LINES);
    }
    renderLog();
}

function renderLog() {
    const keyword = filterInput.value.trim().toLowerCase();
    let filtered = logLines;
    if (keyword) {
        filtered = logLines.filter(line =>
            stripAnsi(line).toLowerCase().includes(keyword)
        );
    }

    // 只渲染可见行（虚拟滚动简化版）
    const visible = filtered.slice(-500);
    const cls = classifyLine;
    let html = '';
    for (const line of visible) {
        const clean = stripAnsi(line);
        const escaped = escapeHtml(clean);
        const lineClass = 'line line-' + cls(line);
        html += `<div class="${lineClass}">${escaped}</div>`;
    }
    logContent.innerHTML = html;
    logCount.textContent = `${filtered.length} 行`;

    if (autoScroll) {
        logContent.scrollTop = logContent.scrollHeight;
    }
}

function clearLog() {
    logLines = [];
    logContent.innerHTML = '';
    logCount.textContent = '0 行';
}

// ---- 过滤防抖 ----
let filterTimer = null;
filterInput.addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(renderLog, 200);
});

// ---- 自动滚动 ----
$('btnAutoScroll').addEventListener('click', () => {
    autoScroll = !autoScroll;
    $('btnAutoScroll').classList.toggle('active', autoScroll);
});

// ---- 清空日志 ----
$('btnClearLog').addEventListener('click', clearLog);

// ---- 串口操作 ----
async function refreshPorts() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        portSelect.innerHTML = '';
        if (data.available_ports && data.available_ports.length) {
            data.available_ports.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.device;
                opt.textContent = `${p.device} — ${p.description}`;
                portSelect.appendChild(opt);
            });
        } else {
            portSelect.innerHTML = '<option value="">无可用串口</option>';
        }
    } catch (e) {
        portSelect.innerHTML = '<option value="">无法获取</option>';
    }
}

async function openSerial() {
    const port = portSelect.value;
    const baud = parseInt(baudSelect.value);
    if (!port) return alert('请选择串口');
    try {
        const res = await fetch('/api/serial/open', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({port, baud}),
        });
        const data = await res.json();
        if (data.ok) {
            $('btnConnect').disabled = true;
            $('btnDisconnect').disabled = false;
            updateStatus();
        } else {
            alert('打开失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        alert('请求失败: ' + e.message);
    }
}

async function closeSerial() {
    try {
        await fetch('/api/serial/close', {method: 'POST'});
        $('btnConnect').disabled = false;
        $('btnDisconnect').disabled = true;
        updateStatus();
    } catch (e) {
        alert('关闭失败: ' + e.message);
    }
}

// ---- 发送命令 ----
async function sendCommand(cmd) {
    if (!cmd.trim()) return;
    try {
        await fetch('/api/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cmd: cmd + '\n'}),
        });
        // 记录历史
        cmdHistory.push(cmd);
        if (cmdHistory.length > 50) cmdHistory.shift();
        cmdHistoryIndex = cmdHistory.length;
    } catch (e) {
        console.error('发送失败:', e);
    }
}

cmdInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        sendCommand(cmdInput.value);
        cmdInput.value = '';
    } else if (e.key === 'ArrowUp') {
        if (cmdHistoryIndex > 0) {
            cmdHistoryIndex--;
            cmdInput.value = cmdHistory[cmdHistoryIndex] || '';
        }
        e.preventDefault();
    } else if (e.key === 'ArrowDown') {
        if (cmdHistoryIndex < cmdHistory.length - 1) {
            cmdHistoryIndex++;
            cmdInput.value = cmdHistory[cmdHistoryIndex] || '';
        } else {
            cmdHistoryIndex = cmdHistory.length;
            cmdInput.value = '';
        }
        e.preventDefault();
    }
});

$('btnSend').addEventListener('click', () => {
    sendCommand(cmdInput.value);
    cmdInput.value = '';
});

// ---- 快捷操作 ----
async function runAction(action, body = {}) {
    try {
        const res = await fetch('/api/' + action, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!data.ok && data.error) {
            appendLogLine(`[bridge] ${action} 失败: ${data.error}`);
        }
    } catch (e) {
        appendLogLine(`[bridge] ${action} 请求失败: ${e.message}`);
    }
}

$('btnBuild').addEventListener('click', () => runAction('build'));
$('btnFlash').addEventListener('click', () => runAction('flash'));
$('btnClean').addEventListener('click', () => runAction('clean'));
$('btnBmgr').addEventListener('click', () => runAction('bmgr'));

// ---- 状态更新 ----
async function updateStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        $('statusPort').textContent = data.port || '未连接';
        $('statusLogCount').textContent = data.log_lines || 0;
        $('statusSvc').textContent = '运行中';
        $('statusSvc').style.color = '#4caf50';
    } catch (e) {
        $('statusSvc').textContent = '无法连接';
        $('statusSvc').style.color = '#f44336';
    }
}

// ---- 切换模式 ----
let term = null;
let fitAddon = null;

$('btnMode').addEventListener('click', () => {
    isTerminalMode = !isTerminalMode;
    $('logPanel').style.display = isTerminalMode ? 'none' : 'flex';
    $('terminalPanel').style.display = isTerminalMode ? 'block' : 'none';
    $('btnMode').textContent = isTerminalMode ? '📋 日志模式' : '📟 终端模式';

    if (isTerminalMode) {
        initTerminal();
    }
});

function initTerminal() {
    if (term) return;
    if (typeof Terminal === 'undefined') {
        $('xterm-container').innerHTML = '<p style="padding:20px;color:red;">xterm.js 未能加载，请检查网络</p>';
        return;
    }

    term = new Terminal({
        cursorBlink: true,
        cursorStyle: 'block',
        fontSize: 14,
        fontFamily: 'Consolas, "Courier New", monospace',
        theme: {
            background: '#1e1e1e',
            foreground: '#d4d4d4',
        },
    });

    fitAddon = new FitAddon();
    term.loadAddon(fitAddon);

    term.open($('xterm-container'));
    fitAddon.fit();

    term.onData((data) => {
        // 通过 API 发送到串口
        fetch('/api/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({cmd: data}),
        }).catch(e => console.error('终端发送失败:', e));
    });

    term.write('Serial Bridge — 终端模式\r\n');
    term.write('输入命令将直接发送到串口\r\n\r\n');

    // 回放已有日志
    for (const line of logLines.slice(-200)) {
        term.write(line);
    }
}

// ---- 窗口事件 ----
window.addEventListener('resize', () => {
    if (term && fitAddon) {
        try { fitAddon.fit(); } catch (e) {}
    }
});

// ---- 初始化 ----
$('btnConnect').addEventListener('click', openSerial);
$('btnDisconnect').addEventListener('click', closeSerial);

// 定时更新状态
setInterval(updateStatus, 5000);
updateStatus();
refreshPorts();

// 连接 WebSocket
connectWs();

console.log('Serial Bridge 前端已加载');