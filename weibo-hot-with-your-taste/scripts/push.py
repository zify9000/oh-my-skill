"""
微博热榜追踪脚本 — 抓取、过滤、反写、LLM核校、推送到飞书
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

SCRIPT_DIR = Path(__file__).parent
CONFIG_DIR = SCRIPT_DIR / "config"
DATA_DIR = SCRIPT_DIR / "data"
LOG_DIR = SCRIPT_DIR / "log"

BASE_CONFIG_PATH = CONFIG_DIR / "base.yaml"
RULE_CONFIG_PATH = CONFIG_DIR / "rule.yaml"
PROMPT_PATH = CONFIG_DIR / "prompt.yaml"
ALL_TOPICS_PATH = DATA_DIR / "all_topics.jsonl"
PUSHED_TOPICS_PATH = DATA_DIR / "pushed_topics.jsonl"
CATEGORY_STORE_PATH = DATA_DIR / "category.json"


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_level = os.environ.get("WEIBO_HOT_NEWS_LOG_LEVEL", "INFO").upper()
    log_file = LOG_DIR / f"push_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    return logging.getLogger("weibo-hot-topics")


logger = setup_logging()


def load_base_config():
    import yaml

    cfg = {}
    if BASE_CONFIG_PATH.exists():
        with open(BASE_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        logger.info(f"已加载基础配置: {BASE_CONFIG_PATH}")

    load_env()

    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        cfg.setdefault("feishu", {})["chat_id"] = feishu_chat_id

    return cfg


def load_rule_config():
    import yaml

    if not RULE_CONFIG_PATH.exists():
        logger.warning(f"规则配置不存在: {RULE_CONFIG_PATH}")
        return {"category_exclude": [], "keyword_recall": []}

    with open(RULE_CONFIG_PATH) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def load_env():
    """加载 scripts/.env 中的环境变量，支持 = 或 : 分隔"""
    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sep = "=" if "=" in line else (":" if ":" in line else None)
            if sep:
                k, v = line.split(sep, 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")


BASE_CONFIG = load_base_config()
RULE_CONFIG = load_rule_config()

EXCLUDE_CATEGORIES = set(RULE_CONFIG.get("category_exclude", []))
RECALL_KEYWORDS = set(RULE_CONFIG.get("keyword_recall", []))


def load_judge_prompt() -> str:
    import yaml

    if not PROMPT_PATH.exists():
        logger.error(f"prompt.yaml 不存在: {PROMPT_PATH}")
        sys.exit(1)

    with open(PROMPT_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["judge_prompt"]


def retry(times=3, delay=5, backoff=2):
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
            return None
        return wrapper
    return decorator


def fetch_weibo_hot():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://weibo.com",
    }
    r = req.get("https://weibo.com/ajax/statuses/hot_band", headers=headers, timeout=10)
    r.raise_for_status()
    d = r.json()
    return d.get("data", {}).get("band_list", [])


def format_hotness(raw_hot):
    if raw_hot >= 10_000_000:
        return f"{raw_hot/10_000_000:.1f}千万"
    elif raw_hot >= 10_000:
        return f"{raw_hot/10_000:.1f}万"
    elif raw_hot >= 1000:
        return f"{raw_hot/1000:.1f}千"
    return str(raw_hot)


def clean_word(w: str) -> str:
    return w.strip("#") if w else ""


def save_topics(all_raw: list):
    """保存原始抓取数据到 all_topics.jsonl"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    record = {
        "ts": now.isoformat(),
        "total": len(all_raw),
        "topics": [{"word": clean_word(item.get("word", "")), "category": item.get("category", "")} for item in all_raw],
    }

    with open(ALL_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"all_topics.jsonl 已追加: {len(all_raw)} 条")


def apply_rules(all_raw: list) -> tuple:
    """
    规则过滤 + 反写

    过滤: category 命中 exclude 列表 → 排除
    反写: 被排除的条目，word 命中 star 列表 → 救回

    Returns:
        (candidates, excluded): 候选列表和排除列表
    """
    candidates = []
    excluded = []

    for item in all_raw:
        rank = item.get("realpos", 0)
        if rank == 0:
            continue

        word = clean_word(item.get("word_scheme", item.get("word", "")))
        category = item.get("category", "")
        field_tag = item.get("field_tag", "")
        raw_hot = item.get("raw_hot", 0)
        note = item.get("note", "")

        entry = {
            "rank": rank,
            "word": word,
            "category": category,
            "field_tag": field_tag,
            "raw_hot": raw_hot,
            "hot_str": format_hotness(raw_hot),
            "note": note,
        }

        combined_text = f"{category} {field_tag} {word} {note}"
        excluded_by_cat = any(kw in (category or "") or kw in (field_tag or "") for kw in EXCLUDE_CATEGORIES)

        if excluded_by_cat:
            rescued = any(kw in combined_text for kw in RECALL_KEYWORDS)
            if rescued:
                candidates.append(entry)
                logger.debug(f"反写救回: {word} (命中 star 关键词)")
            else:
                excluded.append(entry)
        else:
            candidates.append(entry)

    logger.info(f"规则过滤: {len(candidates)} 候选, {len(excluded)} 排除")
    return candidates, excluded


def call_llm_judge(topic_items: list) -> list:
    import openai

    api_key = BASE_CONFIG.get("llm", {}).get("api_key") or os.environ.get("llm_api_key", "")
    if not api_key:
        logger.warning("未找到 API_KEY，跳过 LLM 评估")
        return None

    llm_model = BASE_CONFIG["llm"]["model"]
    base_url = BASE_CONFIG["llm"]["base_url"]

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    topic_lines = []
    for i, n in enumerate(topic_items):
        cat = n.get("category") or n.get("field_tag") or ""
        topic_lines.append(f"{i+1}. {n.get('word','')} | 分类:{cat} | 热度:{n.get('hot_str','')}")

    topics_text = "\n".join(topic_lines)
    prompt_template = load_judge_prompt()
    prompt = prompt_template.format(topics_text=topics_text, topics_count=len(topic_items))

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=BASE_CONFIG["llm"]["temperature"],
            max_tokens=BASE_CONFIG["llm"]["max_tokens"],
            timeout=BASE_CONFIG["llm"]["timeout"],
        )
        content = resp.choices[0].message.content
        if not content:
            logger.warning("LLM 返回内容为空")
            return None

        result_text = content.strip()
        logger.info(f"LLM 评估完成，响应长度: {len(result_text)} 字")

        important_map = {}
        for line in result_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):[【\[】]?(yes|no)[】\]]?", line, re.IGNORECASE)
            if m:
                idx = int(m.group(1))
                important_map[idx] = m.group(2).lower() == "yes"

        logger.info(f"解析出 {len(important_map)} 条判断")

        for i, n in enumerate(topic_items):
            n["important"] = important_map.get(i + 1, False)

        return topic_items

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return None


def _get_feishu_token() -> tuple:
    app_id = BASE_CONFIG.get("feishu", {}).get("feishu_app_id", "")
    app_secret = os.environ.get("feishu_app_secret", "")
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
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = _http_sess.post(url, headers=headers, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"code={result.get('code')} msg={result.get('msg')}")
    return result["data"]["message_id"]


def send_push_card(date_str: str, topic_items: list) -> str:
    """
    发送推送卡片到飞书
    """
    token, _ = _get_feishu_token()
    if not token:
        logger.error("飞书认证失败")
        return json.dumps({"success": False, "error": "飞书认证失败"}, ensure_ascii=False)

    chat_id = BASE_CONFIG["feishu"]["chat_id"]

    elements = []

    for i, n in enumerate(topic_items):
        cat = n.get("category", "")
        hot = n.get("hot_str", "")
        word = n["word"]

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{i+1}.** {word}  `{cat}`  {hot}"}
        })
        elements.append({"tag": "hr"})

    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "💬 回复序号评价本次推送，如\"1,3感兴趣\""}
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
                "title": {"tag": "plain_text", "content": "微博热榜"},
                "template": "green"
            },
            "elements": elements
        }, ensure_ascii=False)
    }

    try:
        retry_times = BASE_CONFIG["feishu"]["retry_times"]
        retry_delay = BASE_CONFIG["feishu"]["retry_delay"]
        send_with_retry = retry(times=retry_times, delay=retry_delay)(_send_feishu_message)
        send_with_retry(token, chat_id, payload)
        logger.info("飞书推送卡片发送成功")
        return json.dumps({"success": True, "pushed_count": len(topic_items)}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"飞书发送失败: {e}")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def append_pushed_topics(pushed_items: list, total_fetched: int):
    """追加推送记录到 pushed_topics.jsonl"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    record = {
        "ts": now.isoformat(),
        "total_fetched": total_fetched,
        "topics": [{"word": n["word"], "category": n.get("category", "")} for n in pushed_items],
    }

    with open(PUSHED_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"pushed_topics.jsonl 已追加: {len(pushed_items)} 条")


def update_category_store(all_raw: list):
    """将本次热榜的 category 写入 category.json"""
    if not all_raw:
        return

    store = {"categories": [], "last_updated": ""}
    if CATEGORY_STORE_PATH.exists():
        try:
            with open(CATEGORY_STORE_PATH, encoding="utf-8") as f:
                store = json.load(f)
                if isinstance(store.get("categories"), dict):
                    store["categories"] = list(store["categories"].keys())
        except Exception as e:
            logger.warning(f"读取 category.json 失败，将重建: {e}")

    now = datetime.now()
    new_cats = 0

    for item in all_raw:
        for cat in (item.get("category") or "").split(","):
            cat = cat.strip()
            if cat and cat not in store["categories"]:
                store["categories"].append(cat)
                new_cats += 1

    store["last_updated"] = now.isoformat()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CATEGORY_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    if new_cats:
        logger.info(f"category.json 已更新: +{new_cats} 新分类 (共 {len(store['categories'])} 分类)")


def main():
    """主流程：抓取 → 规则过滤 → 规则反写 → LLM核校 → 推送"""
    now = datetime.now()
    time_str = now.strftime("%Y年%m月%d日 %H:%M")

    try:
        all_raw = fetch_weibo_hot()
        logger.info(f"抓取到 {len(all_raw)} 条热搜")
    except Exception as e:
        logger.error(f"抓取微博热榜失败: {e}")
        sys.exit(1)

    # 步骤1产出：保存原始数据到 all_topics.jsonl
    save_topics(all_raw)

    # 更新 category 词库
    update_category_store(all_raw)

    # 步骤2-3：规则过滤 + 反写
    candidates, excluded = apply_rules(all_raw)

    if not candidates:
        logger.info("规则过滤后无候选新闻，跳过")
        return

    # 步骤4：LLM 核校
    judged = call_llm_judge(candidates)

    if judged is None:
        logger.warning("LLM 判断失败，所有候选默认推送")
        for c in candidates:
            c["important"] = True

    # 步骤5：推送决策
    pushed = [n for n in (judged or candidates) if n.get("important")]

    logger.info(f"最终推送 {len(pushed)} 条 (过滤后候选 {len(candidates)}, 排除 {len(excluded)})")

    result = None
    if pushed:
        result = send_push_card(time_str, pushed)
        try:
            result_obj = json.loads(result)
            feishu_ok = result_obj.get("success", False)
        except (json.JSONDecodeError, TypeError):
            feishu_ok = False
    else:
        logger.info("无重要话题，跳过推送")
        feishu_ok = True

    # 保存推送记录
    append_pushed_topics(pushed, len(all_raw))

    if not feishu_ok and pushed:
        logger.error("飞书推送失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
