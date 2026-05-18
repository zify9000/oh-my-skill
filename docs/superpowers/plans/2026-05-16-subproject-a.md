# 子项目 A：二元重构 + 推送记录增强 + 反馈闭环 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将微博热榜追踪系统从三级体系重构为二元体系，增加推送记录和飞书反馈闭环

**Architecture:** run.py 重构为二元判断（important/excluded），prompt 外置到 prompt.yaml，推送记录写入 push_history.jsonl，新增 feedback_daemon.py 常驻进程处理飞书回调，optimize_rules.py 去长连接

**Tech Stack:** Python 3.13, lark-oapi (飞书长连接 SDK), openai, pyyaml, curl_cffi, fcntl (文件锁)

---

## 文件结构

| 文件 | 变更类型 | 职责 |
|------|----------|------|
| `config.yaml` | 修改 | 删除 noteworthy，关键词迁移到 important 或 exclude |
| `prompt.yaml` | 新建 | LLM 判断 prompt（从 run.py 提取） |
| `run.py` | 修改 | 二元重构 + push_history.jsonl + 反馈卡片 + 从 prompt.yaml 读 prompt |
| `optimize_rules.py` | 修改 | 去长连接，按钮变 3 个，删除 noteworthy |
| `feedback_daemon.py` | 新建 | 飞书回调守护进程 |
| `SKILL.md` | 修改 | 同步文档 |

---

### Task 1: config.yaml 重构

**Files:**
- Modify: `weibo-hot-tracker/config.yaml`

- [ ] **Step 1: 重构 config.yaml**

将 `star_keywords.critical` 重命名为 `star_keywords.important`，删除 `star_keywords.noteworthy`，将 noteworthy 中的关键词按语义迁移到 important 或 exclude_categories。

noteworthy 关键词迁移方案：
- 迁移到 important：经济、财经、金融、股市、房产、就业、贸易、产业、消费、汽车、新能源、智能汽车、数码、互联网
- 迁移到 exclude_categories：无（noteworthy 中的关键词不是分类名，不会匹配 exclude）
- 删除（降级，不再规则匹配）：企业、手机、时事、政务、外交

新的 config.yaml：

```yaml
llm:
  model: "Qwen3.6-35B-A3B-C"
  max_tokens: 40960
  timeout: 180
  temperature: 0.1
  base_url: "http://172.28.59.193:13080"

feishu:
  chat_id: "oc_c1eda54af48b0aecb7abd029f607c058"
  retry_times: 3
  retry_delay: 10

filter:
  exclude_categories:
    - "娱乐"
    - "明星"
    - "影视"
    - "综艺"
    - "音乐"
    - "游戏"
    - "电竞"
    - "体育"
    - "养生"
    - "美食"
    - "旅游"
    - "美妆"
    - "时尚"
    - "搞笑"
    - "情感"
    - "宠物"
    - "幽默"
    - "艺人"
    - "作品衍生"
    - "剧集"
    - "海外新闻"
  star_keywords:
    important:
      # 微博 API 分类名
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
      # 经济/金融（从 noteworthy 迁移）
      - "经济"
      - "财经"
      - "金融"
      - "股市"
      - "房产"
      - "就业"
      - "贸易"
      - "产业"
      - "消费"
      # 汽车/新能源（从 noteworthy 迁移）
      - "汽车"
      - "新能源"
      - "智能汽车"
      # 数码/互联网（从 noteworthy 迁移）
      - "数码"
      - "互联网"

performance:
  empty_result_skip: true
```

- [ ] **Step 2: 验证 YAML 语法**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import yaml; yaml.safe_load(open('config.yaml')); print('YAML OK')"`

Expected: `YAML OK`

---

### Task 2: prompt.yaml 外置

**Files:**
- Create: `weibo-hot-tracker/prompt.yaml`

- [ ] **Step 1: 创建 prompt.yaml**

将 run.py 中 call_llm_judge 的硬编码 prompt 提取到 prompt.yaml，改为二元判断（重要/不重要）：

```yaml
judge_prompt: |
  你是一个新闻重要性评估专家。请判断以下微博热搜话题是否值得推送。

  === 判断标准（严格按此执行） ===

  【重要】范围：
  - AI大模型/大模型：GPT、ChatGPT、Claude、Gemini、DeepSeek、豆包、千问/Qwen、通义、文心、ERNIE、智谱ChatGLM 等模型发布/突破/争议
  - 芯片：芯片禁令、代工突破、封装创新、设备进展
  - 新能源：固态电池、钠电池、碳化硅、电动车新车型（仅限重磅车型发布）
  - 军事冲突：战争、武装对峙、导弹/火箭袭击、军事演习引发的紧张局势
  - 重大装备：航母、隐身战机、战略核潜艇、新型导弹、空间武器
  - 国内政策：国务院/部委重要政策文件、政府工作报告、重大改革举措
  - 宏观指标：GDP、CPI、PPI、PMI、进出口、失业率、居民收入等官方数据发布
  - 美联储：FOMC决议、鲍威尔讲话、美元利率决策
  - 房地产：重磅调控政策、头部房企爆雷、大型并购、政府救市
  - 经济/金融：股市重大波动、金融监管政策、贸易战、产业政策
  - 汽车/新能源：重磅新车型发布、自动驾驶重大突破、新能源政策

  【不重要】范围：
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
  严格按顺序输出，每行格式："序号:【重要】"或"序号:【不重要】"，不要输出话题名称，不要输出其他内容，不要遗漏任何一条：
  1:【重要】
  2:【不重要】
  3:【重要】
  ...

  必须包含全部 {topic_count} 条话题的判断。
```

- [ ] **Step 2: 验证 YAML 语法**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import yaml; d=yaml.safe_load(open('prompt.yaml')); print(f'prompt length: {len(d[\"judge_prompt\"])} chars'); print('YAML OK')"`

Expected: 输出 prompt 长度和 `YAML OK`

---

### Task 3: run.py 二元重构

**Files:**
- Modify: `weibo-hot-tracker/run.py`

- [ ] **Step 1: 重写 run.py**

完整重写 run.py，主要变更：
1. 删除 NOTEWORTHY_KEYWORDS，CRITICAL_KEYWORDS → IMPORTANT_KEYWORDS
2. classify_priority 返回 bool（True=important，False=excluded）
3. call_llm_judge 从 prompt.yaml 读取 prompt，解析"重要/不重要"而非"重点/要闻"
4. 删除 noteworthy 相关逻辑（报告只分"重要"一档）
5. 新增 push_history.jsonl 写入（含全部话题，pushed 字段）
6. 新增反馈卡片发送

```python
#!/opt/hermes/.venv/bin/python3
"""
微博热榜追踪脚本，筛选政治/经济以及科技类热点话题，推送飞书
"""

import sys
import json
import re
import os
import fcntl
import time as time_module
import logging
from datetime import datetime
from pathlib import Path
import requests as req
import curl_cffi

os.environ["TZ"] = "Asia/Shanghai"
time_module.tzset()

_http_sess = curl_cffi.Session(impersonate="chrome131")
sess = req.Session()

SCRIPT_DIR = Path(__file__).parent
REPORT_DIR = SCRIPT_DIR / "skill_result"
KEYWORD_STORE_PATH = SCRIPT_DIR / "keyword.json"
PUSH_HISTORY_PATH = SCRIPT_DIR / "push_history.jsonl"
PROMPT_PATH = SCRIPT_DIR / "prompt.yaml"


def setup_logging():
    """配置日志系统"""
    log_level = os.environ.get("WEIBO_TRACKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("weibo-tracker")


logger = setup_logging()


def load_config():
    """加载配置文件，本地 config.yaml 优先于全局 ~/.hermes/config.yaml"""
    import yaml

    local_cfg_path = SCRIPT_DIR / "config.yaml"
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
    """从 ~/.hermes/.env 加载环境变量"""
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
    """从全局配置解析 API key 和 LLM 参数"""
    import yaml

    cfg_path = Path.home() / ".hermes" / "config.yaml"
    if not cfg_path.exists():
        return

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
    if "default" in global_model_cfg and "model" not in cfg["llm"]:
        cfg["llm"]["model"] = global_model_cfg["default"]
    if "base_url" in global_model_cfg and "base_url" not in cfg["llm"]:
        cfg["llm"]["base_url"] = global_model_cfg["base_url"]


CONFIG = load_config()

EXCLUDED_CATEGORIES = set(CONFIG["filter"]["exclude_categories"])
IMPORTANT_KEYWORDS = set(CONFIG["filter"]["star_keywords"]["important"])


def is_important(category=None, field_tag=None, word_scheme=None) -> bool:
    """
    根据关键词匹配判断话题是否重要

    Args:
        category: 话题分类
        field_tag: 领域标签
        word_scheme: 话题关键词

    Returns:
        True: 重要话题（important 关键词匹配）
        False: 排除分类匹配或无匹配
    """
    if category and any(kw in category for kw in EXCLUDED_CATEGORIES):
        return False
    if field_tag and any(kw in field_tag for kw in EXCLUDED_CATEGORIES):
        return False

    text = " ".join(filter(None, [category or "", field_tag or "", word_scheme or ""]))
    return any(kw in text for kw in IMPORTANT_KEYWORDS)


def load_judge_prompt() -> str:
    """从 prompt.yaml 加载判断 prompt"""
    import yaml

    if not PROMPT_PATH.exists():
        logger.error(f"prompt.yaml 不存在: {PROMPT_PATH}")
        sys.exit(1)

    with open(PROMPT_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["judge_prompt"]


def retry(times=3, delay=5, backoff=2):
    """重试装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == times:
                        raise
                    logger.warning(f"第{attempt}次失败: {e}，{current_delay}秒后重试")
                    time_module.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator


def fetch_weibo_hot():
    """抓取微博热榜数据"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://weibo.com",
    }
    r = req.get("https://weibo.com/ajax/statuses/hot_band", headers=headers, timeout=10)
    r.raise_for_status()
    d = r.json()
    return d.get("data", {}).get("band_list", [])


def format_hotness(raw_hot):
    """格式化热度数值"""
    if raw_hot >= 10_000_000:
        return f"{raw_hot/10_000_000:.1f}千万"
    elif raw_hot >= 10_000:
        return f"{raw_hot/10_000:.1f}万"
    elif raw_hot >= 1000:
        return f"{raw_hot/1000:.1f}千"
    return str(raw_hot)


def call_llm_judge(topics: list) -> list:
    """
    调用 LLM 评估话题重要性（二元判断）

    Args:
        topics: 待评估的话题列表

    Returns:
        带有 important 字段的话题列表（True/False）
        如果调用失败，返回 None（将使用规则兜底）
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
    prompt_template = load_judge_prompt()
    prompt = prompt_template.format(topics_text=topics_text, topic_count=len(topics))

    try:
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

        important_map = {}
        for line in result_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):[【\[】]?(重要|不重要)[】\]]?", line)
            if m:
                idx = int(m.group(1))
                important_map[idx] = m.group(2) == "重要"

        logger.info(f"解析出 {len(important_map)} 条判断")

        for i, t in enumerate(topics):
            t["important"] = important_map.get(i + 1, False)

        return topics

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}，使用规则评估")
        return None


def _get_feishu_token() -> tuple:
    """获取飞书 tenant_access_token"""
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return None, None

    auth_resp = _http_sess.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    token = auth_resp.json()["tenant_access_token"]
    return token, app_id


def _send_feishu_message(token: str, chat_id: str, payload: dict):
    """发送消息到飞书"""
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = _http_sess.post(url, headers=headers, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"code={result.get('code')} msg={result.get('msg')}")
    return result["data"]["message_id"]


def send_to_feishu_card(date_str: str, topics: list) -> bool:
    """
    发送微博热榜卡片到飞书

    Args:
        date_str: 日期时间字符串
        topics: 重要话题列表

    Returns:
        True=成功，False=失败
    """
    token, _ = _get_feishu_token()
    if not token:
        logger.error("飞书认证失败")
        return False

    chat_id = CONFIG["feishu"]["chat_id"]

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**共 {len(topics)} 条重要话题**，政经科技类"}
        },
        {"tag": "hr"}
    ]

    for t in topics:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"🔴 {t['word']}"}
        })

    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"🕐 {date_str}"}
    })

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps({
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 微博热榜 · {date_str}"},
                "template": "red"
            },
            "elements": elements
        }, ensure_ascii=False)
    }

    try:
        retry_times = CONFIG["feishu"]["retry_times"]
        retry_delay = CONFIG["feishu"]["retry_delay"]
        send_with_retry = retry(times=retry_times, delay=retry_delay)(_send_feishu_message)
        send_with_retry(token, chat_id, payload)
        logger.info("Feishu 热榜卡片发送成功")
        return True
    except Exception as e:
        logger.error(f"Feishu 发送失败: {e}")
        return False


def send_feedback_card(date_str: str, topics: list, ts: str) -> bool:
    """
    发送反馈卡片到飞书，用户可逐条评价是否感兴趣

    Args:
        date_str: 日期时间字符串
        topics: 重要话题列表
        ts: push_history 记录的时间戳，用于定位记录

    Returns:
        True=成功，False=失败
    """
    token, _ = _get_feishu_token()
    if not token:
        logger.error("飞书认证失败")
        return False

    chat_id = CONFIG["feishu"]["chat_id"]

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**请评价以下话题是否值得关注**"}
        },
        {"tag": "hr"}
    ]

    for t in topics:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{t['word']}**"}
        })
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "👍 感兴趣"},
                    "type": "primary",
                    "value": {"source": "feedback", "ts": ts, "word": t["word"], "feedback": 1}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "👎 不感兴趣"},
                    "type": "default",
                    "value": {"source": "feedback", "ts": ts, "word": t["word"], "feedback": 0}
                }
            ]
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "⏭️ 跳过全部"},
            "type": "default",
            "value": {"source": "feedback", "ts": ts, "action": "skip_all"}
        }]
    })

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps({
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📝 推送反馈 · {date_str}"},
                "template": "blue"
            },
            "elements": elements
        }, ensure_ascii=False)
    }

    try:
        retry_times = CONFIG["feishu"]["retry_times"]
        retry_delay = CONFIG["feishu"]["retry_delay"]
        send_with_retry = retry(times=retry_times, delay=retry_delay)(_send_feishu_message)
        send_with_retry(token, chat_id, payload)
        logger.info("Feishu 反馈卡片发送成功")
        return True
    except Exception as e:
        logger.error(f"反馈卡片发送失败: {e}")
        return False


def save_report(report_text, topics, all_topics, feishu_ok):
    """保存报告到本地文件"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M")

    record = {
        "ts": now.isoformat(),
        "total_fetched": len(all_topics),
        "topics_sent": topics,
        "feishu_sent": feishu_ok,
        "report": report_text
    }

    filename = REPORT_DIR / f"{ts}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存至 {filename}")


def append_push_history(important_topics: list, excluded_topics: list, all_raw_topics: list):
    """
    追加推送记录到 push_history.jsonl

    记录全部话题，用 pushed 字段区分是否已推送。
    run.py 只追加（append-only），feedback_daemon.py 负责更新 feedback 字段。

    Args:
        important_topics: 重要话题列表（pushed=True）
        excluded_topics: 被排除的话题列表（pushed=False，含预筛选排除和 LLM 判断不重要）
        all_raw_topics: 微博 API 返回的原始话题列表
    """
    now = datetime.now()
    ts = now.isoformat()

    important_words = {t["word"] for t in important_topics}

    all_topic_entries = []

    for t in important_topics:
        all_topic_entries.append({
            "word": t["word"],
            "category": t.get("category", ""),
            "pushed": True,
            "feedback": None,
        })

    for t in excluded_topics:
        if t["word"] not in important_words:
            all_topic_entries.append({
                "word": t["word"],
                "category": t.get("category", ""),
                "pushed": False,
                "feedback": None,
            })

    record = {
        "ts": ts,
        "topics": all_topic_entries,
        "total_fetched": len(all_raw_topics),
    }

    with open(PUSH_HISTORY_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"push_history.jsonl 已追加: {len(all_topic_entries)} 条话题 (重要 {len(important_topics)}, 排除 {len(excluded_topics) - len(important_topics & excluded_topics)})")


def update_keyword_store(all_topics: list):
    """将本次热榜的 category 写入 keyword.json，仅追加新出现的条目"""
    if not all_topics:
        return

    store = {"categories": [], "last_updated": ""}
    if KEYWORD_STORE_PATH.exists():
        try:
            with open(KEYWORD_STORE_PATH, encoding="utf-8") as f:
                store = json.load(f)
                if isinstance(store.get("categories"), dict):
                    store["categories"] = list(store["categories"].keys())
        except Exception as e:
            logger.warning(f"读取 keyword.json 失败，将重建: {e}")

    now = datetime.now()
    new_cats = 0

    for item in all_topics:
        for cat in (item.get("category") or "").split(","):
            cat = cat.strip()
            if cat and cat not in store["categories"]:
                store["categories"].append(cat)
                new_cats += 1

    store["last_updated"] = now.isoformat()

    with open(KEYWORD_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    logger.info(f"keyword.json 已更新: +{new_cats} 新分类 (共 {len(store['categories'])} 分类)")


def main():
    """
    主函数：微博热榜追踪主流程

    流程：
    1. 抓取微博热榜数据
    2. 更新关键词库（keyword.json）
    3. 预筛选：排除娱乐、明星等非目标分类
    4. 调用 LLM 评估话题重要性（失败时使用规则兜底）
    5. 筛选重要话题
    6. 发送到飞书（热榜卡片 + 反馈卡片）
    7. 保存推送记录到 push_history.jsonl
    8. 保存报告到本地
    """
    now = datetime.now()
    time_str = now.strftime("%Y年%m月%d日 %H:%M")

    try:
        all_topics = fetch_weibo_hot()
        logger.info(f"抓取到 {len(all_topics)} 条热榜话题")
    except Exception as e:
        logger.error(f"抓取微博热榜失败: {e}")
        sys.exit(1)

    update_keyword_store(all_topics)

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
        for t in prefiltered:
            t["important"] = is_important(t["category"], t["field_tag"], t["word"])
    else:
        pass

    important_topics = [t for t in (judged or prefiltered) if t.get("important")]
    excluded_topics = [t for t in (judged or prefiltered) if not t.get("important")]

    logger.info(f"重要 {len(important_topics)} 条，排除 {len(excluded_topics)} 条")

    lines = [f"📊 微博热榜 {time_str}（政经科技）"]

    if important_topics:
        lines.append("【🔴 重要】")
        for t in important_topics:
            lines.append(f"🔴 {t['word']}")

    report_text = "\n".join(lines)

    feishu_ok = False
    if important_topics:
        feishu_ok = send_to_feishu_card(time_str, important_topics)
        if feishu_ok:
            send_feedback_card(time_str, important_topics, now.isoformat())
    else:
        logger.info("无重要话题，跳过飞书推送")

    append_push_history(important_topics, excluded_topics, all_topics)

    save_report(
        report_text,
        topics=[t["word"] for t in important_topics],
        all_topics=[t["word"] for t in all_topics],
        feishu_ok=feishu_ok
    )

    if not feishu_ok and important_topics:
        logger.error("飞书推送失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 语法检查**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import py_compile; py_compile.compile('run.py', doraise=True)" && echo "SYNTAX OK"`

Expected: `SYNTAX OK`

- [ ] **Step 3: 验证配置加载和关键词匹配**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "
from run import CONFIG, EXCLUDED_CATEGORIES, IMPORTANT_KEYWORDS, is_important
print(f'EXCLUDED_CATEGORIES: {len(EXCLUDED_CATEGORIES)} items')
print(f'IMPORTANT_KEYWORDS: {len(IMPORTANT_KEYWORDS)} items')
print(f'is_important(\"国内时政\", \"\", \"\"): {is_important(\"国内时政\", \"\", \"\")}')
print(f'is_important(\"娱乐\", \"\", \"\"): {is_important(\"娱乐\", \"\", \"\")}')
print(f'is_important(\"财经\", \"\", \"\"): {is_important(\"财经\", \"\", \"\")}')
print(f'is_important(\"数码\", \"\", \"\"): {is_important(\"数码\", \"\", \"\")}')
" 2>&1`

Expected: 国内时政=True, 娱乐=False, 财经=True, 数码=True

---

### Task 4: optimize_rules.py 重构

**Files:**
- Modify: `weibo-hot-tracker/optimize_rules.py`

- [ ] **Step 1: 重写 optimize_rules.py**

主要变更：
1. 删除 CHOICE_NOTEWORTHY，CHOICE_CRITICAL → CHOICE_IMPORTANT
2. 按钮从 4 个变 3 个：排除/重要/跳过
3. 删除长连接逻辑，只发送卡片后退出
4. 卡片 value 包含 source 和 session_id
5. find_unclassified_categories 查找 important 而非 critical/noteworthy
6. apply_choices_to_config 写入 important 而非 critical/noteworthy

```python
#!/opt/hermes/.venv/bin/python3
"""
规则优化脚本：发现 keyword.json 中未归类的分类，
让 LLM 预判归属，通过飞书交互式卡片让用户确认后由 feedback_daemon 写入 config.yaml
"""

import sys
import json
import os
import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

import yaml
import openai

os.environ.setdefault("TZ", "Asia/Shanghai")

SCRIPT_DIR = Path(__file__).parent
KEYWORD_STORE_PATH = SCRIPT_DIR / "keyword.json"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
DAEMON_STATE_PATH = SCRIPT_DIR / "daemon_state.json"

CHOICE_EXCLUDE = "exclude"
CHOICE_IMPORTANT = "important"
CHOICE_SKIP = "skip"

VALID_CHOICES = {CHOICE_EXCLUDE, CHOICE_IMPORTANT, CHOICE_SKIP}

LABEL_MAP = {
    CHOICE_EXCLUDE: "排除",
    CHOICE_IMPORTANT: "重要",
    CHOICE_SKIP: "跳过",
}


def setup_logging():
    """配置日志系统"""
    import time as time_module
    log_level = os.environ.get("WEIBO_TRACKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("rule-optimizer")


logger = setup_logging()


def load_config():
    """加载配置文件"""
    cfg = {}

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = yaml.safe_load(f) or {}
            logger.info(f"已加载配置文件: {CONFIG_PATH}")
        except Exception as e:
            logger.error(f"加载配置文件失败 {CONFIG_PATH}: {e}")
            raise

    global_cfg_path = Path.home() / ".hermes" / "config.yaml"
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

    from run import _load_dotenv, _resolve_api_credentials
    _load_dotenv()
    _resolve_api_credentials(cfg)

    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        if "feishu" not in cfg:
            cfg["feishu"] = {}
        cfg["feishu"]["chat_id"] = feishu_chat_id

    return cfg


def find_unclassified_categories(keyword_store: dict, config: dict) -> list:
    """
    找出 keyword.json 中未在 config.yaml 中归类的分类

    已归类 = 出现在 exclude_categories 或 star_keywords.important 中
    """
    all_cats = set(keyword_store.get("categories", []))

    filter_cfg = config.get("filter", {})
    exclude = set(filter_cfg.get("exclude_categories", []))
    important = set(filter_cfg.get("star_keywords", {}).get("important", []))

    classified = exclude | important
    return sorted(all_cats - classified)


def llm_classify_categories(categories: list, config: dict) -> dict:
    """
    调用 LLM 预判每个未归类分类的推荐归属

    Returns:
        {分类名: 推荐归属} 映射，归属值为 exclude/important/skip
    """
    api_key = config.get("llm", {}).get("api_key", "")
    if not api_key:
        logger.warning("未找到 API_KEY，所有分类默认标记为 skip")
        return {cat: CHOICE_SKIP for cat in categories}

    llm_model = config["llm"]["model"]
    base_url = config["llm"]["base_url"]

    filter_cfg = config.get("filter", {})
    exclude = filter_cfg.get("exclude_categories", [])
    important = filter_cfg.get("star_keywords", {}).get("important", [])

    cat_list = "\n".join(f"{i+1}. {cat}" for i, cat in enumerate(categories))

    prompt = f"""你是一个微博热搜分类专家。请判断以下微博热搜分类应归属哪一类。

=== 已有规则参考 ===

排除分类（娱乐/生活类，不值得关注）：
{', '.join(exclude)}

重要分类关键词（必须推送的重要新闻）：
{', '.join(important)}

=== 待分类列表 ===
{cat_list}

=== 归类标准 ===

排除(exclude)：纯娱乐/生活类，如影视、综艺、体育、美食、旅游等
重要(important)：政治/军事/重大科技/宏观经济等核心关注领域
跳过(skip)：无法确定或需要人工判断

=== 输出格式 ===
每行格式："序号:归属"，严格按序号输出，不要输出分类名称：
1:exclude
2:important
3:skip

必须包含全部 {len(categories)} 条分类的判断。"""

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            timeout=60,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.warning("LLM 返回为空，所有分类默认标记为 skip")
            return {cat: CHOICE_SKIP for cat in categories}

        result = {}
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):(exclude|important|skip)", line)
            if m:
                idx = int(m.group(1))
                choice = m.group(2)
                if 1 <= idx <= len(categories):
                    result[categories[idx - 1]] = choice

        for cat in categories:
            if cat not in result:
                result[cat] = CHOICE_SKIP
                logger.warning(f"LLM 未返回 {cat} 的判断，默认 skip")

        return result

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}，所有分类默认标记为 skip")
        return {cat: CHOICE_SKIP for cat in categories}


def build_card_json(unclassified: list, choices: dict, recommendations: dict, session_id: str) -> dict:
    """
    构建飞书交互式卡片 JSON

    Args:
        unclassified: 未归类分类列表
        choices: 当前用户选择 {分类: 归属}
        recommendations: LLM 推荐归属 {分类: 归属}
        session_id: 会话 ID（用于 feedback_daemon 路由）
    """
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**发现 {len(unclassified)} 个新分类待处理**，LLM 已给出推荐归属，可逐个调整后确认提交"
            }
        },
        {"tag": "hr"}
    ]

    for cat in unclassified:
        current = choices.get(cat, recommendations.get(cat, CHOICE_SKIP))
        rec = recommendations.get(cat, CHOICE_SKIP)
        rec_label = LABEL_MAP.get(rec, rec)

        rec_hint = f"（推荐: {rec_label}）" if current != rec else f"（推荐: {rec_label} ✓）"

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{cat}** {rec_hint}"}
        })

        actions = []
        for c in [CHOICE_EXCLUDE, CHOICE_IMPORTANT, CHOICE_SKIP]:
            label = LABEL_MAP[c]
            if c == current:
                label = f"{label} ✓"
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": "primary" if c == current else "default",
                "value": {"source": "optimize_rules", "session_id": session_id, "category": cat, "choice": c}
            })

        elements.append({"tag": "action", "actions": actions})
        elements.append({"tag": "hr"})

    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "✅ 确认提交"},
            "type": "primary",
            "value": {"source": "optimize_rules", "session_id": session_id, "action": "confirm"}
        }]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🔧 规则优化建议"},
            "template": "blue"
        },
        "elements": elements
    }


def send_feishu_card(config: dict, card_json: dict) -> str:
    """发送飞书交互式卡片，返回 message_id"""
    from run import _get_feishu_token, _send_feishu_message

    token, _ = _get_feishu_token()
    if not token:
        logger.error("飞书认证失败")
        sys.exit(1)

    chat_id = config["feishu"]["chat_id"]
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card_json, ensure_ascii=False),
    }

    message_id = _send_feishu_message(token, chat_id, payload)
    logger.info(f"卡片已发送, message_id={message_id}")
    return message_id


def apply_choices_to_config(choices: dict, config: dict):
    """
    将用户确认的分类归属写入 config.yaml

    Args:
        choices: {分类: 归属} 映射
        config: 当前配置字典
    """
    backup_path = CONFIG_PATH.with_suffix(".yaml.bak")
    shutil.copy2(CONFIG_PATH, backup_path)
    logger.info(f"已备份配置到 {backup_path}")

    filter_cfg = config.setdefault("filter", {})
    exclude = filter_cfg.setdefault("exclude_categories", [])
    star_kw = filter_cfg.setdefault("star_keywords", {})
    important = star_kw.setdefault("important", [])

    for cat, choice in choices.items():
        if choice == CHOICE_SKIP:
            continue
        elif choice == CHOICE_EXCLUDE:
            if cat not in exclude:
                exclude.append(cat)
                logger.info(f"  + 排除: {cat}")
        elif choice == CHOICE_IMPORTANT:
            if cat not in important:
                important.append(cat)
                logger.info(f"  + 重要: {cat}")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"配置已写入 {CONFIG_PATH}")


def main():
    CONFIG = load_config()

    if not KEYWORD_STORE_PATH.exists():
        logger.error("keyword.json 不存在，请先运行 run.py")
        sys.exit(1)

    with open(KEYWORD_STORE_PATH, encoding="utf-8") as f:
        keyword_store = json.load(f)

    unclassified = find_unclassified_categories(keyword_store, CONFIG)

    if not unclassified:
        logger.info("没有未归类的新分类，退出")
        return

    logger.info(f"发现 {len(unclassified)} 个未归类分类: {unclassified}")

    recommendations = llm_classify_categories(unclassified, CONFIG)
    for cat, choice in recommendations.items():
        label = LABEL_MAP.get(choice, choice)
        logger.info(f"  {cat} → {label}")

    choices = dict(recommendations)
    session_id = datetime.now().isoformat()

    card_json = build_card_json(unclassified, choices, recommendations, session_id)
    message_id = send_feishu_card(CONFIG, card_json)

    # 保存会话状态到 daemon_state.json，供 feedback_daemon 使用
    state = {"sessions": {}}
    if DAEMON_STATE_PATH.exists():
        try:
            with open(DAEMON_STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    state["sessions"][session_id] = {
        "type": "optimize_rules",
        "choices": choices,
        "recommendations": recommendations,
        "unclassified": unclassified,
        "message_id": message_id,
    }

    with open(DAEMON_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    logger.info(f"会话状态已保存到 daemon_state.json (session_id={session_id})")
    logger.info("卡片已发送，等待 feedback_daemon 处理回调")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 语法检查**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import py_compile; py_compile.compile('optimize_rules.py', doraise=True)" && echo "SYNTAX OK"`

Expected: `SYNTAX OK`

---

### Task 5: feedback_daemon.py

**Files:**
- Create: `weibo-hot-tracker/feedback_daemon.py`

- [ ] **Step 1: 创建 feedback_daemon.py**

常驻进程，通过飞书长连接监听所有卡片回调，根据 source 字段分发处理。

```python
#!/opt/hermes/.venv/bin/python3
"""
飞书回调守护进程：监听所有卡片交互回调，根据 source 字段分发处理

支持回调类型：
1. source=feedback → 更新 push_history.jsonl 的 feedback 字段
2. source=optimize_rules → 更新 config.yaml 的分类归属
"""

import sys
import json
import os
import fcntl
import shutil
import logging
from pathlib import Path

import yaml
import lark_oapi as lark

os.environ["TZ"] = "Asia/Shanghai"

SCRIPT_DIR = Path(__file__).parent
PUSH_HISTORY_PATH = SCRIPT_DIR / "push_history.jsonl"
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
DAEMON_STATE_PATH = SCRIPT_DIR / "daemon_state.json"


def setup_logging():
    """配置日志系统"""
    log_level = os.environ.get("WEIBO_TRACKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("feedback-daemon")


logger = setup_logging()


def load_daemon_state() -> dict:
    """加载 daemon 状态"""
    if DAEMON_STATE_PATH.exists():
        try:
            with open(DAEMON_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载 daemon_state.json 失败: {e}")
    return {"sessions": {}}


def save_daemon_state(state: dict):
    """保存 daemon 状态"""
    with open(DAEMON_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def update_feedback_in_history(ts: str, word: str, feedback: int):
    """
    更新 push_history.jsonl 中指定记录的 feedback 字段

    使用文件锁防止与 run.py 的并发写入冲突

    Args:
        ts: 记录时间戳
        word: 话题名称
        feedback: 反馈值（0 或 1）
    """
    if not PUSH_HISTORY_PATH.exists():
        logger.warning("push_history.jsonl 不存在")
        return

    with open(PUSH_HISTORY_PATH, "r+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

        lines = f.readlines()
        updated = False

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("ts") != ts:
                continue

            for topic in record.get("topics", []):
                if topic.get("word") == word and topic.get("feedback") is None:
                    topic["feedback"] = feedback
                    updated = True

            if updated:
                lines[i] = json.dumps(record, ensure_ascii=False) + "\n"
                break

        if updated:
            f.seek(0)
            f.writelines(lines)
            f.truncate()
            logger.info(f"反馈已更新: {word} → {feedback}")
        else:
            logger.warning(f"未找到匹配记录: ts={ts}, word={word}")

        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def skip_all_feedback(ts: str):
    """跳过指定批次所有未评价的 topic（feedback 保持 null）"""
    logger.info(f"跳过全部反馈: ts={ts}")


def handle_feedback(action_value: dict):
    """处理 source=feedback 的回调"""
    ts = action_value.get("ts", "")
    action = action_value.get("action", "")

    if action == "skip_all":
        skip_all_feedback(ts)
        return

    word = action_value.get("word", "")
    feedback = action_value.get("feedback")

    if word and feedback is not None:
        update_feedback_in_history(ts, word, int(feedback))


def handle_optimize_rules(action_value: dict, state: dict):
    """处理 source=optimize_rules 的回调"""
    session_id = action_value.get("session_id", "")
    action = action_value.get("action", "")

    session = state.get("sessions", {}).get(session_id)
    if not session or session.get("type") != "optimize_rules":
        logger.warning(f"未找到 optimize_rules 会话: {session_id}")
        return

    if action == "confirm":
        choices = session.get("choices", {})
        config = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                config = yaml.safe_load(f) or {}

        backup_path = CONFIG_PATH.with_suffix(".yaml.bak")
        shutil.copy2(CONFIG_PATH, backup_path)
        logger.info(f"已备份配置到 {backup_path}")

        filter_cfg = config.setdefault("filter", {})
        exclude = filter_cfg.setdefault("exclude_categories", [])
        star_kw = filter_cfg.setdefault("star_keywords", {})
        important = star_kw.setdefault("important", [])

        for cat, choice in choices.items():
            if choice == "skip":
                continue
            elif choice == "exclude":
                if cat not in exclude:
                    exclude.append(cat)
                    logger.info(f"  + 排除: {cat}")
            elif choice == "important":
                if cat not in important:
                    important.append(cat)
                    logger.info(f"  + 重要: {cat}")

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"配置已写入 {CONFIG_PATH}")

        # 更新卡片为"已完成"
        message_id = session.get("message_id", "")
        if message_id:
            try:
                from run import _get_feishu_token
                import curl_cffi

                token, _ = _get_feishu_token()
                if token:
                    sess = curl_cffi.Session(impersonate="chrome131")
                    done_card = json.dumps({
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "title": {"tag": "plain_text", "content": "✅ 规则优化已完成"},
                            "template": "green"
                        },
                        "elements": [{
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": "配置已更新，新规则将在下次运行时生效"}
                        }]
                    }, ensure_ascii=False)

                    sess.patch(
                        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={"content": done_card},
                        timeout=15,
                    )
            except Exception as e:
                logger.warning(f"更新卡片失败: {e}")

        # 删除已完成的 session
        state["sessions"].pop(session_id, None)
        save_daemon_state(state)
        logger.info("会话已完成并清理")

    else:
        # 用户点击了某个分类的按钮，更新 choices
        cat = action_value.get("category", "")
        choice = action_value.get("choice", "")
        if cat and choice in {"exclude", "important", "skip"}:
            session["choices"][cat] = choice
            save_daemon_state(state)
            logger.info(f"用户选择: {cat} → {choice}")

            # 更新卡片
            message_id = session.get("message_id", "")
            if message_id:
                try:
                    from optimize_rules import build_card_json
                    from run import _get_feishu_token
                    import curl_cffi

                    token, _ = _get_feishu_token()
                    if token:
                        sess = curl_cffi.Session(impersonate="chrome131")
                        new_card = build_card_json(
                            session["unclassified"],
                            session["choices"],
                            session["recommendations"],
                            session_id,
                        )
                        card_str = json.dumps(new_card, ensure_ascii=False)

                        sess.patch(
                            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            json={"content": card_str},
                            timeout=15,
                        )
                except Exception as e:
                    logger.warning(f"更新卡片失败: {e}")


def main():
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        logger.error("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        sys.exit(1)

    state = load_daemon_state()
    logger.info(f"加载了 {len(state.get('sessions', {}))} 个活跃会话")

    def handle_card_action(ctx, conf, event):
        action = event.event.action
        action_value = action.value
        source = action_value.get("source", "")

        logger.info(f"收到回调: source={source}, value={action_value}")

        if source == "feedback":
            handle_feedback(action_value)
        elif source == "optimize_rules":
            handle_optimize_rules(action_value, state)
        else:
            logger.warning(f"未知 source: {source}")

        return lark.im.v1.P2CardActionTriggerResponse()

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_card_action_trigger_v1(handle_card_action)
        .build()
    )

    cli = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG,
    )

    logger.info("feedback_daemon 已启动，等待飞书回调...")
    cli.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 语法检查**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "import py_compile; py_compile.compile('feedback_daemon.py', doraise=True)" && echo "SYNTAX OK"`

Expected: `SYNTAX OK`

---

### Task 6: 端到端验证 + 更新 SKILL.md

**Files:**
- Modify: `weibo-hot-tracker/SKILL.md`

- [ ] **Step 1: 运行 run.py 验证完整流程**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 run.py 2>&1`

Expected: 脚本运行成功，输出包含"重要 N 条，排除 M 条"，push_history.jsonl 被创建

- [ ] **Step 2: 验证 push_history.jsonl 内容**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "
import json
with open('push_history.jsonl') as f:
    for line in f:
        r = json.loads(line)
        pushed = sum(1 for t in r['topics'] if t['pushed'])
        excluded = sum(1 for t in r['topics'] if not t['pushed'])
        print(f'ts={r[\"ts\"]}, pushed={pushed}, excluded={excluded}, total_fetched={r[\"total_fetched\"]}')
        for t in r['topics'][:3]:
            print(f'  {t[\"word\"]} pushed={t[\"pushed\"]} feedback={t[\"feedback\"]}')
        if len(r['topics']) > 3:
            print(f'  ... ({len(r[\"topics\"])-3} more)')
" 2>&1`

Expected: 输出包含 pushed 和 excluded 统计

- [ ] **Step 3: 验证 optimize_rules.py**

Run: `cd /home/zify/myProject/oh-my-skill/weibo-hot-tracker && python3 -c "
from optimize_rules import load_config, find_unclassified_categories, KEYWORD_STORE_PATH
import json
CONFIG = load_config()
with open(KEYWORD_STORE_PATH) as f:
    ks = json.load(f)
unclassified = find_unclassified_categories(ks, CONFIG)
print(f'未归类分类 ({len(unclassified)}): {unclassified}')
" 2>&1`

Expected: 输出未归类分类列表

- [ ] **Step 4: 更新 SKILL.md**

更新目录结构、脚本路径、配置说明，删除 noteworthy 相关描述，新增 feedback_daemon.py、prompt.yaml、push_history.jsonl、daemon_state.json 的说明。

- [ ] **Step 5: 提交**

```bash
cd /home/zify/myProject/oh-my-skill
git add weibo-hot-tracker/
git commit -m "refactor: 二元重构 + 推送记录增强 + 反馈闭环

- 删除 noteworthy，三级变二级（important/excluded）
- prompt 外置到 prompt.yaml
- 新增 push_history.jsonl（含全部话题，pushed 字段）
- 新增反馈卡片（👍/👎 按钮）
- 新增 feedback_daemon.py（飞书长连接回调守护进程）
- optimize_rules.py 去长连接，按钮变 3 个
- config.yaml 关键词迁移（noteworthy → important）"
```
