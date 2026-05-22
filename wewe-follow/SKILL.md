---
name: wewe-follow
description: 追踪微信读书书架上的公众号更新和B站UP主更新，检测新文章/视频/动态发布。关键词：公众号/微信读书/B站/bilibili/UP主/更新追踪/未读检测
---

# wewe-follow

追踪微信读书书架上订阅的公众号和B站关注的UP主，通过对比本地状态检测是否有新内容更新。

## 工作目录

**所有命令在以下目录执行：**

```
cd /home/zify/myProject/oh-my-skill/wewe-follow/scripts
```

如果该目录不存在，说明 skill 安装位置与项目目录不同。此时告知用户："wewe-follow 项目位于 `/home/zify/myProject/oh-my-skill/wewe-follow/`，请确认项目已正确部署。"

## 环境初始化

首次使用前，检查 `.env` 是否存在：

```bash
test -f /home/zify/myProject/oh-my-skill/wewe-follow/scripts/.env || echo "MISSING"
```

如果输出 `MISSING`，告知用户需要配置以下环境变量（参考 `.env.example`）：

| 变量 | 必填 | 说明 |
|------|------|------|
| `weread_api_key` | 公众号需要 | 微信读书 API Key |
| `bili_sessdata` | B站需要 | B站登录 Cookie（F12 → Application → Cookies 获取） |
| `bili_jct` | B站需要 | B站 CSRF Token |
| `bili_buvid3` | B站需要 | B站设备指纹 |
| `feishu_app_id` | 飞书推送需要 | 飞书应用 ID |
| `feishu_app_secret` | 飞书推送需要 | 飞书应用密钥 |
| `feishu_chat_id` | 飞书推送需要 | 飞书群聊 ID |

**初始化方式**：让用户提供缺失的值，你写入 `.env` 文件即可。

## 目录结构

```
wewe-follow/
├── SKILL.md                  # Skill 说明文档
├── scripts/
│   ├── check_wechat.py       # 检查公众号更新状态
│   ├── check_bili.py         # 检查B站UP主更新状态
│   ├── .env.example          # 环境变量模板
│   ├── config.yaml           # 追踪目标配置（公众号 + B站UP主）
│   └── data/
│       ├── wechat_last_check.json   # 公众号上次查询快照（自动维护）
│       └── bili_last_check.json     # B站上次查询快照（自动维护）
```

## 核心脚本

| 脚本 | 触发方式 | 职责 |
|------|---------|------|
| `check_wechat.py` | agent 调用 | 查书架 → 过滤公众号 → 对比本地状态 → 输出更新摘要 |
| `check_bili.py` | agent 调用 | 查B站UP主 → 获取最新视频/动态 → 对比本地状态 → 输出更新摘要 |

## 配置文件

`scripts/config.yaml` 定义追踪目标（按名称匹配，留空则不限制）：

```yaml
# 公众号
wechat:
  - "猫笔刀"
  - "重燃阅读"

# B站UP主
bili:
  - "稚晖君"
```

## 业务流

### 1. 检查公众号更新

**触发词**：`检查公众号` / `公众号更新` / `有没有新文章` / `公众号`

**工作流**：

1. 执行 `cd /home/zify/myProject/oh-my-skill/wewe-follow/scripts && python3 check_wechat.py`，获取 JSON 输出
2. 按以下格式展示结果（**必须使用 markdown 链接语法，`[文本](weread://...)` 不可简化为纯文本**）：

```
📚 公众号更新检查（共 2 个）

🆕 有更新：
1. 猫笔刀 — 最后更新: 2025-05-30  [打开阅读](weread://reading?bId=MP_WXS_3198966508)

✅ 无新内容：
2. 重燃阅读 — 最后更新: 2025-05-29  [打开阅读](weread://reading?bId=MP_WXS_2392369754)
```

**链接格式硬性要求**：
- 每条公众号/UP主后面必须跟随 `[打开阅读](weread://reading?bId={bookId})` 格式的 markdown 链接
- 禁止用纯文本（如 "📖 打开阅读"）替代 markdown 链接
- 禁止省略 `[]()` 语法，确保渲染为可点击的超链接
- `bookId` 从 JSON 的 `accounts[].bookId` 取
3. 如果有 `new` 状态的公众号，用 📌 标记并提示"新关注的公众号"
4. 如果有 `removed` 状态的公众号，提示"以下公众号已不在书架上：xxx"

### 2. 飞书推送（公众号）

**触发词**：`推送公众号更新` / `公众号推送`

**工作流**：

1. 执行 `cd /home/zify/myProject/oh-my-skill/wewe-follow/scripts && python3 check_wechat.py --push`
2. 发送飞书卡片，包含所有公众号的更新状态、文章标题和阅读链接
3. 不需要额外回复，用户会在飞书收到卡片

### 3. 检查B站更新

**触发词**：`检查B站` / `B站更新` / `UP主更新` / `有没有新视频` / `B站`

**工作流**：

1. 执行 `cd /home/zify/myProject/oh-my-skill/wewe-follow/scripts && python3 check_bili.py`，获取 JSON 输出
2. 按以下格式展示结果（**必须使用 markdown 链接语法，不可用纯文本替代**）：

```
📺 B站UP主更新检查（共 2 个）

🆕 有更新（视频）：
1. 稚晖君 — 🎬新视频: 我做了个机械臂 / 2025-05-20 10:30  [打开主页](https://space.bilibili.com/20259914)

🆕 有更新（动态）：
2. LinusTechTips — 📝新动态: 本周硬件速递... / 2025-05-20 09:00  [打开主页](https://space.bilibili.com/123456)

✅ 无新内容：
3. 某UP主 — 最后视频: 2025-05-15 / 最后动态: 2025-05-18
```

**链接格式硬性要求**：每条 UP 主后面必须跟随 markdown 链接 `[打开主页](https://space.bilibili.com/{uid})`，`uid` 从 JSON 的 `accounts[].uid` 取。

3. 展示规则：
   - 先展示 🆕 有更新的（视频优先于动态），再展示 ✅ 无新内容的
   - 同一 UP 主同时有视频和动态更新时，合并在一个 UP 主项中，视频信息在前
   - 如果有 `new` 状态的 UP 主，用 📌 标记并提示"新关注的UP主"
   - 如果有 `removed` 状态的 UP 主，提示"以下UP主已不在追踪列表中：xxx"
   - 如果输出含 `errors` 字段，在末尾展示"⚠️ 以下UP主获取失败：xxx"

### 4. 飞书推送（B站）

**触发词**：`推送B站更新` / `B站推送`

**工作流**：

1. 执行 `cd /home/zify/myProject/oh-my-skill/wewe-follow/scripts && python3 check_bili.py --push`
2. 发送飞书卡片，包含所有 UP 主的更新状态、视频标题、动态摘要和空间链接
3. 不需要额外回复，用户会在飞书收到卡片

## 展示规范

- **时间戳**：所有 Unix 时间戳展示为 YYYY-MM-DD 格式
- **微信读书深度链接**：每条公众号末尾必须用 markdown 语法 `[打开阅读](weread://reading?bId={bookId})` 渲染为可点击链接
- **B站深度链接**：每条 UP 主末尾必须用 markdown 语法 `[打开主页](https://space.bilibili.com/{uid})` 渲染为可点击链接
- **禁止**：将链接输出为纯文本（如 "📖 打开阅读"），必须使用 `[文本](URL)` 语法
- **公众号状态标记**：🆕 有更新 / ✅ 无新内容 / 📌 新关注 / ❌ 已取关
- **B站标记**：🎬新视频 / 📝新动态
