"""微博热榜抓取：抓取 → 规则过滤 → 规则反写 → LLM核校 → 缓存重要话题"""
import sys
import json
import re
import fcntl
import argparse
import logging
from datetime import datetime

import requests as req

from common import (
    SCRIPT_DIR, DATA_DIR,
    ALL_TOPICS_PATH, CATEGORY_STORE_PATH, CACHED_TOPICS_PATH,
    setup_logging, load_base_config, load_rule_config, load_prompt,
    format_hotness, clean_word, resolve_llm_creds,
)

logger = setup_logging("fetch")

BASE_CONFIG = load_base_config()
RULE_CONFIG = load_rule_config()

EXCLUDE_CATEGORIES = set(RULE_CONFIG.get("category_exclude", []))
RECALL_KEYWORDS = set(RULE_CONFIG.get("keyword_recall", []))


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
        excluded_by_cat = any(kw in (category or "") or kw in (field_tag or "") for kw in EXCLUDE_CATEGORIES)

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


def call_llm_judge(topic_items: list, llm_model="", base_url="", api_key="") -> list:
    """LLM 首次核校"""
    import openai

    if not api_key:
        logger.warning("未找到 API_KEY，跳过 LLM 评估")
        return None

    if not llm_model or not base_url:
        logger.warning("未配置 llm_model 或 llm_base_url，跳过 LLM 评估")
        return None

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    topic_lines = []
    for i, n in enumerate(topic_items):
        cat = n.get("category") or n.get("field_tag") or ""
        topic_lines.append(f"{i+1}. {n.get('word','')} | 分类:{cat} | 热度:{n.get('hot_str','')}")

    topics_text = "\n".join(topic_lines)
    prompt_template = load_prompt("judge_prompt")
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
        for cat in (item.get("category") or "").split(","):
            cat = cat.strip()
            if cat and cat not in store["categories"]:
                store["categories"].append(cat)
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


def save_cached_topics(topic_items: list):
    """仅缓存 LLM 判定为 important 的话题到 cached_topics.jsonl"""
    important = [n for n in topic_items if n.get("important")]
    if not important:
        logger.info("无重要话题，跳过缓存")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    count = 0
    with open(CACHED_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        for n in important:
            record = {
                "rank": n.get("rank", 0),
                "word": n["word"],
                "category": n.get("category", ""),
                "field_tag": n.get("field_tag", ""),
                "raw_hot": n.get("raw_hot", 0),
                "hot_str": n.get("hot_str", ""),
                "note": n.get("note", ""),
                "cached_at": now.isoformat(),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"cached_topics.jsonl 已缓存 {count} 条重要话题")


def main():
    parser = argparse.ArgumentParser(description="微博热榜抓取")
    parser.add_argument("--llm-model", default="", help="agent 模式：LLM 模型名")
    parser.add_argument("--llm-base-url", default="", help="agent 模式：LLM API 地址")
    parser.add_argument("--llm-api-key", default="", help="agent 模式：LLM API 密钥")
    args = parser.parse_args()

    logger.info("=== 开始抓取 ===")

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

    llm_model, llm_base_url, llm_api_key = resolve_llm_creds(
        BASE_CONFIG, args.llm_model, args.llm_base_url, args.llm_api_key
    )
    judged = call_llm_judge(candidates, llm_model, llm_base_url, llm_api_key)
    if judged is None:
        logger.warning("LLM 判断失败，所有候选默认不缓存（保守策略）")
        return

    save_cached_topics(judged or candidates)

    important_count = sum(1 for n in (judged or candidates) if n.get("important"))
    logger.info(f"=== 抓取完成: 候选 {len(candidates)}, 排除 {len(excluded)}, 缓存 {important_count} ===")


if __name__ == "__main__":
    main()
