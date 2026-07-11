/**
 * Serial Bridge — 前端逻辑
 * 现代化串口调试界面，支持 HEX/文本/彩色模式、快捷命令、统计
 */

// ============ 状态 ============
let ws = null;
let logMode = 'text';        // text | hex | ansi
let autoScroll = true;
let showTimestamp = false;
let cmdHistory = [];
let cmdHistoryIndex = -1;
let loopTimer = null;
let stats = { tx_bytes: 0, rx_bytes: 0 };

const MAX_LOG_LINES = 5000;
let logLines = [];

// ============ DOM ============
const $ = (id) => document.getElementById(id);
const logView = $('logView');

// ============ WebSocket ============
function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/log`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => { /* 无需额外操作 */ };

    ws.onmessage = (evt) => {
        // 尝试解析 JSON（烧录/编译进度事件），失败则当作普通日志
        if (evt.data.startsWith('{')) {
            try {
                const msg = JSON.parse(evt.data);
                if (msg.type === 'flash_progress' && msg.progress) {
                    updateFlashProgress(msg.progress);
                    return;
                }
                if (msg.type === 'build_progress' && msg.progress) {
                    updateBuildProgress(msg.progress);
                    return;
                }
            } catch (e) { /* 非 JSON，走普通日志 */ }
        }
        appendLog(evt.data);
    };

    ws.onclose = () => {
        ws = null;
        setTimeout(connectWs, 3000);
    };

    ws.onerror = () => {};
}

// ============ 日志处理 ============
const ANSI_RE = /\x1b\[[0-9;]*m/g;
const ANSI_ALL_RE = /\x1b\[[0-9;]*[a-zA-Z]/g;

/** ANSI 颜色码 → HTML span（简化版） */
function ansiToHtml(text) {
    const colors = {
        '0': 'inherit', '30': '#000', '31': '#e53935', '32': '#43a047',
        '33': '#ffb300', '34': '#1e88e5', '35': '#8e24aa', '36': '#00acc1',
        '37': '#bdbdbd', '90': '#616161', '91': '#ef5350', '92': '#66bb6a',
        '93': '#ffee58', '94': '#42a5f5', '95': '#ab47bc', '96': '#26c6da',
        '97': '#e0e0e0',
    };
    let html = '';
    let current = '';
    let i = 0;
    while (i < text.length) {
        if (text[i] === '\x1b') {
            const m = text.slice(i).match(/^\x1b\[([0-9;]*)m/);
            if (m) {
                if (current) {
                    const color = colors[current] || 'inherit';
                    html += `<span style="color:${color}">${escapeHtml(current)}</span>`;
                    current = '';
                }
                i += m[0].length;
                continue;
            }
        }
        current += text[i];
        i++;
    }
    if (current) html += escapeHtml(current);
    return html;
}

function stripAnsi(text) {
    return text.replace(ANSI_ALL_RE, '');
}

function toHexView(text) {
    // 将文本转为十六进制表示
    const bytes = new TextEncoder().encode(text);
    let hex = '';
    let ascii = '';
    let result = '';
    for (let i = 0; i < bytes.length; i++) {
        hex += bytes[i].toString(16).padStart(2, '0').toUpperCase() + ' ';
        ascii += (bytes[i] >= 32 && bytes[i] < 127) ? String.fromCharCode(bytes[i]) : '.';
        if ((i + 1) % 16 === 0 || i === bytes.length - 1) {
            const pad = '   '.repeat(16 - (i % 16) - 1);
            result += hex.padEnd(48) + '  ' + ascii + '\n';
            hex = '';
            ascii = '';
        }
    }
    return result;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function classifyLine(text) {
    const clean = stripAnsi(text);
    if (/^E\s*\(/.test(clean)) return 'error';
    if (/^W\s*\(/.test(clean)) return 'warning';
    if (/^I\s*\(/.test(clean)) return 'info';
    if (/heap/i.test(clean)) return 'memory';
    if (/wifi:/i.test(clean)) return 'wifi';
    if (/writing at 0x/i.test(clean)) return 'flash';
    if (/^\[idf\.py\]/.test(clean)) return 'idf';
    return 'normal';
}

function formatTimestamp() {
    const d = new Date();
    return `[${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}.${String(d.getMilliseconds()).padStart(3,'0')}]`;
}

function appendLog(text) {
    logLines.push({ text, ts: formatTimestamp() });
    if (logLines.length > MAX_LOG_LINES) {
        logLines.shift();
    }
    renderAppend(text);
}

/** 增量追加单行（性能优化） */
function renderAppend(text) {
    const keyword = $('filterInput').value.trim().toLowerCase();
    if (keyword && !stripAnsi(text).toLowerCase().includes(keyword)) return;

    const lineDiv = document.createElement('div');
    const cls = classifyLine(text);
    lineDiv.className = `line line-${cls}`;

    let content = '';
    if (showTimestamp) {
        content += `<span class="ts">${formatTimestamp()}</span>`;
    }

    if (logMode === 'hex') {
        content += escapeHtml(toHexView(text));
    } else if (logMode === 'ansi') {
        content += ansiToHtml(text);
    } else {
        content += escapeHtml(stripAnsi(text));
    }

    lineDiv.innerHTML = content;
    logView.appendChild(lineDiv);

    // 限制 DOM 节点数
    while (logView.children.length > MAX_LOG_LINES) {
        logView.removeChild(logView.firstChild);
    }

    if (autoScroll) {
        logView.scrollTop = logView.scrollHeight;
    }
}

/** 全量重新渲染（切换模式时调用） */
function renderAll() {
    logView.innerHTML = '';
    const keyword = $('filterInput').value.trim().toLowerCase();
    for (const item of logLines) {
        if (keyword && !stripAnsi(item.text).toLowerCase().includes(keyword)) continue;
        const lineDiv = document.createElement('div');
        const cls = classifyLine(item.text);
        lineDiv.className = `line line-${cls}`;

        let content = '';
        if (showTimestamp) content += `<span class="ts">${item.ts}</span>`;
        if (logMode === 'hex') {
            content += escapeHtml(toHexView(item.text));
        } else if (logMode === 'ansi') {
            content += ansiToHtml(item.text);
        } else {
            content += escapeHtml(stripAnsi(item.text));
        }
        lineDiv.innerHTML = content;
        logView.appendChild(lineDiv);
    }
    if (autoScroll) logView.scrollTop = logView.scrollHeight;
}

function clearLog() {
    logLines = [];
    logView.innerHTML = '';
    fetch('/api/log/clear', { method: 'POST' });
}

// ============ 过滤 ============
let filterTimer = null;
$('filterInput').addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(renderAll, 200);
});

// ============ 日志模式切换 ============
document.querySelectorAll('.seg-btn[data-view]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.seg-btn[data-view]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        logMode = btn.dataset.view;
        logView.classList.toggle('ansi-mode', logMode === 'ansi');
        renderAll();
    });
});

// ============ 选项 ============
$('autoScroll').addEventListener('change', (e) => {
    autoScroll = e.target.checked;
    if (autoScroll) logView.scrollTop = logView.scrollHeight;
});

$('showTs').addEventListener('change', (e) => {
    showTimestamp = e.target.checked;
    renderAll();
});

// ============ 日志导出/清空 ============
$('btnExport').addEventListener('click', () => {
    if (!logLines.length) {
        toast('日志为空，无需导出', 'warning');
        return;
    }
    const text = logLines.map(l => stripAnsi(l.text)).join('\n');
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    const now = new Date();
    const ts = `${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}`;
    a.download = `serial_log_${ts}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`已导出 ${logLines.length} 行日志`, 'success');
});

// 清空前二次确认
$('btnClearLog').addEventListener('click', () => {
    if (!logLines.length && (!term || term.buffer.length === 0)) {
        toast('没有可清空的内容', 'warning');
        return;
    }
    if (confirm(`确认清空 ${logLines.length} 条日志？此操作不可撤销。`)) {
        clearLog();
        // 同时清空 xterm 终端（终端串口 / 终端Shell）
        if (term) {
            term.clear();
        }
        toast('日志和终端已清空', 'success');
    }
});

// ============ 串口操作 ============
async function refreshPorts() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const sel = $('portSelect');
        const current = sel.value;
        sel.innerHTML = '<option value="">选择串口</option>';
        if (data.available_ports && data.available_ports.length) {
            data.available_ports.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.device;
                opt.textContent = `${p.device} — ${p.description}`;
                sel.appendChild(opt);
            });
        }
        if (current) sel.value = current;
        updateFlashPortHint();
    } catch (e) { /* 忽略 */ }
}

/** 获取当前选中的串口，用于烧录 */
function getSelectedPort() {
    return $('portSelect').value || '';
}

/** 更新烧录卡片里的端口提示 */
function updateFlashPortHint() {
    const port = getSelectedPort();
    const hint = $('flashPortHint');
    if (port) {
        hint.textContent = port;
        hint.classList.remove('no-port');
    } else {
        hint.textContent = '未选择（请在上方选择串口）';
        hint.classList.add('no-port');
    }
}

// 串口选择变化时更新提示
$('portSelect').addEventListener('change', updateFlashPortHint);

async function openSerial() {
    const port = $('portSelect').value;
    const baud = parseInt($('baudSelect').value);
    if (!port) { toast('请选择串口'); return; }
    try {
        const res = await fetch('/api/serial/open', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port, baud }),
        });
        const data = await res.json();
        if (data.ok) {
            updateConnUI(true);
        } else {
            toast('打开失败: ' + (data.error || '未知错误'), 'error');
        }
    } catch (e) {
        toast('请求失败: ' + e.message, 'error');
    }
}

async function closeSerial() {
    try {
        await fetch('/api/serial/close', { method: 'POST' });
        updateConnUI(false);
    } catch (e) {
        toast('关闭失败: ' + e.message, 'error');
    }
}

function updateConnUI(connected) {
    $('btnConnect').disabled = connected;
    $('btnDisconnect').disabled = !connected;
    $('connDot').classList.toggle('connected', connected);
    $('connText').textContent = connected ? `${$('portSelect').value} 已连接` : '未连接';
}

$('btnConnect').addEventListener('click', openSerial);
$('btnDisconnect').addEventListener('click', closeSerial);
$('btnRefreshPorts').addEventListener('click', refreshPorts);

// ============ 发送 ============
async function sendCommand(cmd) {
    if (!cmd) return;
    const hexMode = $('hexSend').checked;
    const addNl = $('addNl').checked;
    let payload = cmd;
    if (!hexMode && addNl) payload = cmd + '\n';

    try {
        const res = await fetch('/api/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cmd: payload, hex: hexMode }),
        });
        const data = await res.json();
        if (data.ok) {
            cmdHistory.push(cmd);
            if (cmdHistory.length > 50) cmdHistory.shift();
            cmdHistoryIndex = cmdHistory.length;
        } else if (data.error) {
            toast(data.error, 'error');
        }
    } catch (e) {
        toast('发送失败: ' + e.message, 'error');
    }
}

$('btnSend').addEventListener('click', () => {
    const cmd = $('sendInput').value;
    sendCommand(cmd);
    $('sendInput').value = '';
});

$('sendInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const cmd = $('sendInput').value;
        sendCommand(cmd);
        $('sendInput').value = '';
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (cmdHistoryIndex > 0) {
            cmdHistoryIndex--;
            $('sendInput').value = cmdHistory[cmdHistoryIndex] || '';
        }
    } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (cmdHistoryIndex < cmdHistory.length - 1) {
            cmdHistoryIndex++;
            $('sendInput').value = cmdHistory[cmdHistoryIndex] || '';
        } else {
            cmdHistoryIndex = cmdHistory.length;
            $('sendInput').value = '';
        }
    }
});

// 循环发送
$('loopSend').addEventListener('change', (e) => {
    if (e.target.checked) {
        const interval = parseInt($('loopInterval').value) || 1000;
        loopTimer = setInterval(() => {
            const cmd = $('sendInput').value;
            if (cmd) sendCommand(cmd);
        }, interval);
    } else {
        clearInterval(loopTimer);
        loopTimer = null;
    }
});

$('loopInterval').addEventListener('change', () => {
    if (loopTimer) {
        clearInterval(loopTimer);
        const interval = parseInt($('loopInterval').value) || 1000;
        loopTimer = setInterval(() => {
            const cmd = $('sendInput').value;
            if (cmd) sendCommand(cmd);
        }, interval);
    }
});

// ============ 统计 ============
function formatBytes(n) {
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
}

async function updateStats() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (data.stats) {
            stats = data.stats;
            $('statTx').textContent = formatBytes(stats.tx_bytes || 0);
            $('statRx').textContent = formatBytes(stats.rx_bytes || 0);
        }
        $('statLogs').textContent = data.log_lines || 0;

        // 更新连接状态
        if (data.port && data.status === 'connected') {
            updateConnUI(true);
        } else {
            updateConnUI(false);
        }
    } catch (e) { /* 忽略 */ }
}

$('btnResetStats').addEventListener('click', async () => {
    await fetch('/api/stats/reset', { method: 'POST' });
    stats = { tx_bytes: 0, rx_bytes: 0 };
    $('statTx').textContent = '0 B';
    $('statRx').textContent = '0 B';
});

// ============ 快捷命令 ============
async function loadQuickCommands() {
    try {
        const res = await fetch('/api/quick-commands');
        const data = await res.json();
        renderQuickCommands(data.commands || []);
    } catch (e) { /* 忽略 */ }
}

function renderQuickCommands(commands) {
    const list = $('quickCmdList');
    if (!commands.length) {
        list.innerHTML = '<div class="quick-cmd-empty">暂无快捷命令<br>点击 + 添加</div>';
        return;
    }
    list.innerHTML = '';
    commands.forEach((cmd, i) => {
        const item = document.createElement('div');
        item.className = 'quick-cmd-item';
        item.innerHTML = `
            <span class="quick-cmd-name">${escapeHtml(cmd.name)}</span>
            ${cmd.hex ? '<span class="quick-cmd-badge hex">HEX</span>' : '<span class="quick-cmd-badge">TXT</span>'}
            <button class="quick-cmd-del" data-index="${i}" title="删除">×</button>
        `;
        item.addEventListener('click', (e) => {
            if (e.target.classList.contains('quick-cmd-del')) return;
            sendCommand(cmd.cmd);
        });
        list.appendChild(item);
    });
    // 绑定删除
    list.querySelectorAll('.quick-cmd-del').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const idx = parseInt(btn.dataset.index);
            await fetch(`/api/quick-commands/${idx}`, { method: 'DELETE' });
            loadQuickCommands();
        });
    });
}

$('btnAddCmd').addEventListener('click', () => {
    $('modalAddCmd').style.display = 'flex';
    $('cmdName').value = '';
    $('cmdContent').value = '';
    $('cmdHex').checked = false;
    $('cmdName').focus();
});

$('closeModal').addEventListener('click', () => $('modalAddCmd').style.display = 'none');
$('cancelCmd').addEventListener('click', () => $('modalAddCmd').style.display = 'none');

$('saveCmd').addEventListener('click', async () => {
    const name = $('cmdName').value.trim();
    const cmd = $('cmdContent').value.trim();
    const hex = $('cmdHex').checked;
    if (!name || !cmd) { toast('请填写名称和命令'); return; }
    await fetch('/api/quick-commands', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, cmd, hex }),
    });
    $('modalAddCmd').style.display = 'none';
    loadQuickCommands();
});

// ============ 卡片折叠 ============
document.querySelectorAll('.card-header').forEach(header => {
    header.addEventListener('click', (e) => {
        if (e.target.classList.contains('btn-toggle') || e.target === header) {
            const body = header.nextElementSibling;
            const toggle = header.querySelector('.btn-toggle');
            if (body) {
                body.classList.toggle('collapsed');
                toggle.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
            }
        }
    });
});

// ============ Toast 通知 ============
function toast(msg, type = 'info') {
    const colors = { info: '#29b6f6', success: '#66bb6a', error: '#ef5350', warn: '#ffa726' };
    const el = document.createElement('div');
    el.style.cssText = `
        position: fixed; bottom: 60px; left: 50%; transform: translateX(-50%);
        background: ${colors[type]}; color: #fff; padding: 8px 20px;
        border-radius: 4px; font-size: 13px; z-index: 2000;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3); transition: opacity 0.3s;
    `;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; }, 2500);
    setTimeout(() => el.remove(), 3000);
}

// ============ 终端模式（xterm.js） ============
let term = null;
let termFit = null;
let termWs = null;
let currentMode = 'log';  // log | serial | shell

function initTerminal() {
    if (term) return;
    term = new Terminal({
        fontSize: 13,
        fontFamily: 'Consolas, "Courier New", monospace',
        cursorBlink: true,
        convertEol: false,
        scrollback: 5000,
    });
    termFit = new FitAddon.FitAddon();
    term.loadAddon(termFit);
    term.open($('terminalView'));
    termFit.fit();

    term.onData((data) => {
        if (termWs && termWs.readyState === WebSocket.OPEN) {
            termWs.send(data);
        }
    });

    // 终端大小变化时通知后端调整 PTY 尺寸
    term.onResize((size) => {
        if (termWs && termWs.readyState === WebSocket.OPEN) {
            termWs.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
        }
    });

    window.addEventListener('resize', () => {
        if (currentMode !== 'log' && termFit) termFit.fit();
    });
}

function connectTerminal(mode) {
    // 断开旧连接
    if (termWs) {
        termWs.close();
        termWs = null;
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/terminal?mode=${mode}`;
    termWs = new WebSocket(url);
    termWs.binaryType = 'arraybuffer';

    $('termStatus').textContent = '连接中...';

    termWs.onopen = () => {
        $('termStatus').textContent = mode === 'serial' ? '串口已连接' : 'Shell 已连接';
        if (term) {
            term.focus();
            // 连接后立即同步 xterm 尺寸给后端 PTY，避免光标错位
            if (termFit) termFit.fit();
            const cols = term.cols;
            const rows = term.rows;
            if (cols && rows) {
                termWs.send(JSON.stringify({ type: "resize", cols: cols, rows: rows }));
            }
        }
    };

    termWs.onmessage = (evt) => {
        if (!term) return;
        if (evt.data instanceof ArrayBuffer) {
            term.write(new Uint8Array(evt.data));
        } else if (evt.data instanceof Blob) {
            evt.data.arrayBuffer().then(buf => term.write(new Uint8Array(buf)));
        } else {
            term.write(evt.data);
        }
    };

    termWs.onclose = () => {
        $('termStatus').textContent = '已断开';
        if (term) term.write('\r\n\x1b[31m[bridge] 终端已断开\x1b[0m\r\n');
    };

    termWs.onerror = () => {
        $('termStatus').textContent = '连接错误';
    };
}

// 视图模式切换
document.querySelectorAll('#viewModeGroup .seg-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const mode = btn.dataset.mode;
        if (mode === currentMode) return;

        document.querySelectorAll('#viewModeGroup .seg-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        currentMode = mode;

        if (mode === 'log') {
            // 日志模式
            $('logView').style.display = '';
            $('sendArea').style.display = '';
            $('terminalView').style.display = 'none';
            $('logControls').style.display = 'flex';
            $('termControls').style.display = 'none';
            // 断开终端 WS
            if (termWs) { termWs.close(); termWs = null; }
        } else {
            // 终端模式
            $('logView').style.display = 'none';
            $('sendArea').style.display = 'none';
            $('terminalView').style.display = '';
            $('logControls').style.display = 'none';
            $('termControls').style.display = 'inline-flex';

            initTerminal();
            // 切换模式时清空终端，避免串口/Shell 输出混杂
            if (term) term.clear();
            setTimeout(() => {
                termFit.fit();
                // fit 后再连接，确保 PTY 尺寸正确
                connectTerminal(mode);
            }, 50);
        }
    });
});

// ============ 编译 & 烧录 ============

const buildEls = {
    btnBuild: document.getElementById('btnBuild'),
    btnFlash: document.getElementById('btnFlash'),
    btnClean: document.getElementById('btnCleanBuild'),
    btnErase: document.getElementById('btnEraseFlash'),
    status: document.getElementById('buildStatus'),
    phase: document.getElementById('buildPhase'),
    msg: document.getElementById('buildMsg'),
};

let buildInProgress = false;

function setBuildStatus(phase, msg, show = true) {
    // phase 可能是英文（building/cleaning/flashing/done/error），转成中文
    const labelMap = {
        building: '编译中', cleaning: '清理中', flashing: '烧录中',
        erasing: '擦除中',
        done: '完成', error: '失败', connecting: '连接中', resetting: '重启中',
    };
    const label = labelMap[phase] || phase || '';
    buildEls.status.style.display = show ? 'flex' : 'none';
    buildEls.phase.textContent = label;
    buildEls.phase.className = 'build-status-phase phase-' + (phase || '').toLowerCase();
    buildEls.msg.textContent = msg || '';
}

/** 处理后端推送的编译/清理进度 */
function updateBuildProgress(prog) {
    if (!prog) return;
    const phaseMap = {
        building: '编译中', cleaning: '清理中', flashing: '烧录中',
        resetting: '重启中', done: '完成', error: '失败', connecting: '连接中',
    };
    const phaseLabel = phaseMap[prog.phase] || prog.phase || '';
    const show = prog.active || prog.phase === 'done' || prog.phase === 'error';
    if (show) {
        setBuildStatus(phaseLabel, prog.message || '', true);
        // 同步更新主进度条（编译/清理时也显示进度条）
        if (typeof updateFlashProgress === 'function') {
            updateFlashProgress(prog);
        }
    }
    // 完成后恢复按钮
    if (!prog.active) {
        setBuildButtonsDisabled(false);
        buildInProgress = false;
        setTimeout(() => { buildEls.status.style.display = 'none'; }, 5000);
    }
}

function setBuildButtonsDisabled(disabled) {
    buildEls.btnBuild.disabled = disabled;
    buildEls.btnFlash.disabled = disabled;
    buildEls.btnClean.disabled = disabled;
    buildEls.btnErase.disabled = disabled;
}

/** 编译 */
async function doBuild() {
    if (buildInProgress) return;
    buildInProgress = true;
    setBuildButtonsDisabled(true);
    setBuildStatus('building', '编译中...');
    try {
        const res = await fetch('/api/build', { method: 'POST' });
        const data = await res.json();
        if (data.ok) {
            setBuildStatus('done', '编译成功');
            toast('编译成功', 'success');
        } else {
            setBuildStatus('error', '编译失败: ' + (data.error || '').slice(0, 80));
            toast('编译失败', 'error');
        }
    } catch (e) {
        setBuildStatus('error', '请求失败: ' + e.message);
        toast('编译请求失败: ' + e.message, 'error');
    } finally {
        setBuildButtonsDisabled(false);
        buildInProgress = false;
        setTimeout(() => { buildEls.status.style.display = 'none'; }, 5000);
    }
}

/** 烧录（使用左上角选择的串口） */
async function doFlash() {
    if (buildInProgress) return;
    const port = getSelectedPort();
    if (!port) {
        toast('请先在左上角选择串口', 'error');
        return;
    }
    buildInProgress = true;
    setBuildButtonsDisabled(true);
    setBuildStatus('flashing', `烧录中... 端口: ${port}`);
    // 触发进度条轮询
    if (typeof pollFlashProgress === 'function') {
        flashPollTimer = setTimeout(pollFlashProgress, 500);
    }
    try {
        const res = await fetch('/api/flash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port }),
        });
        const data = await res.json();
        if (data.ok) {
            setBuildStatus('done', '烧录成功');
            toast('烧录成功', 'success');
        } else {
            setBuildStatus('error', '烧录失败: ' + (data.error || data.output || '').slice(0, 80));
            toast('烧录失败', 'error');
        }
    } catch (e) {
        setBuildStatus('error', '请求失败: ' + e.message);
        toast('烧录请求失败: ' + e.message, 'error');
    } finally {
        setBuildButtonsDisabled(false);
        buildInProgress = false;
        setTimeout(() => { buildEls.status.style.display = 'none'; }, 8000);
    }
}

/** 清理 */
async function doCleanBuild() {
    if (buildInProgress) return;
    buildInProgress = true;
    setBuildButtonsDisabled(true);
    setBuildStatus('cleaning', '清理中 (fullclean)...');
    try {
        const res = await fetch('/api/clean', { method: 'POST' });
        const data = await res.json();
        if (data.ok) {
            setBuildStatus('done', '清理完成');
            toast('清理完成', 'success');
        } else {
            setBuildStatus('error', '清理失败: ' + (data.error || '').slice(0, 80));
            toast('清理失败', 'error');
        }
    } catch (e) {
        setBuildStatus('error', '请求失败: ' + e.message);
    } finally {
        setBuildButtonsDisabled(false);
        buildInProgress = false;
        setTimeout(() => { buildEls.status.style.display = 'none'; }, 5000);
    }
}

/** 擦除 flash */
async function doEraseFlash() {
    if (buildInProgress) return;
    if (!confirm('确认擦除整个 flash？\n\n这会清除所有分区数据（bootloader、分区表、otadata、应用固件）。\n擦除后必须重新编译烧录才能启动。')) {
        return;
    }
    buildInProgress = true;
    setBuildButtonsDisabled(true);
    setBuildStatus('erasing', '擦除 flash 中 (erase-flash)...');
    try {
        const port = getSelectedPort() || 'COM6';
        const res = await fetch('/api/erase-flash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port }),
        });
        const data = await res.json();
        if (data.ok) {
            setBuildStatus('done', '擦除完成，请重新编译烧录');
            toast('Flash 擦除完成，请重新 build + flash', 'success');
        } else {
            setBuildStatus('error', '擦除失败: ' + (data.error || '').slice(0, 80));
            toast('擦除失败', 'error');
        }
    } catch (e) {
        setBuildStatus('error', '请求失败: ' + e.message);
    } finally {
        setBuildButtonsDisabled(false);
        buildInProgress = false;
        setTimeout(() => { buildEls.status.style.display = 'none'; }, 8000);
    }
}

buildEls.btnBuild.addEventListener('click', doBuild);
buildEls.btnFlash.addEventListener('click', doFlash);
buildEls.btnClean.addEventListener('click', doCleanBuild);
buildEls.btnErase.addEventListener('click', doEraseFlash);

// 初始化时更新端口提示
updateFlashPortHint();

// ============ 烧录进度 ============

const flashEls = {
    container: document.getElementById('flashProgressContainer'),
    phase: document.getElementById('flashPhase'),
    message: document.getElementById('flashMessage'),
    percent: document.getElementById('flashPercent'),
    barFill: document.getElementById('flashBarFill'),
    addr: document.getElementById('flashAddr'),
    partitions: document.getElementById('flashPartitions'),
    elapsed: document.getElementById('flashElapsed'),
};

const PHASE_LABELS = {
    connecting: '连接中',
    flashing: '烧录中',
    resetting: '重启中',
    done: '完成',
    error: '失败',
    building: '编译中',
    cleaning: '清理中',
};

let flashPollTimer = null;

function updateFlashProgress(prog) {
    if (!prog || !flashEls.container) return;

    // active=true 或 phase 非 done/error 时显示进度条
    const show = prog.active || prog.phase === 'done' || prog.phase === 'error';
    flashEls.container.style.display = show ? 'block' : 'none';
    if (!show) return;

    const pct = prog.percent || 0;
    flashEls.percent.textContent = pct + '%';
    flashEls.barFill.style.width = pct + '%';
    flashEls.message.textContent = prog.message || '';

    const phaseLabel = PHASE_LABELS[prog.phase] || prog.phase || '';
    flashEls.phase.textContent = phaseLabel;
    flashEls.phase.className = 'flash-progress-phase phase-' + (prog.phase || '');

    flashEls.barFill.className = 'flash-progress-bar-fill' +
        (prog.phase === 'error' ? ' error' : '') +
        (prog.phase === 'done' ? ' done' : '');

    // 地址（烧录阶段）或编译步骤（编译阶段）
    if (prog.address) {
        flashEls.addr.textContent = '地址: ' + prog.address;
    } else if (prog.build_step) {
        flashEls.addr.textContent = '步骤: ' + prog.build_step;
    } else {
        flashEls.addr.textContent = '';
    }

    // 分区数（烧录）或文件数（编译）
    if (prog.written_partitions > 0) {
        flashEls.partitions.textContent = '分区: ' + prog.written_partitions;
    } else if (prog.files_built > 0) {
        flashEls.partitions.textContent = '已编译: ' + prog.files_built + ' 个文件';
    } else {
        flashEls.partitions.textContent = '';
    }

    // 耗时 + 心跳检测
    let elapsedText = prog.elapsed > 0 ? '耗时: ' + prog.elapsed + 's' : '';
    if (prog.active && prog.idle_seconds !== undefined && prog.idle_seconds > 5) {
        // 超过 5 秒没有新输出，显示警告
        elapsedText += ' ⚠️ 无输出 ' + prog.idle_seconds + 's';
        flashEls.barFill.style.opacity = '0.5';
    } else {
        flashEls.barFill.style.opacity = '1';
    }
    flashEls.elapsed.textContent = elapsedText;

    // 完成或失败后停止轮询
    if (!prog.active && flashPollTimer) {
        clearTimeout(flashPollTimer);
        flashPollTimer = null;
        // 3 秒后自动隐藏完成/失败状态
        if (prog.phase === 'done' || prog.phase === 'error') {
            setTimeout(() => {
                if (flashEls.container) flashEls.container.style.display = 'none';
            }, 3000);
        }
    }
}

/** 轮询烧录进度（WebSocket 推送的兜底） */
function pollFlashProgress() {
    fetch('/api/flash/progress')
        .then(r => r.json())
        .then(data => {
            if (data.active || data.phase === 'done' || data.phase === 'error') {
                updateFlashProgress(data);
            }
            if (data.active) {
                flashPollTimer = setTimeout(pollFlashProgress, 1000);
            }
        })
        .catch(() => {});
}

// 监听 build/flash 按钮触发进度轮询（如果有这些按钮的话）
// 兜底：每 2 秒检查一次是否在烧录
setInterval(() => {
    if (!flashPollTimer) {
        fetch('/api/flash/progress')
            .then(r => r.json())
            .then(data => {
                if (data.active) {
                    pollFlashProgress();
                }
            })
            .catch(() => {});
    }
}, 2000);

// ============ 初始化 ============
refreshPorts();
updateStats();
loadQuickCommands();
connectWs();
setInterval(updateStats, 3000);

console.log('Serial Bridge v1.4 已加载（含烧录进度）');