---
name: wechat-follow
description: 检查微信读书书架中公众号的更新状态，通过飞书卡片推送。支持按配置指定追踪的公众号，支持查询公众号详情。关键词：公众号/微信读书/更新推送
---

# 公众号更新追踪

## 目录结构

```
wechat-follow/
├── SKILL.md
└── scripts/
    ├── common.py             # 公共工具：配置、日志、微信读书API、飞书
    ├── init.py               # 初始化：将凭据写入 env 文件
    ├── check.py              # 检查公众号更新 → 输出 JSON + 写入 last_result.json
    ├── push.py               # 读取 last_result.json → 发送飞书卡片
    ├── detail.py             # 查询公众号详情
    ├── config/
    │   └── base.yaml         # feishu_credential_source + 追踪目标列表
    ├── env/
    │   ├── .weread.env  # 微信读书 API Key
    │   ├── .weread.env.example
    │   ├── .feishu.env       # 飞书凭据（env 模式）
    │   └── .feishu.env.example
    └── data/
        ├── wechat_last_check.json  # 上次检查状态
        └── last_result.json        # 最近一次检查结果（push.py 读取）
```

## 核心脚本

| 脚本 | 职责 |
|------|------|
| `init.py` | 首次使用，将微信读书/飞书凭据写入 `env/` 目录 |
| `check.py` | 调用微信读书 API → 过滤公众号 → 对比状态 → 输出 JSON + 写入 `last_result.json` |
| `push.py` | 读取 `last_result.json` → 构建飞书卡片 → 发送 |
| `detail.py` | 按名称或 bookId 查询公众号详情 |

## 业务流

### 凭据初始化

```
步骤 1：检查 scripts/env/.feishu.env 是否存在
         → 存在：跳过
         → 不存在：agent 用自身凭据自动写入，或提示用户提供凭据后写入：
           python3 scripts/init.py --feishu-app-id <app_id> --feishu-app-secret <secret> --feishu-chat-id <chat_id>

步骤 2：检查 scripts/env/.weread.env 是否存在
         → 存在：跳过
         → 不存在：提示用户提供微信读书 API Key，然后运行：
           python3 scripts/init.py --weread-api-key <用户提供的密钥>

步骤 3：再次检查两个 env 文件是否均已存在，缺一则报错退出
```

### 日常使用 — 检查更新

```
步骤 1：运行 python3 scripts/check.py
        输出 JSON 到 stdout，查看更新情况

步骤 2（如需推送）：运行 python3 scripts/push.py
        读取 last_result.json，发送飞书卡片消息
```


### 查询详情

```bash
python3 scripts/detail.py --name "猫笔刀"
python3 scripts/detail.py --book-id "MP_WXS_xxx"
```
