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

$('btnClearLog').addEventListener('click', clearLog);

$('btnExport').addEventListener('click', () => {
    const text = logLines.map(l => stripAnsi(l.text)).join('');
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `serial_log_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
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
    } catch (e) { /* 忽略 */ }
}

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

// ============ IDF 工具 ============
async function runIdfAction(action, body = {}) {
    try {
        const res = await fetch('/api/' + action, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!data.ok && data.error) {
            toast(`${action} 失败: ${data.error}`, 'error');
        } else {
            toast(`${action} 完成`, 'success');
        }
    } catch (e) {
        toast(`${action} 请求失败: ${e.message}`, 'error');
    }
}

$('btnBuild').addEventListener('click', () => runIdfAction('build'));
$('btnFlash').addEventListener('click', () => {
    if (confirm('确认触发烧录？将自动释放串口并重新连接。')) {
        runIdfAction('flash');
    }
});
$('btnClean').addEventListener('click', () => runIdfAction('clean'));
$('btnBmgr').addEventListener('click', () => runIdfAction('bmgr'));

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

// ============ 初始化 ============
refreshPorts();
updateStats();
loadQuickCommands();
connectWs();
setInterval(updateStats, 3000);

console.log('Serial Bridge v1.1 已加载');