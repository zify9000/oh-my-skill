# wewe-follow 扩展设计：B站UP主动态追踪

## 背景

`wewe-follow` 当前仅追踪微信公众号更新（通过微信读书 API）。本次扩展新增 B站 UP 主动态追踪，追平命名（`wechat` / `bili`），保持分平台独立脚本、共享 skill 品牌和推送基础设施的架构。

## 追踪范围

- **新视频**：检测 UP 主是否有新发布的视频
- **图文动态**：检测 UP 主是否有新发布的图文动态（排除转发）
- **直播**：不在本次范围内

## 目录结构

```
wewe-follow/
├── SKILL.md                            # 修改：新增B站触发词和展示规范
├── scripts/
│   ├── check_wechat.py                 # 重命名：公众号检查（原 check.py）
│   ├── check_bili.py                   # 新建：B站UP主检查
│   ├── config.yaml                     # 修改：mps→wechat，新增 bili 段
│   ├── .env.example                    # 不改
│   └── data/
│       ├── wechat_last_check.json      # 重命名：公众号状态（原 last_check.json）
│       └── bili_last_check.json        # 新建：B站状态
```

## 配置文件

`config.yaml`：

```yaml
# 公众号（按名称匹配，留空则不限制）
wechat:
  - "猫笔刀"
  - "重燃阅读"

# B站UP主（按名称匹配，留空则不限制）
bili:
  - "LinusTechTips"
  - "稚晖君"
```

- 两个段共享语义：留空 → 追踪全部；配置后 → 仅追踪列表
- UP 主名称通过 B站搜索接口在首次运行时解析为 UID，写入 `bili_last_check.json` 的 `uid_cache`

## 数据模型

### bili_last_check.json

```json
{
  "checked_at": "2026-05-20T10:00:00Z",
  "uid_cache": {
    "稚晖君": "20259914"
  },
  "accounts": {
    "稚晖君": {
      "uid": "20259914",
      "name": "稚晖君",
      "sign": "野生钢铁侠，AI/机器人/DIY...",
      "face": "https://i0.hdslb.com/bfs/face/xxx.jpg",
      "last_video": {
        "bvid": "BV1xx411c7XX",
        "title": "我做了个机械臂",
        "desc": "用3D打印做了个六轴机械臂...",
        "cover": "https://i0.hdslb.com/bfs/archive/xxx.jpg",
        "pubdate": 1747728000
      },
      "last_dynamic": {
        "id_str": "9876543210",
        "content": "新视频发了，这次折腾了三个月...",
        "images": ["https://i0.hdslb.com/bfs/album/xxx.jpg"],
        "timestamp": 1747720000
      }
    }
  }
}
```

- `uid_cache`：名称→UID 映射，首次解析后持久化，避免重复搜索
- `accounts` 中每个 UP 主保存：基础信息（sign/face）、最新视频信息（bvid/title/desc/cover/pubdate）、最新动态信息（id_str/content/images/timestamp）

### wechat_last_check.json

与旧版 `last_check.json` 结构一致，仅重命名。

## check_bili.py 脚本设计

### 流程

```
加载 config.yaml → 读取 bili 列表
        ↓
加载 bili_last_check.json
        ↓
  ┌─ 名称不在 uid_cache？──→ 调搜索接口解析 → 写入 uid_cache
        ↓
遍历每个 UP 主：
  ├─ 调 /x/space/acc/info 获取 name / sign / face
  ├─ 调 /x/space/wbi/arc/search 获取最新视频
  ├─ 调 /x/polymer/web-dynamic/v1/feed/space 获取最新图文动态
  └─ 对比上一次状态 → 判定 updated / no_change / new / removed
        ↓
更新 bili_last_check.json
        ↓
输出 JSON 到 stdout
        ↓
  └─ --push？──→ 组装飞书卡片 → 发送
```

### 状态判定

| 条件 | 状态 |
|------|------|
| 视频 pubdate 变大 或 动态 id_str 变化 | `updated` |
| 视频和动态均无变化 | `no_change` |
| UP 主不在上次记录中 | `new` |
| 上次有但本次未找到（且配置了追踪列表） | `removed` |

### 输出结构

```json
{
  "checked_at": "2026-05-20T10:05:00Z",
  "summary": {
    "total": 2,
    "updated": 1,
    "no_change": 1,
    "new": 0,
    "removed": 0
  },
  "accounts": [
    {
      "name": "稚晖君",
      "uid": "20259914",
      "sign": "野生钢铁侠...",
      "face": "https://i0.hdslb.com/bfs/face/xxx.jpg",
      "status": "updated",
      "has_new_video": true,
      "has_new_dynamic": false,
      "last_video": {
        "bvid": "BV1xx411c7XX",
        "title": "我做了个机械臂",
        "desc": "用3D打印做了个六轴机械臂...",
        "cover": "https://i0.hdslb.com/bfs/archive/xxx.jpg",
        "pubdate": 1747728000
      },
      "last_dynamic": {
        "id_str": "9876543210",
        "content": "新视频发了...",
        "images": ["https://i0.hdslb.com/bfs/album/xxx.jpg"],
        "timestamp": 1747720000
      },
      "deep_link": "https://space.bilibili.com/20259914"
    }
  ]
}
```

- `summary` 与公众号对齐四态统计
- `has_new_video` / `has_new_dynamic` 标记更新类型

## SKILL.md 更新

### 触发词

`检查B站` / `B站更新` / `UP主更新` / `有没有新视频` / `B站`

### 展示规范

```
📺 B站UP主更新检查（共 2 个）

🆕 有更新（视频）：
1. 稚晖君 — 新视频: 我做了个机械臂 / 2025-05-20 10:30 🔴未读  [打开](https://space.bilibili.com/20259914)

🆕 有更新（动态）：
2. LinusTechTips — 新动态: 本周硬件速递... / 2025-05-20 09:00  [打开](https://space.bilibili.com/123456)

✅ 无新内容：
3. 某UP主 — 最后视频: 2025-05-15 / 最后动态: 2025-05-18 ✅已读
```

- 状态标记复用公众号体系：🆕 / ✅ / 📌 / ❌ / 🔴未读 / ✅已读
- 更新类型标注"（视频）"或"（动态）"
- 同一 UP 主视频+动态都有更新时合并在一个 UP 主项中，视频优先
- 展示顺序：视频更新 → 动态更新 → 无变化
- 深度链接为 UP 主空间首页 `https://space.bilibili.com/{uid}`

### 飞书推送

触发词：`推送B站更新` / `B站推送`

与公众号共用飞书推送逻辑，`check_bili.py --push` 发送飞书卡片。

## 错误处理

### API 异常

| 场景 | 处理 |
|------|------|
| B站API 无响应/超时 | 输出 `{"error": "B站API请求失败: {reason}"}`，非零退出 |
| 名称搜索不到结果 | 输出 `{"error": "未找到UP主: '{name}'，请检查 config.yaml"}`，非零退出 |
| 单个 UP 主 API 失败 | 跳过该 UP 主，标记 `status: "error"`，继续处理其余 |
| 频率限制 | 请求间加 0.5s delay，遇 412 返回时退避重试 1 次 |

### 状态文件

| 场景 | 处理 |
|------|------|
| 首次运行（无状态文件） | 所有 UP 主标记为 `new` |
| uid_cache 中名称与实际不符 | 搜索接口重新解析，覆盖旧缓存 |

### 配置

| 场景 | 处理 |
|------|------|
| `bili` 段未配置或为空 | 需用户事先提供列表（B站无类似书架的全集入口） |
| `WEREAD_API_KEY` 不存在 | 仅影响公众号脚本，B站不依赖 |

### 飞书

| 场景 | 处理 |
|------|------|
| `--push` 但 feishu 环境变量未配 | 输出 `push_error` 字段，不阻断主流程 |

## 不在范围内

- 直播检测
- B站 API wbi 签名（如基础接口可调通则不引入）
- 抽象统一追踪层（YAGNI，仅两个平台）
