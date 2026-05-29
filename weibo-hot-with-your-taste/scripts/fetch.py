"""微博热榜抓取：抓取 → 规则过滤 → 规则反写 → LLM核校 → 缓存重要话题"""
import sys
import json
import re
import fcntl
import logging
from datetime import datetime
from urllib.parse import quote

import requests as req

from common import (
    SCRIPT_DIR, DATA_DIR,
    ALL_TOPICS_PATH, RULE_CHECKED_TOPICS_PATH, CATEGORY_STORE_PATH,
    CACHED_FETCH_META_PATH, CACHED_FETCH_TOPICS_PATH,
    setup_logging, load_base_config, load_llm_env, load_weibo_env, load_rule_config, load_judge_prompt, load_prompt,
    get_llm_creds, validate_llm_creds, get_weibo_cookies, format_hotness, clean_word,
)

logger = setup_logging("fetch")

BASE_CONFIG = load_base_config()
RULE_CONFIG = load_rule_config()

EXCLUDE_CATEGORIES = set(RULE_CONFIG.get("category_exclude", []))
RECALL_KEYWORDS = set(RULE_CONFIG.get("recall_keywords", []))

SUMMARY_CONFIG = BASE_CONFIG.get("summary", {})
SHORT_TOPIC_MAX_LEN = SUMMARY_CONFIG.get("short_topic_max_len", 5)
MAX_SUMMARY_LEN = SUMMARY_CONFIG.get("max_summary_len", 20)
TOP_WEIBO_COUNT = SUMMARY_CONFIG.get("top_weibo_count", 10)


def fetch_weibo_hot() -> list:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://weibo.com",
    }
    r = req.get("https://weibo.com/ajax/statuses/hot_band", headers=headers, timeout=10)
    r.raise_for_status()
    d = r.json()
    return d.get("data", {}).get("band_list", [])


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
    """规则过滤 + 反写，返回 (candidates, excluded)"""
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
        excluded_by_cat = any(excluded_cat in (category or "") or excluded_cat in (field_tag or "") for excluded_cat in EXCLUDE_CATEGORIES)

        if excluded_by_cat:
            rescued = any(kw in combined_text for kw in RECALL_KEYWORDS)
            if rescued:
                candidates.append(entry)
                logger.debug(f"反写救回: {word}")
            else:
                excluded.append(entry)
        else:
            candidates.append(entry)

    logger.info(f"规则过滤: {len(candidates)} 候选, {len(excluded)} 排除")
    return candidates, excluded


def fetch_topic_detail(word: str, cookies: dict) -> list:
    """获取话题下热度最高的微博内容列表（m.weibo.cn API），返回 [str, ...]"""
    import requests as req

    if not cookies or not cookies.get("SUB"):
        return []

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        resp = req.get(
            f"https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D1%26q%3D{quote(word)}&page_type=searchall",
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
                "Cookie": cookie_str,
                "Referer": "https://m.weibo.cn/",
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("ok") != 1:
            logger.warning(f"m.weibo 搜索失败: {word} - ok={data.get('ok')}")
            return []

        cards = data.get("data", {}).get("cards", []) or []
        contents = []
        for card in cards:
            if card.get("card_type") != 9:
                continue
            mblog = card.get("mblog", {})
            if mblog:
                text = mblog.get("text", "")
                clean_text = re.sub(r"<[^>]+>", "", text).strip()
                if clean_text:
                    contents.append(clean_text)
            if len(contents) >= TOP_WEIBO_COUNT:
                break

        return contents

    except Exception as e:
        logger.warning(f"获取话题详情异常: {word} - {e}")
        return []


def generate_summary(word: str, weibo_contents: list, llm_model="", base_url="", api_key="") -> str:
    """用 LLM 根据微博内容生成一句话摘要"""
    import openai

    if not weibo_contents or not api_key:
        return ""

    content_text = "\n".join(f"- {c}" for c in weibo_contents)
    prompt_template = load_prompt("summary_prompt")
    prompt = prompt_template.format(
        topic_name=word,
        weibo_content=content_text,
        max_len=MAX_SUMMARY_LEN,
    )

    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=100,
            timeout=30,
        )
        summary = resp.choices[0].message.content.strip()
        # 截断到最大长度
        if len(summary) > MAX_SUMMARY_LEN:
            summary = summary[:MAX_SUMMARY_LEN]
        return summary
    except Exception as e:
        logger.warning(f"生成摘要失败: {word} - {e}")
        return ""


def enrich_short_topics(candidates: list, llm_model="", base_url="", api_key="") -> list:
    """对短话题名补充摘要信息"""
    cookies = get_weibo_cookies()
    if not cookies or not cookies.get("SUB"):
        logger.info("未配置微博 Cookie，跳过话题摘要补充")
        return candidates

    short_topics = [n for n in candidates if len(n.get("word", "")) <= SHORT_TOPIC_MAX_LEN]
    if not short_topics:
        return candidates

    logger.info(f"发现 {len(short_topics)} 个短话题名（≤{SHORT_TOPIC_MAX_LEN}字），开始补充摘要")

    for n in short_topics:
        word = n["word"]
        contents = fetch_topic_detail(word, cookies)
        if contents:
            summary = generate_summary(word, contents, llm_model, base_url, api_key)
            if summary:
                n["summary"] = summary
                logger.info(f"摘要: {word} → {summary}")
        else:
            logger.info(f"未获取到话题详情: {word}")

    return candidates


def save_rule_checked_topics(candidates: list):
    """将规则过滤后的候选写入 ruleChecked_topics.jsonl"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat()

    record = {
        "ts": now,
        "total": len(candidates),
        "topics": [
            {
                "rank": n.get("rank", 0),
                "word": n["word"],
                "category": n.get("category", ""),
                "raw_hot": n.get("raw_hot", 0),
                "hot_str": n.get("hot_str", ""),
            }
            for n in candidates
        ],
    }

    with open(RULE_CHECKED_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"ruleChecked_topics.jsonl 已追加: {len(candidates)} 条")


def call_llm_judge(topic_items: list, llm_model="", base_url="", api_key="") -> list:
    """LLM 首次核校"""
    import openai

    issues = validate_llm_creds(llm_model, base_url, api_key)
    if issues:
        logger.warning(f"LLM 凭据异常，跳过评估: {'; '.join(issues)}")
        return None

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    topic_lines = []
    for i, n in enumerate(topic_items):
        category = n.get("category") or n.get("field_tag") or ""
        topic_lines.append(f"{i+1}. {n.get('word','')} | 分类:{category} | 热度:{n.get('hot_str','')}")

    topics_text = "\n".join(topic_lines)
    prompt_template = load_judge_prompt()
    prompt = prompt_template.format(topics_text=topics_text, topics_count=len(topic_items))

    try:
        stream = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=BASE_CONFIG["llm"]["temperature"],
            max_tokens=BASE_CONFIG["llm"]["max_tokens"],
            timeout=BASE_CONFIG["llm"]["timeout"],
            stream=True,
        )
        content = ""
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content += chunk.choices[0].delta.content
                print(chunk.choices[0].delta.content, end="", flush=True, file=sys.stderr)
        print(file=sys.stderr)
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
            m = re.match(r"^(\d+):[【\]【】]?(yes|no)[】\]]?", line, re.IGNORECASE)
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


def update_category_store(all_raw: list):
    """将本次热榜的 category 写入 topic_category.json"""
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
            logger.warning(f"读取 topic_category.json 失败，将重建: {e}")

    now = datetime.now()
    new_cats = 0

    for item in all_raw:
        for category in (item.get("category") or "").split(","):
            category = category.strip()
            if category and category not in store["categories"]:
                store["categories"].append(category)
                new_cats += 1

    store["last_updated"] = now.isoformat()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = open(CATEGORY_STORE_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        json.dump(store, fd, ensure_ascii=False, indent=2)
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()

    if new_cats:
        logger.info(f"topic_category.json 已更新: +{new_cats} 新分类 (共 {len(store['categories'])} 分类)")


def save_fetch_result(candidates: list, judged: list | None, llm_ok: bool):
    """写入缓存 meta + topics

    - meta: 记录本轮抓取状态，携带完整候选话题数据
      - llm_ok → candidates + important_idx
      - llm_failed → candidates（供 push 阶段补跑 judge）
    - topics: 扁平话题列表
      - llm_ok → 仅写 important 子集
      - llm_failed → 不写（候选在 meta 中，push 阶段补跑 judge 后写入）
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat()

    # ── 构建候选数据 ──
    def _strip(n: dict) -> dict:
        result = {
            "rank": n.get("rank", 0),
            "word": n["word"],
            "category": n.get("category", ""),
            "field_tag": n.get("field_tag", ""),
            "raw_hot": n.get("raw_hot", 0),
            "hot_str": n.get("hot_str", ""),
            "note": n.get("note", ""),
        }
        if n.get("summary"):
            result["summary"] = n["summary"]
        return result

    # ── 写 meta ──
    meta = {
        "ts": now,
        "llm": "ok" if llm_ok else "failed",
        "candidates": [_strip(n) for n in candidates],
    }
    if llm_ok and judged:
        important_idx = [i for i, n in enumerate(judged) if n.get("important")]
        meta["important_idx"] = important_idx

    with open(CACHED_FETCH_META_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    n_important = len(meta.get("important_idx", []))
    logger.info(f"cached_fetch_meta.jsonl 已写入: llm={meta['llm']}, candidates={len(candidates)}, important={n_important}")

    # ── 写 topics ──
    if llm_ok and judged:
        topics_to_write = [n for n in judged if n.get("important")]
    else:
        # LLM 失败时不写 topics，候选在 meta 中，push 阶段补跑 judge
        topics_to_write = []

    if not topics_to_write:
        if llm_ok:
            logger.info("无 important 话题可缓存")
        return

    count = 0
    with open(CACHED_FETCH_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        for n in topics_to_write:
            record = {
                "cycle_ts": now,
                "rank": n.get("rank", 0),
                "word": n["word"],
                "category": n.get("category", ""),
                "field_tag": n.get("field_tag", ""),
                "raw_hot": n.get("raw_hot", 0),
                "hot_str": n.get("hot_str", ""),
                "note": n.get("note", ""),
            }
            if n.get("summary"):
                record["summary"] = n["summary"]
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"cached_fetch_topics.jsonl 已缓存 {count} 条话题")


def main():
    logger.info("=== 开始抓取 ===")

    load_llm_env()
    llm_model, llm_base_url, llm_api_key = get_llm_creds()

    llm_issues = validate_llm_creds(llm_model, llm_base_url, llm_api_key)
    if llm_issues:
        logger.warning(f"LLM 凭据异常，将跳过 LLM 核校: {', '.join(llm_issues)}")

    try:
        all_raw = fetch_weibo_hot()
        logger.info(f"抓取到 {len(all_raw)} 条热搜")
    except Exception as e:
        logger.error(f"抓取微博热榜失败: {e}")
        sys.exit(1)

    save_topics(all_raw)
    update_category_store(all_raw)

    candidates, excluded = apply_rules(all_raw)
    if not candidates:
        logger.info("规则过滤后无候选，跳过")
        return

    save_rule_checked_topics(candidates)

    # 对短话题名补充摘要信息
    load_weibo_env()
    candidates = enrich_short_topics(candidates, llm_model, llm_base_url, llm_api_key)

    judged = call_llm_judge(candidates, llm_model, llm_base_url, llm_api_key)
    if judged is None:
        logger.warning("LLM 判断失败，回退为规则过滤全量缓存")
        save_fetch_result(candidates, None, llm_ok=False)
        logger.info(f"=== 抓取完成: 候选 {len(candidates)}, 排除 {len(excluded)}, LLM=failed (规则兜底) ===")
    else:
        save_fetch_result(candidates, judged, llm_ok=True)
        important_count = sum(1 for n in judged if n.get("important"))
        logger.info(f"=== 抓取完成: 候选 {len(candidates)}, 排除 {len(excluded)}, 缓存 {important_count} ===")


if __name__ == "__main__":
    main()
