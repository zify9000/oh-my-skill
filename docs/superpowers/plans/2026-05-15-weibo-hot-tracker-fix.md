# weibo-hot-tracker 精准修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 weibo-hot-tracker 脚本的 7 个已确认问题（配置合并 bug、预筛选遗漏、规则匹配失效、.env 加载位置、文档不一致、关键词库不可维护、命名不直观）

**Architecture:** 单文件脚本 `weibo_hot_tracker.py` + 配置文件 `config.yaml` + 文档 `SKILL.md`。所有修改保持单文件结构，不拆分。

**Tech Stack:** Python 3.13, PyYAML, requests, curl_cffi, openai SDK

---

## 文件结构

| 文件 | 职责 | 修改类型 |
|------|------|----------|
| `weibo-hot-tracker/config.yaml` | 本地配置 | 修改（key 重命名 + 新增关键词/分类） |
| `weibo-hot-tracker/weibo_hot_tracker.py` | 主脚本 | 修改（命名、配置合并、.env 加载） |
| `weibo-hot-tracker/SKILL.md` | 文档 | 修改（同步默认值 + 关键词维护指南） |

---

### Task 1: 命名直观化（config.yaml）

**Files:**
- Modify: `weibo-hot-tracker/config.yaml`

将 `star_keywords` 下的 key 从 `level_3`/`level_2` 改为 `critical`/`noteworthy`，并按语义分组加注释。

- [ ] **Step 1: 修改 config.yaml 的 star_keywords 结构**

将：

```yaml
  star_keywords:
    level_3:
      - "时事"
      ...
    level_2:
      - "经济"
      ...
```

替换为：

```yaml
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

- [ ] **Step 2: 在 exclude_categories 中新增遗漏分类**

在 `filter.exclude_categories` 列表末尾追加：

```yaml
    - "幽默"
    - "艺人"
    - "作品衍生"
    - "剧集"
    - "海外新闻"
```

---

### Task 2: 命名直观化 + 配置合并修复（weibo_hot_tracker.py）

**Files:**
- Modify: `weibo-hot-tracker/weibo_hot_tracker.py`

- [ ] **Step 1: 重命名模块级常量**

将第 88-90 行：

```python
EXCLUDE_CATS = set(CONFIG["filter"]["exclude_categories"])
STARKW_THIRD = set(CONFIG["filter"]["star_keywords"]["level_3"])
STARKW_SECOND = set(CONFIG["filter"]["star_keywords"]["level_2"])
```

替换为：

```python
EXCLUDED_CATEGORIES = set(CONFIG["filter"]["exclude_categories"])
CRITICAL_KEYWORDS = set(CONFIG["filter"]["star_keywords"]["critical"])
NOTEWORTHY_KEYWORDS = set(CONFIG["filter"]["star_keywords"]["noteworthy"])
```

- [ ] **Step 2: 重命名 is_interested 函数**

将第 93-122 行的 `is_interested` 函数重命名为 `classify_priority`，更新内部变量引用：

```python
def classify_priority(category=None, field_tag=None, word_scheme=None):
    """
    根据关键词匹配判断话题优先级

    Args:
        category: 话题分类
        field_tag: 领域标签
        word_scheme: 话题关键词

    Returns:
        3: critical（重点话题，critical 关键词匹配）
        2: noteworthy（要闻话题，noteworthy 关键词匹配）
        0: skip（排除分类匹配或无匹配）
    """
    text = " ".join(filter(None, [category or "", field_tag or "", word_scheme or ""]))

    if category and any(kw in category for kw in EXCLUDED_CATEGORIES):
        return 0
    if field_tag and any(kw in field_tag for kw in EXCLUDED_CATEGORIES):
        return 0

    for kw in CRITICAL_KEYWORDS:
        if kw in text:
            return 3

    for kw in NOTEWORTHY_KEYWORDS:
        if kw in text:
            return 2

    return 0
```

- [ ] **Step 3: 修复 load_config 配置合并逻辑 + 集成 .env 加载和 API key 解析**

将第 40-79 行的 `load_config` 函数替换为：

```python
def load_config():
    """加载配置文件，本地 config.yaml 优先于全局 ~/.hermes/config.yaml"""
    import yaml

    script_dir = Path(__file__).parent
    local_cfg_path = script_dir / "config.yaml"
    global_cfg_path = Path.home() / ".hermes" / "config.yaml"

    cfg = {}

    if local_cfg_path.exists():
        try:
            with open(local_cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            logger.info(f"已加载配置文件: {local_cfg_path}")
        except Exception as e:
            logger.error(f"加载配置文件失败 {local_cfg_path}: {e}")
            raise

    if global_cfg_path.exists():
        try:
            with open(global_cfg_path) as f:
                global_cfg = yaml.safe_load(f) or {}
                if global_cfg:
                    for key, value in global_cfg.items():
                        if key not in cfg:
                            cfg[key] = value
                        elif isinstance(cfg[key], dict) and isinstance(value, dict):
                            merged = dict(value)
                            merged.update(cfg[key])
                            cfg[key] = merged
                logger.info(f"已加载全局配置文件: {global_cfg_path}")
        except Exception as e:
            logger.warning(f"加载全局配置文件失败 {global_cfg_path}: {e}")

    _load_dotenv()

    _resolve_api_credentials(cfg)

    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        if "feishu" not in cfg:
            cfg["feishu"] = {}
        cfg["feishu"]["chat_id"] = feishu_chat_id

    return cfg


def _load_dotenv():
    """从 ~/.hermes/.env 加载环境变量（cron 任务不会自动加载 .env）"""
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _resolve_api_credentials(cfg):
    """从全局配置解析 API key 和 LLM 参数，存入 cfg['llm']"""
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    if not cfg_path.exists():
        return

    import yaml
    with open(cfg_path) as f:
        global_model_cfg = yaml.safe_load(f).get("model", {})

    raw_key = global_model_cfg.get("api_key", "")
    if raw_key.startswith("${") and raw_key.endswith("}"):
        env_var = raw_key[2:-1]
        api_key = os.environ.get(env_var, "")
    else:
        api_key = raw_key

    if "llm" not in cfg:
        cfg["llm"] = {}

    if api_key:
        cfg["llm"]["api_key"] = api_key
    if "default" in global_model_cfg:
        cfg["llm"]["model"] = global_model_cfg["default"]
    if "base_url" in global_model_cfg and "base_url" not in cfg["llm"]:
        cfg["llm"]["base_url"] = global_model_cfg["base_url"]
```

- [ ] **Step 4: 简化 call_llm_judge 函数**

将第 184-316 行的 `call_llm_judge` 函数替换为（删除内部的 .env 加载和全局配置读取，改为从 CONFIG 读取）：

```python
def call_llm_judge(topics: list) -> list:
    """
    调用LLM评估话题重要性

    Args:
        topics: 待评估的话题列表

    Returns:
        带有priority字段的话题列表，priority=3表示critical，priority=2表示noteworthy
        如果调用失败或缺少API密钥，返回None（将使用规则兜底）
    """
    import openai

    api_key = CONFIG.get("llm", {}).get("api_key", "")
    if not api_key:
        logger.warning("未找到 API_KEY，跳过 LLM 评估，使用规则")
        return None

    llm_model = CONFIG["llm"]["model"]
    base_url = CONFIG["llm"]["base_url"]

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    topic_lines = []
    for i, t in enumerate(topics):
        cat = t.get("category") or t.get("field_tag") or ""
        topic_lines.append(f"{i+1}. {t.get('word','')} | 分类:{cat} | 热度:{t.get('hot_str','')}")

    topics_text = "\n".join(topic_lines)

    prompt = f"""你是一个新闻重要性评估专家。请将以下微博热搜话题分为两类：

【重点】：值得推送的真正重要新闻
【要闻】：一般资讯，重要性不高

=== 判断标准（严格按此执行） ===

【重点】范围：
- AI大模型/大模型：GPT、ChatGPT、Claude、Gemini、DeepSeek、豆包、千问/Qwen、通义、文心、ERNIE、智谱ChatGLM 等模型发布/突破/争议
- 芯片：芯片禁令、代工突破、封装创新、设备进展
- 新能源：固态电池、钠电池、碳化硅、电动车新车型（仅限重磅车型发布）
- 军事冲突：战争、武装对峙、导弹/火箭袭击、军事演习引发的紧张局势
- 重大装备：航母、隐身战机、战略核潜艇、新型导弹、空间武器
- 国内政策：国务院/部委重要政策文件、政府工作报告、重大改革举措
- 宏观指标：GDP、CPI、PPI、PMI、进出口、失业率、居民收入等官方数据发布
- 美联储：FOMC决议、鲍威尔讲话、美元利率决策
- 房地产：重磅调控政策、头部房企爆雷、大型并购、政府救市

【要闻】范围（不属于重点的都在这里）：
- 航天/探月/火星探测（除非与军事装备直接相关）
- 一般科技产品发布（手机发布、APP更新、游戏发布等）
- 影视综艺：演唱会、电影播出、综艺热点
- 民生新闻：奇闻轶事、健康养生、个人理财科普
- 日常财经：个股涨跌、公司财报常规、理财产品
- 外交/国际关系：无实质冲突的一般性外交动态
- 民营企业家动态（除非与重大政策直接相关）
- 网红/社会事件

=== 话题列表 ===
{topics_text}

=== 输出格式 ===
严格按顺序输出，每行格式："序号:【星级】"，不要输出话题名称，不要输出其他内容，不要遗漏任何一条：
1:【重点】
2:【要闻】
3:【重点】
...

必须包含全部 {len(topics)} 条话题的判断。"""

    try:
        logger.debug(f"LLM prompt 长度: {len(prompt)} 字符")
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=CONFIG["llm"]["temperature"],
            max_tokens=CONFIG["llm"]["max_tokens"],
            timeout=CONFIG["llm"]["timeout"],
        )
        content = resp.choices[0].message.content
        if not content:
            logger.warning("LLM 返回内容为空，使用规则评估")
            return None
        result_text = content.strip()
        logger.info(f"LLM 评估完成，响应长度: {len(result_text)} 字")

        lines = result_text.split("\n")
        priority_map = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):[【\[【]?(重点|要闻)[】\]]?", line)
            if m:
                idx = int(m.group(1))
                priority = 3 if m.group(2) == "重点" else 2
                priority_map[idx] = priority

        logger.info(f"解析出 {len(priority_map)} 条优先级标注")

        for i, t in enumerate(topics):
            t["priority"] = priority_map.get(i + 1, 2)
            t["reason"] = ""

        return topics

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}，使用规则评估")
        return None
```

- [ ] **Step 5: 更新 main 函数中的变量名和字段名**

将 `main()` 函数中所有 `three_star` → `critical_topics`，`two_star` → `noteworthy_topics`，`star` → `priority`，`is_interested` → `classify_priority`，`EXCLUDE_CATS` → `EXCLUDED_CATEGORIES`。

将第 456-553 行的 `main` 函数替换为：

```python
def main():
    """
    主函数：微博热榜追踪主流程

    流程：
    1. 抓取微博热榜数据
    2. 预筛选：排除娱乐、明星等非目标分类
    3. 调用LLM评估话题重要性（失败时使用规则兜底）
    4. 筛选critical重点话题
    5. 发送到飞书
    6. 保存报告到本地
    """
    now = datetime.now()
    time_str = now.strftime("%Y年%m月%d日 %H:%M")

    try:
        all_topics = fetch_weibo_hot()
        logger.info(f"抓取到 {len(all_topics)} 条热榜话题")
    except Exception as e:
        logger.error(f"抓取微博热榜失败: {e}")
        sys.exit(1)

    prefiltered = []
    for item in all_topics:
        rank = item.get("realpos", 0)
        if rank == 0:
            continue

        word = item.get("word_scheme", item.get("word", ""))
        category = item.get("category", "")
        field_tag = item.get("field_tag", "")

        skip = any(kw in (category or "") or kw in (field_tag or "")
                   for kw in EXCLUDED_CATEGORIES)
        if skip:
            continue

        raw_hot = item.get("raw_hot", 0)
        prefiltered.append({
            "rank": rank,
            "word": word,
            "category": category,
            "field_tag": field_tag,
            "raw_hot": raw_hot,
            "hot_str": format_hotness(raw_hot),
        })

    logger.info(f"预筛选后剩余 {len(prefiltered)} 条话题")

    if CONFIG["performance"]["empty_result_skip"] and not prefiltered:
        logger.info("预筛选后无话题，跳过 LLM 调用")
        return

    judged = call_llm_judge(prefiltered)

    if judged is None:
        logger.warning("LLM 判断失败，使用规则兜底")
        critical_topics = [t for t in prefiltered if classify_priority(t["category"], t["field_tag"], t["word"]) == 3]
        noteworthy_topics = [t for t in prefiltered if classify_priority(t["category"], t["field_tag"], t["word"]) == 2]
        for t in critical_topics:
            t["priority"] = 3
        for t in noteworthy_topics:
            t["priority"] = 2
    else:
        critical_topics = [t for t in judged if t.get("priority") == 3]
        noteworthy_topics = [t for t in judged if t.get("priority") == 2]

    logger.info(f"Critical重点 {len(critical_topics)} 条，Noteworthy要闻 {len(noteworthy_topics)} 条")

    lines = [f"📊 微博热榜 {time_str}（政经科技）"]

    if critical_topics:
        lines.append("【★★★ 重点】")
        for t in critical_topics:
            lines.append(f"🔴 {t['word']}")

    if noteworthy_topics:
        lines.append("【★★ 要闻】")
        for t in noteworthy_topics:
            lines.append(f"🟡 {t['word']}")

    report_text = "\n".join(lines)

    if critical_topics:
        feishu_ok = send_to_feishu_card(time_str, critical_topics)
    else:
        feishu_ok = False
        logger.info("无Critical重点话题，跳过飞书推送")

    save_report(
        report_text,
        topics=[t["word"] for t in critical_topics],
        all_topics=[t["word"] for t in all_topics],
        feishu_ok=feishu_ok
    )

    if not feishu_ok and critical_topics:
        logger.error("飞书推送失败")
        sys.exit(1)
```

---

### Task 3: 更新 SKILL.md

**Files:**
- Modify: `weibo-hot-tracker/SKILL.md`

- [ ] **Step 1: 更新配置项默认值**

将 SKILL.md 中以下默认值更新为与 config.yaml 一致：

- `llm.model`: `Qwen3.6-35B-A3B` → `Qwen3.6-35B-A3B-C`
- `llm.max_tokens`: `2000` → `40960`
- `llm.timeout`: `120` → `180`
- `feishu.retry_times`: `5` → `3`
- `feishu.retry_delay`: `5` → `10`

在配置项说明末尾加注：`（实际值以 config.yaml 为准）`

- [ ] **Step 2: 更新过滤逻辑章节**

将 SKILL.md 中"过滤逻辑"章节的变量名和字段名同步更新：
- `★★★重点` → `Critical重点`
- `level_3` → `critical`
- `level_2` → `noteworthy`
- `star` → `priority`
- `model.default` → `CONFIG['llm']['model']`

- [ ] **Step 3: 新增关键词维护指南**

在 SKILL.md 的 Pitfalls 章节之前，新增"关键词维护"章节：

```markdown
## 关键词维护

关键词库位于 `config.yaml` 的 `filter.star_keywords` 下，按语义分组并附注释。

### 何时添加关键词

1. **LLM 判断为 Critical 但规则未匹配**：说明规则遗漏了该话题的特征词，应添加到 `critical` 组
2. **LLM 判断为 Noteworthy 但规则未匹配**：添加到 `noteworthy` 组
3. **新出现的微博分类名**：如果微博 API 新增了分类（如"科技"改为"前沿科技"），需同步更新 `exclude_categories` 或 `star_keywords`

### 如何添加关键词

1. 确定关键词属于 `critical`（必须推送）还是 `noteworthy`（值得关注）
2. 在对应组的合适语义分组中添加（如 AI 类、政策类、人物类等）
3. 如果没有合适的分组，新建一个并加注释说明
4. 运行脚本验证匹配效果

### 关键词匹配规则

- 匹配方式为**子串包含**：关键词 "芯片" 可匹配 "芯片禁令"、"半导体芯片" 等
- 匹配字段为 `category` + `field_tag` + `word_scheme` 的拼接文本
- `critical` 优先于 `noteworthy`：同一话题若同时匹配两组关键词，判定为 critical
```

---

### Task 4: 验证

- [ ] **Step 1: 语法检查**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import py_compile; py_compile.compile('weibo_hot_tracker.py', doraise=True)" && echo "OK"`

Expected: `OK`

- [ ] **Step 2: 验证配置加载**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "
from weibo_hot_tracker import CONFIG, EXCLUDED_CATEGORIES, CRITICAL_KEYWORDS, NOTEWORTHY_KEYWORDS
print('EXCLUDED_CATEGORIES:', sorted(EXCLUDED_CATEGORIES))
print('CRITICAL_KEYWORDS:', sorted(CRITICAL_KEYWORDS))
print('NOTEWORTHY_KEYWORDS:', sorted(NOTEWORTHY_KEYWORDS))
print('llm.model:', CONFIG['llm']['model'])
print('llm.base_url:', CONFIG['llm']['base_url'])
print('llm.api_key:', CONFIG['llm'].get('api_key', 'NOT SET')[:8] + '...' if CONFIG['llm'].get('api_key') else 'NOT SET')
"`

Expected: 输出包含新增的分类和关键词，base_url 为本地配置值

- [ ] **Step 3: 验证预筛选效果**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "
from weibo_hot_tracker import fetch_weibo_hot, EXCLUDED_CATEGORIES, format_hotness
all_topics = fetch_weibo_hot()
prefiltered = []
excluded = []
for item in all_topics:
    rank = item.get('realpos', 0)
    if rank == 0:
        continue
    word = item.get('word_scheme', item.get('word', ''))
    category = item.get('category', '')
    field_tag = item.get('field_tag', '')
    skip = any(kw in (category or '') or kw in (field_tag or '') for kw in EXCLUDED_CATEGORIES)
    if skip:
        excluded.append(f'{word} | cat={category}')
    else:
        prefiltered.append(word)
print(f'原始: {len(all_topics)}, 预筛选后: {len(prefiltered)}, 排除: {len(excluded)}')
for e in excluded:
    print(f'  ❌ {e}')
"`

Expected: 幽默、艺人、作品衍生、剧集、海外新闻分类的话题被排除

- [ ] **Step 4: 验证规则匹配效果**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "
from weibo_hot_tracker import fetch_weibo_hot, EXCLUDED_CATEGORIES, CRITICAL_KEYWORDS, NOTEWORTHY_KEYWORDS, classify_priority, format_hotness
all_topics = fetch_weibo_hot()
prefiltered = []
for item in all_topics:
    rank = item.get('realpos', 0)
    if rank == 0:
        continue
    word = item.get('word_scheme', item.get('word', ''))
    category = item.get('category', '')
    field_tag = item.get('field_tag', '')
    skip = any(kw in (category or '') or kw in (field_tag or '') for kw in EXCLUDED_CATEGORIES)
    if skip:
        continue
    priority = classify_priority(category, field_tag, word)
    label = 'CRITICAL' if priority == 3 else 'NOTEWORTHY' if priority == 2 else 'SKIP'
    print(f'  {label} {word} | cat={category}')
"`

Expected: 国内时政/国际时政类话题被标记为 CRITICAL，汽车/数码类被标记为 NOTEWORTHY

- [ ] **Step 5: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add weibo-hot-tracker/config.yaml weibo-hot-tracker/weibo_hot_tracker.py weibo-hot-tracker/SKILL.md
git commit -m "fix: weibo-hot-tracker 精准修复 — 配置合并、预筛选、关键词、命名、文档"
```
