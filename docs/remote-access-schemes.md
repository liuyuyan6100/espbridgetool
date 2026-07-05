# 跨机器安全接入方案

> 本文档记录 Serial Bridge 跨机器/跨网络安全接入的几种方案，供日后联网调试时参考。
> 当前（本地调试）用不到，先归档。
>
> 更新时间：2026-07-04

## 背景

`serial_bridge.py` 默认监听 `127.0.0.1:8080`，只本机可达。当 agent 不在 ESP32 所在机器上运行时（跨机器/跨网络调试），需要让 agent 端的 `mcp_server.py` 能安全地访问到远端的 serial_bridge。

直接把 `HOST` 改成 `0.0.0.0` + 配 `BRIDGE_AUTH_TOKEN` 虽然能通，但有安全短板：

| 风险 | 说明 |
|---|---|
| Token 明文传输 | `X-Bridge-Token` 走 HTTP 明文，局域网抓包可见 |
| 无 TLS | 任何人能到达 8080 端口就能尝试调用 |
| 暴露面大 | 8080 监听 `0.0.0.0`，整个局域网可达，串口/烧录/日志全暴露 |
| 无速率限制 | token 泄露后无二次防线 |
| 无审计 | 谁在什么时候调了什么，无记录 |

下面是四种更安全的替代方案。

---

## 方案 A：SSH 隧道（推荐，同局域网首选）

**适用场景**：agent 机器和 ESP32 机器在同一局域网，或能互相 SSH。

**原理**：serial_bridge 始终只听 `127.0.0.1`（不暴露到网络），agent 端用 SSH 隧道把远程 8080 映射到本地 8080。mcp_server 仍连 `127.0.0.1:8080`，流量全程 SSH 加密。

```
agent 机器                          ESP32 机器
┌─────────────┐    SSH 隧道       ┌──────────────┐
│ mcp_server  │═══════════════════│ serial_bridge│
│ →127.0.0.1  │  加密 + 端口转发   │ 127.0.0.1    │
│   :8080     │═══════════════════│   :8080      │
└─────────────┘                   └──────┬───────┘
                                         │ COM6
```

### ESP32 机器（接板子的那台）

1. `.env` 保持 `HOST=127.0.0.1`（**不要**改成 0.0.0.0）
2. 开启 Windows OpenSSH 服务：
   - 设置 → 应用 → 可选功能 → 添加功能 → OpenSSH 服务器
   - 或 PowerShell（管理员）：`Start-Service sshd; Set-Service -Name sshd -StartupType Automatic`
3. 确认 Windows 防火墙放行 22 端口（SSH）
4. 双击 `start.bat` 启动 serial_bridge

### agent 机器

建隧道（保持窗口不关）：
```powershell
ssh -N -L 8080:127.0.0.1:8080 user@<esp32机器IP>
```
- `-N` 不执行远程命令，只做端口转发
- `-L 8080:127.0.0.1:8080` 把本地 8080 转发到远端的 127.0.0.1:8080
- 建议用密钥认证免密：`ssh-keygen` + `ssh-copy-id user@<ip>`

之后 mcp_server 配置不变，仍连 `BRIDGE_HOST=127.0.0.1`。

### 优点

- serial_bridge 始终只听 127.0.0.1，网络上看不到这个服务
- 全程 SSH 加密，token 不怕抓包（甚至可不配 token）
- SSH 自带身份认证（密钥/密码）
- 隧道断了 mcp_server 立刻报错，不会误连别人
- 零代码改动，只改部署方式

### 缺点

- 需要两台机器都能 SSH（Windows OpenSSH 配置一次）
- 隧道窗口要保持开着（可做成后台服务或用 `autossh`）

---

## 方案 B：Cloudflare Tunnel（跨网络、无公网 IP）

**适用场景**：agent 和 ESP32 不在同一局域网，ESP32 机器没有公网 IP（如在家里 NAT 后）。

**原理**：ESP32 机器跑 `cloudflared`，把 8080 隧道到 Cloudflare 边缘节点，agent 通过 `https://你的域名` 连。Cloudflare 自带 TLS，可加 Cloudflare Access 做身份认证。

```
agent 机器                 Cloudflare              ESP32 机器（NAT 后）
┌──────────┐  HTTPS    ┌──────────────┐  隧道  ┌──────────────┐
│mcp_server│──────────►│ 边缘节点      │◄──────│ cloudflared  │
│ →域名     │           │ + Access 认证 │        │ →127.0.0.1   │
└──────────┘           └──────────────┘        │   :8080      │
                                                └──────┬───────┘
```

### ESP32 机器

1. 安装 cloudflared：`winget install cloudflare.cloudflared`
2. 登录并创建隧道：
   ```powershell
   cloudflared tunnel login
   cloudflared tunnel create esp32-bridge
   cloudflared tunnel route dns esp32-bridge esp32-bridge.你的域名.com
   ```
3. 配置 `~/.cloudflared/config.yml`：
   ```yaml
   tunnel: <隧道ID>
   credentials-file: C:\Users\<你>\.cloudflared\<隧道ID>.json
   ingress:
     - hostname: esp32-bridge.你的域名.com
       service: http://127.0.0.1:8080
     - service: http_status:404
   ```
4. 启动隧道并设为服务：`cloudflared tunnel run esp32-bridge`（或 `cloudflared service install`）

### agent 机器

1. `.env` 设 `BRIDGE_HOST=esp32-bridge.你的域名.com`、`BRIDGE_PORT=443`
2. 如启用 Cloudflare Access，需在请求头带 Access token（需改 bridge_client 加 header 支持）

### 优点

- ESP32 机器不需要公网 IP，穿透 NAT
- 自带 TLS，不用自己管证书
- Cloudflare Access 可加 SSO/邮箱验证
- 域名好记

### 缺点

- 需要域名（绑在 Cloudflare）
- 免费版有带宽和连接数限制
- 多一层依赖（Cloudflare 服务可用性）
- bridge_client 可能要改（加 Authorization header）

---

## 方案 C：WireGuard VPN（团队多人共享）

**适用场景**：多个 agent/多人需要同时访问 ESP32 机器，或长期共享。

**原理**：两台机器加入同一个 WireGuard 网络，分配内网 IP（如 `10.0.0.x`），serial_bridge 监听 WireGuard 接口的 IP，只在 VPN 网络内可达。

```
agent 机器 (10.0.0.2)          WireGuard 隧道         ESP32 机器 (10.0.0.1)
┌──────────┐  加密 VPN 隧道  ┌──────────────┐  ┌──────────────┐
│mcp_server│════════════════════════════════│ serial_bridge│
│→10.0.0.1 │════════════════════════════════│ 10.0.0.1     │
└──────────┘                 └──────────────┘  └──────┬───────┘
```

### ESP32 机器

1. 安装 WireGuard：`winget install WireGuard.WireGuard`
2. 生成密钥对：`wg genkey | tee private.key | wg pubkey > public.key`
3. 配置 `wg0.conf`：
   ```ini
   [Interface]
   PrivateKey = <ESP32机器的私钥>
   Address = 10.0.0.1/24
   ListenPort = 51820

   [Peer]
   PublicKey = <agent机器的公钥>
   AllowedIPs = 10.0.0.2/32
   ```
4. `.env` 设 `HOST=10.0.0.1`（只监听 VPN 接口）
5. 防火墙放行 51820/UDP

### agent 机器

1. 配置对应的 `wg0.conf`（Address=10.0.0.2/24，Peer 指向 ESP32 机器）
2. `.env` 设 `BRIDGE_HOST=10.0.0.1`
3. 连上 VPN 后 mcp_server 即可访问

### 优点

- 全程加密，性能比 SSH 隧道好（内核级）
- 一台 ESP32 机器可被多个 agent 共享
- IP 层隔离，serial_bridge 只在 VPN 内可达
- 连接稳定，适合长期使用

### 缺点

- 需要在两台机器都装 WireGuard 并配密钥
- 需要理解 VPN 网络配置
- 每加一个 agent 要加一个 Peer 配置

---

## 方案 D：反向代理 + TLS + 客户端证书（安全要求极致）

**适用场景**：对安全要求最高，如生产环境、公网暴露、需要审计。

**原理**：用 Nginx/Caddy 做反向代理，终止 TLS，校验客户端证书，再转发到 serial_bridge。serial_bridge 仍只听 127.0.0.1。

```
agent 机器                Nginx/Caddy (TLS)          ESP32 机器
┌──────────┐  HTTPS    ┌──────────────────┐      ┌──────────────┐
│mcp_server│──────────►│ 证书校验 + TLS    │─────►│ serial_bridge│
│+ 客户端证书│           │ + 速率限制        │      │ 127.0.0.1    │
└──────────┘           │ + 审计日志        │      └──────────────┘
                       └──────────────────┘
```

### ESP32 机器

1. 用 Caddy（自动 HTTPS）或 Nginx
2. Caddyfile 示例：
   ```
   esp32-bridge.你的域名.com {
       reverse_proxy 127.0.0.1:8080

       tls /etc/certs/server.crt /etc/certs/server.key {
           client_auth {
               mode require_and_verify
               trusted_ca_cert_file /etc/certs/ca.crt
           }
       }

       # 速率限制（需 caddy-ratelimit 插件）
       rate_limit {
           zone esp32 {
               events 100
               window 1m
           }
       }

       log {
           output file /var/log/esp32-bridge/access.log
       }
   }
   ```
3. 生成 CA 和客户端证书：
   ```bash
   # CA
   openssl genrsa -out ca.key 4096
   openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=ESP32 Bridge CA"

   # 客户端证书
   openssl genrsa -out client.key 2048
   openssl req -new -key client.key -out client.csr -subj "/CN=agent"
   openssl x509 -req -days 365 -in client.csr -CA ca.crt -CAkey ca.key -set_serial 01 -out client.crt
   ```
4. agent 机器持 client.crt + client.key 访问

### agent 机器

- `.env` 设 `BRIDGE_HOST=esp32-bridge.你的域名.com`、`BRIDGE_PORT=443`
- bridge_client 需改造：httpx 请求带 `cert=(client.crt, client.key)` 和 `verify=ca.crt`

### 优点

- TLS 加密 + 客户端证书双向认证，安全性最高
- 速率限制防滥用
- 完整审计日志
- 可加 WAF / IP 白名单

### 缺点

- 配置最复杂（证书管理、Nginx/Caddy 配置）
- bridge_client.py 需要改造（加 cert 参数）
- 证书过期管理

---

## 方案选择速查

| 场景 | 推荐方案 | 一句话理由 |
|---|---|---|
| 同一局域网，1 对 1 | A（SSH 隧道） | 5 分钟搭好，零代码改动 |
| 跨网络，无公网 IP | B（Cloudflare Tunnel） | 穿透 NAT，自带 TLS |
| 多人/多 agent 共享 | C（WireGuard VPN） | 内核级加密，多对一 |
| 公网暴露/安全极致 | D（反代+TLS+客户端证书） | 双向认证 + 审计 |

## 共通注意事项

1. **BRIDGE_AUTH_TOKEN 仍建议配置**，即使有传输层加密——作为第二道防线，防止隧道内其他机器误访问。
2. **serial_bridge 的 `/api/stats/reset`、`/api/flash`、`/api/clean` 等高危端点**，跨机器时尤其要确认访问者可信。
3. **logs/ 目录**会记录串口和 IDF 输出，可能含敏感信息，跨机器时注意 ESP32 机器的文件权限。
4. **mcp_server.py 本身不需要暴露**——它始终是 stdio 进程，由 agent 本地 spawn，只通过 HTTP 连 serial_bridge。

## 当前状态

当前为本地调试模式：
- `HOST=127.0.0.1`（serial_bridge 只本机可达）
- `BRIDGE_AUTH_TOKEN=`（空，不校验）
- `MCP_TRANSPORT=stdio`（agent 本地 spawn mcp_server）

无需任何跨机器配置。等需要联网调试时，回到本文档选对应方案即可。
