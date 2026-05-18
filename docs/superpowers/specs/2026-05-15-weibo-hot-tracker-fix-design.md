# weibo-hot-tracker 精准修复设计

## 背景

通过实际运行脚本并分阶段记录数据（原始数据 → 预筛选 → 规则匹配 → LLM 评估），发现以下核心问题：

1. **配置合并 bug**：全局配置覆盖本地配置，导致 LLM base_url 指向错误端点
2. **预筛选遗漏**：5 个应排除的分类未在 exclude_categories 中，浪费 LLM token
3. **规则匹配失效**：关键词与微博 API 实际分类名不匹配，fallback 形同虚设
4. **.env 加载位置不当**：藏在 call_llm_judge() 内部，其他功能无法受益
5. **文档不一致**：SKILL.md 默认值与 config.yaml 实际值矛盾
6. **关键词库不可维护**：关键词混在 config.yaml 中，无分类注释，难以逐步完善
7. **命名不直观**：level_3/level_2、STARKW_THIRD/STARKW_SECOND、is_interested 等命名语义模糊

## 修复范围

仅修复已确认的 bug 和命名问题，不引入新功能。保持单文件脚本定位。

---

## 修复1：配置合并逻辑

### 问题

`load_config()` 中全局配置的 dict 字段通过 `cfg[key].update(global_cfg[key])` 合并，全局值覆盖了本地值。本地 `config.yaml` 设置的 `base_url: http://172.28.59.193:13080` 被全局的 `http://host.docker.internal:3081/v1` 覆盖。

此外，`call_llm_judge()` 独立重新读取 `~/.hermes/config.yaml`，与 `load_config()` 逻辑重复且行为不一致。

### 方案

1. 修改 `load_config()` 的合并策略：本地配置优先，全局配置仅补充本地未定义的字段
2. 将 API key 解析逻辑（`${ENV_VAR}` 模板变量展开）统一到 `load_config()` 中
3. 将解析后的 `api_key`、`llm_model`、`base_url` 存入 CONFIG，`call_llm_judge()` 直接从 CONFIG 读取

### 合并策略

```python
for key, value in global_cfg.items():
    if key not in cfg:
        cfg[key] = value
    elif isinstance(cfg[key], dict) and isinstance(value, dict):
        merged = dict(value)
        merged.update(cfg[key])
        cfg[key] = merged
```

---

## 修复2：补全预筛选排除分类

### 问题

当前 `exclude_categories` 遗漏了 5 个微博 API 实际返回的分类，导致以下话题通过预筛选进入 LLM 评估，浪费 token：

- `幽默`：2 条（"原来是过敏啊"、"建议喜欢熬夜的"）
- `艺人`：5 条（温岚、迪丽热巴、蒋毅等）
- `作品衍生`：4 条（鹿晗等）
- `剧集`：4 条（白玉兰、金鹰奖等）
- `海外新闻`：1 条（熊吃冰淇淋等奇闻）

### 方案

在 `config.yaml` 的 `filter.exclude_categories` 中新增：

```yaml
- "幽默"
- "艺人"
- "作品衍生"
- "剧集"
- "海外新闻"
```

注意：`海外新闻` 与 `国际时政` 不同——`#黄仁勋库克发声#` 分类为 `国际时政`（保留），`一只熊吃掉了40磅冰淇淋` 分类为 `海外新闻`（排除）。

---

## 修复3：统一规则匹配关键词

### 问题

规则匹配的 `star_keywords` 与微博 API 实际返回的分类名不匹配：

- config 中有 "时事"、"政务"、"外交"，但 API 返回 "国内时政"、"国际时政"
- 导致 31 条预筛选话题中，规则匹配仅命中 2 条 ★★，0 条 ★★★
- LLM fallback 时规则形同虚设

### 方案

在 `config.yaml` 的 `filter.star_keywords.critical` 中新增匹配微博 API 分类名的关键词（key 名随修复7从 `level_3` 改为 `critical`）：

```yaml
critical:
  # 保留现有
  - "AI"
  - "人工智能"
  - "芯片"
  - "半导体"
  - "大模型"
  - "科技"
  - "技术突破"
  - "火箭"
  - "卫星"
  - "航天"
  - "国防"
  - "反腐"
  - "政策"
  - "监管"
  - "法治"
  # 新增：匹配微博 API 分类名和高权重标志词
  - "国内时政"
  - "国际时政"
  - "特朗普"
  - "黄仁勋"
  - "库克"
  - "白宫"
  - "中方回应"
  - "访华"
```

将无法匹配微博 API 数据的关键词从 critical 降级到 noteworthy："时事"、"政务"、"外交"。这些词在非微博场景下可能有用，降级而非删除可保留匹配能力。

### 权衡

增加这些关键词会让规则匹配更宽泛，可能匹配到一些不那么重要的时政话题。但作为 LLM 的 fallback，宁可多匹配再由 LLM 精筛，也好过完全匹配不到。

---

## 修复4：.env 加载移到启动时

### 问题

`.env` 文件加载逻辑在 `call_llm_judge()` 内部，导致：
1. 每次调用 LLM 都重复读取文件
2. 飞书推送等需要环境变量的功能无法受益

### 方案

将 `.env` 加载逻辑移到 `load_config()` 末尾，在脚本启动时统一执行一次。`call_llm_judge()` 中删除重复的 `.env` 加载代码。

---

## 修复5：SKILL.md 文档同步

### 问题

SKILL.md 中的默认值与 config.yaml 实际值不一致：

| 配置项 | SKILL.md 默认值 | config.yaml 实际值 |
|--------|----------------|-------------------|
| llm.model | Qwen3.6-35B-A3B | Qwen3.6-35B-A3B-C |
| llm.max_tokens | 2000 | 40960 |
| llm.timeout | 120 | 180 |
| feishu.retry_times | 5 | 3 |
| feishu.retry_delay | 5 | 10 |

### 方案

更新 SKILL.md 中的默认值，使其与 config.yaml 一致。在配置项说明中标注"实际值以 config.yaml 为准"。

---

## 修复6：关键词库可维护性

### 问题

当前关键词直接平铺在 `config.yaml` 的 `filter.star_keywords.level_3/level_2` 中，无分类、无注释。随着定期执行和持续维护，关键词库会不断增长，平铺结构将越来越难维护。

### 方案

将 `config.yaml` 中的关键词按语义分组，每组加注释说明用途。这样后续维护时可以：
- 快速定位某类关键词
- 知道哪些关键词对应微博 API 的哪个分类
- 按需增删，不会误改其他类别

```yaml
filter:
  star_keywords:
    critical:
      # 微博 API 分类名（直接匹配 category/field_tag 字段）
      - "国内时政"
      - "国际时政"
      # AI/芯片/大模型
      - "AI"
      - "人工智能"
      - "芯片"
      - "半导体"
      - "大模型"
      # 高权重人物/机构标志词
      - "特朗普"
      - "黄仁勋"
      - "库克"
      - "白宫"
      - "中方回应"
      - "访华"
      # 军事/国防
      - "国防"
      - "火箭"
      - "卫星"
      - "航天"
      # 政策/监管
      - "政策"
      - "监管"
      - "法治"
      - "反腐"
      # 科技
      - "科技"
      - "技术突破"
    noteworthy:
      # 经济/金融
      - "经济"
      - "财经"
      - "金融"
      - "股市"
      - "房产"
      - "就业"
      - "贸易"
      - "产业"
      - "消费"
      - "企业"
      # 汽车/新能源
      - "汽车"
      - "新能源"
      - "智能汽车"
      # 数码/互联网
      - "数码"
      - "手机"
      - "互联网"
      # 降级关键词（匹配度低但保留）
      - "时事"
      - "政务"
      - "外交"
```

同时，在 SKILL.md 中增加关键词维护指南：如何根据 LLM 评估结果发现遗漏关键词、如何添加新关键词。

---

## 修复7：命名直观化

### 问题

当前命名语义模糊，需要看代码才能理解含义：

| 当前命名 | 问题 |
|---------|------|
| `level_3` / `level_2` | 数字无语义，不知道3比2重要 |
| `STARKW_THIRD` / `STARKW_SECOND` | 星级+序数词，更难理解 |
| `is_interested()` | 返回0/2/3，不是布尔值，函数名误导 |
| `three_star` / `two_star` | 变量名与业务含义脱节 |
| `star` 字段 | 含义模糊 |

### 方案

统一重命名为业务语义明确的名称：

| 当前命名 | 新命名 | 含义 |
|---------|--------|------|
| `level_3` | `critical` | 必须推送的重点话题 |
| `level_2` | `noteworthy` | 值得关注的要闻话题 |
| `STARKW_THIRD` | `CRITICAL_KEYWORDS` | 重点关键词集合 |
| `STARKW_SECOND` | `NOTEWORTHY_KEYWORDS` | 要闻关键词集合 |
| `is_interested()` | `classify_priority()` | 返回优先级分类 |
| `three_star` | `critical_topics` | 重点话题列表 |
| `two_star` | `noteworthy_topics` | 要闻话题列表 |
| `star` 字段 | `priority` | 优先级（3=critical, 2=noteworthy, 0=skip） |
| `EXCLUDE_CATS` | `EXCLUDED_CATEGORIES` | 排除分类集合 |

### 影响范围

- `config.yaml`：`star_keywords.level_3` → `star_keywords.critical`，`star_keywords.level_2` → `star_keywords.noteworthy`
- `weibo_hot_tracker.py`：所有变量名、函数名、字段名同步更新
- `SKILL.md`：文档中的引用同步更新
- `skill_result/*.json`：`star` 字段 → `priority` 字段（历史数据不迁移，仅影响新数据）

---

## 不在范围内

以下问题已识别但不属于本次修复范围：

- 防重复推送（功能增强）
- ★★ 要闻推送飞书（功能增强）
- 历史对比/新上榜标注（功能增强）
- 飞书卡片增强（功能增强）
- LLM prompt 优化（需要实际测试 LLM 输出质量）
