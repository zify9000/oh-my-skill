# 规则优化脚本 optimize_rules.py 设计

## 背景

`run.py` 每次运行会向 `keyword.json` 累积微博 API 返回的分类名。随着时间推移，`keyword.json` 中会出现 `config.yaml` 尚未归类的分类（既不在 `exclude_categories`，也不在 `star_keywords.critical` 或 `star_keywords.noteworthy` 中）。

需要一个独立脚本，自动发现这些未归类分类，让 LLM 预判归属，通过飞书交互式卡片让用户确认后写入 `config.yaml`。

## 流程

```
1. 读取 keyword.json + config.yaml
2. 找出未归类的新分类
3. 无新分类 → 退出
4. 调用 LLM 预判每个新分类的推荐归属（exclude / critical / noteworthy）
5. 发送飞书交互式卡片（每个分类一行，带按钮）
6. 启动飞书长连接，等待用户交互
7. 用户点击按钮调整归属 → 卡片实时更新
8. 用户点击"确认提交" → 备份 config.yaml → 写入新规则 → 退出
9. 超时（10 分钟）→ 退出，不修改
```

## 卡片交互设计

```
┌──────────────────────────────────────────┐
│ 🔧 规则优化建议 · N 个新分类待处理        │
├──────────────────────────────────────────┤
│ 辟谣/通报 → 推荐: 要闻 ✓                 │
│   [排除] [重点] [要闻✓] [跳过]            │
├──────────────────────────────────────────┤
│ 电影 → 推荐: 排除 ✓                      │
│   [排除✓] [重点] [要闻] [跳过]            │
├──────────────────────────────────────────┤
│ 演出 → 推荐: 排除 ✓                      │
│   [排除✓] [重点] [要闻] [跳过]            │
├──────────────────────────────────────────┤
│              [✅ 确认提交]                │
└──────────────────────────────────────────┘
```

- 每个分类一行，4 个按钮：排除 / 重点 / 要闻 / 跳过
- LLM 推荐的选项默认带 ✓ 标记
- 用户点击某个按钮后，卡片实时更新该分类的选择状态（✓ 移动到新选择）
- 点击"确认提交"后，脚本将所有非"跳过"的分类写入 config.yaml

## 技术实现

### 飞书长连接

使用 `lark-oapi` SDK 的 `lark.ws` 模块建立 WebSocket 长连接，订阅 `card.action.trigger` 回调。

优势：
- 无需公网 IP 或域名
- 无需处理加密解密
- 本地开发环境即可接收回调

限制：
- 仅支持企业自建应用
- 回调需在 3 秒内处理完成
- 每个应用最多 50 个连接

### 卡片更新机制

用户点击按钮后，回调处理函数返回新的卡片 JSON，飞书自动更新卡片内容（无需重新发送消息）。

具体实现：在 `card.action.trigger` 回调中，根据用户点击的按钮更新内存中的选择状态，返回更新后的卡片 JSON 作为响应。

### LLM 预判

Prompt 结构：
- 输入：未归类分类列表 + 当前 config.yaml 的分类规则
- 输出：每个分类的推荐归属（exclude / critical / noteworthy / skip）
- 使用与 run.py 相同的 LLM 配置（model、base_url、api_key）

### config.yaml 写入

确认提交后：
1. 备份 `config.yaml` 为 `config.yaml.bak`
2. 读取当前 config.yaml
3. 将新分类追加到对应列表（`exclude_categories`、`star_keywords.critical`、`star_keywords.noteworthy`）
4. 写回 config.yaml（保留注释和格式：使用 ruamel.yaml 或手动拼接）

## 文件结构

```
weibo-hot-tracker/
├── optimize_rules.py   # 新增：规则优化脚本
├── run.py              # 现有：主脚本
├── config.yaml         # 现有：配置（会被 optimize_rules.py 修改）
├── keyword.json        # 现有：关键词库（只读）
└── ...
```

## 依赖

- `lark-oapi`：飞书 SDK（长连接 + 卡片交互回调）
- `openai`：LLM 调用（复用 run.py 的配置加载逻辑）
- `pyyaml` 或 `ruamel.yaml`：读写 config.yaml

## 关键约束

- 脚本运行后保持长连接，直到用户确认或超时（默认 10 分钟）
- 修改 config.yaml 前自动备份为 `config.yaml.bak`
- 没有未归类的新分类时，直接退出，不发送卡片
- 超时退出时不修改任何文件
- 复用 `run.py` 中的 `load_config()`、`_load_dotenv()`、`_resolve_api_credentials()` 逻辑。由于 `run.py` 有模块级副作用（`CONFIG = load_config()` 在 import 时执行），`optimize_rules.py` 不直接 import run.py，而是复制必要的配置加载函数（3 个函数约 60 行），避免 import 副作用

## 不在范围内

- 自动定期运行（手动触发即可）
- 优化 LLM prompt 中的判断标准文本
- 删除或修改已有规则（仅追加新分类）
