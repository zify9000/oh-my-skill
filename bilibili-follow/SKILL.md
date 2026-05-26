---
name: bilibili-follow
description: 检查B站UP主更新状态（视频/图文动态），通过飞书卡片推送。支持按配置指定追踪的UP主。关键词：B站/UP主/更新推送/bilibili
---

# B站UP主更新追踪

## 目录结构

```
bilibili-follow/
├── SKILL.md
└── scripts/
    ├── bili_client.py        # B站API客户端：限流、重试、WBI签名、代理
    ├── common.py             # 公共工具：配置、日志、环境变量、飞书
    ├── init.py               # 凭据初始化：写入/刷新飞书&B站 Cookie（支持 CLI 传入或扫码）
    ├── check.py              # 检查UP主更新 → 输出 JSON + 写入 last_result.json
    ├── push.py               # 读取 last_result.json → 发送飞书卡片
    ├── config/
    │   └── base.yaml         # 全局配置（B站请求参数 + 飞书凭据来源 + 追踪列表）
    ├── env/
    │   ├── .bili.env  # B站 Cookie（SESSDATA/bili_jct/buvid3）
    │   ├── .bili.env.example
    │   ├── .feishu.env       # 飞书凭据（env 模式）
    │   └── .feishu.env.example
    └── data/
        ├── bili_last_check.json  # 上次检查状态
        ├── last_result.json      # 最近一次检查结果（push.py 读取）
        └── .wbi_keys.json        # WBI签名密钥缓存（6小时TTL）
```

## 核心脚本

| 脚本 | 职责 |
|------|------|
| `bili_client.py` | B站API客户端类，封装限流（令牌桶）、指数退避重试、WBI签名（持久化缓存）、代理支持 |
| `init.py` | 凭据初始化：写入飞书凭据 + 写入/刷新 B站 Cookie（支持 CLI 传值或扫码获取） |
| `check.py` | 通过 BiliClient 获取 UP 主最新视频和动态 → 对比状态 → 输出 JSON + 写入 `last_result.json`。支持 `--dry-run` |
| `push.py` | 读取 `last_result.json` → 构建飞书卡片 → 发送 |

## 业务流

### 🍪 Cookie 刷新（遇到 412 错误时）

```
python3 scripts/init.py --refresh-bili-cookie
```

1. 终端展示二维码
2. 用 B站 App「扫一扫」扫描
3. 手机上确认登录
4. Cookie 自动写入 `env/.bili.env`

依赖：`curl_cffi`、`qrcode`（可选，`pip install qrcode`）

### 凭据初始化

```
步骤 1：检查 scripts/env/.feishu.env 是否存在
         → 存在：跳过
         → 不存在：agent 用自身凭据自动写入，或提示用户提供凭据后写入：
           python3 scripts/init.py --feishu-app-id <app_id> --feishu-app-secret <secret> --feishu-chat-id <chat_id>

步骤 2：检查 scripts/env/.bili.env 是否存在
         → 存在：跳过
         → 不存在：询问用户选择初始化方式：
           A) 扫码：python3 scripts/init.py --refresh-bili-cookie
              → 展示二维码，提示用户用 B站 App 扫码确认
           B) 手动：提示用户提供 SESSDATA / bili_jct / buvid3，然后运行：
              python3 scripts/init.py --bili-sessdata <...> --bili-jct <...> --bili-buvid3 <...>

步骤 3：再次检查两个 env 文件是否均已存在，缺一则报错退出
```

### 日常使用 — 检查更新

```
步骤 1：运行 python3 scripts/check.py
        输出 JSON 到 stdout，查看更新情况

步骤 1（调试模式）：运行 python3 scripts/check.py --dry-run
        不发起任何API请求，用上次缓存数据重新输出

步骤 2（如需推送）：运行 python3 scripts/push.py
        读取 last_result.json，发送飞书卡片消息
```

## 反封控配置

`base.yaml` 中的 `bili` 段控制请求行为：

```yaml
bili:
  request_interval: 5     # 请求间隔秒数（令牌桶速率）
  retry_max: 3            # 最大重试次数
  retry_base_wait: 10     # 重试基础等待（指数退避 base * 2^attempt + jitter）
  proxy: ""               # 代理地址，如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080
```

### 代理配置说明

VPN/代理软件通常在本地开启代理端口，在 `proxy` 中填入对应地址即可：
- **Clash**: `http://127.0.0.1:7890`
- **V2Ray/Shadowsocks**: `socks5://127.0.0.1:1080`
- 留空则直连

### 反封控机制

| 机制 | 说明 |
|------|------|
| 令牌桶限流 | 全局请求速率受 `request_interval` 控制，避免突发 |
| 随机抖动 | 每次请求前 0.5~2秒随机延迟，打破机械节奏 |
| 指数退避 | 遇到 412/-412/-799 时，等待时间指数增长 + 随机抖动 |
| 随机化顺序 | 每次检查UP主的顺序随机打乱 |
| WBI密钥缓存 | 密钥持久化到文件（6小时TTL），减少nav接口请求 |
| dry-run模式 | 调试时零请求，不触发任何API调用 |

