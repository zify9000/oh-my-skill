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
    """发送消息到飞书，返回 message_id"""
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

    topic_lines = "\n".join(f"**{i+1}.** {t['word']}  `{t.get('category', '')}`" for i, t in enumerate(topics))

    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": topic_lines}
        },
        {"tag": "hr"},
    ]

    for i, t in enumerate(topics):
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"{i+1} 👍"},
                    "type": "primary",
                    "value": {"source": "feedback", "ts": ts, "word": t["word"], "feedback": 1}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"{i+1} 👎"},
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
        excluded_topics: 被排除的话题列表（pushed=False）
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

    logger.info(f"push_history.jsonl 已追加: {len(all_topic_entries)} 条话题 (重要 {len(important_topics)}, 排除 {len(excluded_topics) - len([t for t in excluded_topics if t['word'] in important_words])})")


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
