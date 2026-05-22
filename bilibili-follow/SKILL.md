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
    ├── common.py             # 公共工具：配置、日志、B站API、飞书
    ├── init.py               # 初始化：将 agent 凭据写入 env 文件
    ├── check.py              # 检查UP主更新 → 输出 JSON + 写入 last_result.json
    ├── push.py               # 读取 last_result.json → 发送飞书卡片
    ├── config/
    │   └── base.yaml         # feishu_credential_source + 追踪目标列表
    ├── env/
    │   ├── .bili.env  # B站 Cookie（SESSDATA/bili_jct/buvid3）
    │   ├── .bili.env.example
    │   ├── .feishu.env       # 飞书凭据（env 模式）
    │   └── .feishu.env.example
    └── data/
        ├── bili_last_check.json  # 上次检查状态
        └── last_result.json      # 最近一次检查结果（push.py 读取）
```

## 核心脚本

| 脚本 | 职责 |
|------|------|
| `init.py` | 首次使用，将 agent 的飞书凭据写入 `env/.feishu.env`，切换 `base.yaml` 为 `env` 模式。B站 Cookie 需手动创建 `env/.bili.env` |
| `check.py` | 通过 B站 API 获取 UP 主最新视频和动态 → 对比状态 → 输出 JSON + 写入 `last_result.json` |
| `push.py` | 读取 `last_result.json` → 构建飞书卡片 → 发送 |

## 业务流

### 首次使用 — 初始化

如果 `base.yaml` 中 `feishu_credential_source` 为 `agent`，说明尚未初始化：

```
python3 scripts/init.py \
  --feishu-app-id <app_id> --feishu-app-secret <secret> --feishu-chat-id <chat_id>
```

初始化后 `base.yaml` 自动切换为 `env` 模式。

### 日常使用 — 检查更新

```
步骤 1：读取 scripts/config/base.yaml，记录 feishu_credential_source

步骤 2：
   - 如果 feishu_credential_source == "agent" → 先运行 init.py 初始化
   - 如果 feishu_credential_source == "env" → 继续

步骤 3：运行 python3 scripts/check.py
        输出 JSON 到 stdout，查看更新情况

步骤 4（如需推送）：运行 python3 scripts/push.py
        读取 last_result.json，发送飞书卡片消息
```

**凭据参数速查：**

| 来源 | push.py 参数 |
|------|-------------|
| `env` | 自动从 `env/` 读取，无需传参 |
| `agent` | `--feishu-app-id` `--feishu-app-secret` `--feishu-chat-id` |
