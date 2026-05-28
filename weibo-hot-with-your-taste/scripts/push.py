"""微博热榜推送：读缓存 → 去重 → LLM二次过滤 → 推送飞书卡片 → 清空缓存"""
import sys
import json
import os
import re
import fcntl
from datetime import datetime

import curl_cffi

from common import (
    SCRIPT_DIR, DATA_DIR,
    CACHED_FETCH_META_PATH, CACHED_FETCH_TOPICS_PATH, PUSHED_TOPICS_PATH,
    setup_logging, load_base_config, load_llm_env, load_feishu_env, load_prompt, load_judge_prompt,
    get_llm_creds, get_feishu_creds, retry,
)

logger = setup_logging("push")
BASE_CONFIG = load_base_config()
_http_sess = curl_cffi.Session(impersonate="chrome131")
TASTED_TOPICS_PATH = DATA_DIR / "tasted_topics.jsonl"


# ── 缓存读写 ──

def read_cached_data() -> tuple:
    """读取缓存，返回 (cycles, topics)

    - cycles: cached_fetch_meta.jsonl 中的轮次元信息列表
    - topics: cached_fetch_topics.jsonl 中的话题列表
    """
    cycles = []
    if CACHED_FETCH_META_PATH.exists():
        with open(CACHED_FETCH_META_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        cycles.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning(f"跳过无效 meta 行: {line[:80]}")

    topics = []
    if CACHED_FETCH_TOPICS_PATH.exists():
        with open(CACHED_FETCH_TOPICS_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        topics.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning(f"跳过无效 topics 行: {line[:80]}")

    logger.info(f"从缓存读取 {len(cycles)} 轮抓取, {len(topics)} 条话题")
    return cycles, topics


def deduplicate_topics(topics: list) -> list:
    """按 word 去重，保留 cycle_ts 最新、raw_hot 最高的一条"""
    seen = {}
    for t in topics:
        word = t["word"]
        if word in seen:
            existing = seen[word]
            if t["cycle_ts"] > existing["cycle_ts"]:
                seen[word] = t
            elif t["cycle_ts"] == existing["cycle_ts"] and t.get("raw_hot", 0) > existing.get("raw_hot", 0):
                seen[word] = t
        else:
            seen[word] = t

    result = sorted(seen.values(), key=lambda x: x.get("raw_hot", 0), reverse=True)
    if len(topics) != len(result):
        logger.info(f"去重: {len(topics)} → {len(result)} 条")
    return result


def load_today_pushed_topics() -> dict:
    """加载当天已推送话题及其热度，返回 {word: {raw_hot, hot_str, ts}}"""
    from datetime import date as date_type
    
    if not PUSHED_TOPICS_PATH.exists():
        return {}
    
    today = date_type.today().isoformat()
    result = {}
    
    with open(PUSHED_TOPICS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not record.get("ts", "").startswith(today):
                continue
            for n in record.get("topics", []):
                word = n.get("word", "")
                if word:
                    # 保留最后一次推送的热度（同一天内可能有多次推送）
                    result[word] = {
                        "raw_hot": n.get("raw_hot", 0),
                        "hot_str": n.get("hot_str", ""),
                        "ts": record.get("ts", ""),
                    }
    
    return result


def compute_hot_change(current_raw_hot: int, previous_raw_hot: int, threshold_pct: float = 0.1) -> dict:
    """计算热度变化，返回 {change: int, change_str: str, significant: bool}
    
    threshold_pct: 变化阈值百分比（默认10%），低于阈值不显示变化标记
    """
    change = current_raw_hot - previous_raw_hot
    
    # 计算变化百分比（相对于之前热度）
    if previous_raw_hot > 0:
        change_pct = abs(change) / previous_raw_hot
    else:
        change_pct = 1.0 if change > 0 else 0.0
    
    significant = change_pct >= threshold_pct
    
    if not significant:
        return {"change": change, "change_str": "", "significant": False}
    
    # 格式化变化值
    if change >= 10000:
        change_str = f"↑{change / 10000:.1f}万"
    elif change >= 1000:
        change_str = f"↑{change / 1000:.1f}千"
    elif change > 0:
        change_str = f"↑{change}"
    elif change <= -10000:
        change_str = f"↓{abs(change) / 10000:.1f}万"
    elif change <= -1000:
        change_str = f"↓{abs(change) / 1000:.1f}千"
    elif change < 0:
        change_str = f"↓{abs(change)}"
    else:
        change_str = ""
    
    return {"change": change, "change_str": change_str, "significant": True}


def split_new_and_duplicate(topic_items: list, today_pushed: dict) -> tuple:
    """将话题分为新热点和重复热点，返回 (new_topics, duplicate_topics)
    
    duplicate_topics 包含热度变化信息
    """
    new_topics = []
    duplicate_topics = []
    
    for n in topic_items:
        word = n.get("word", "")
        if word in today_pushed:
            prev = today_pushed[word]
            hot_change = compute_hot_change(
                n.get("raw_hot", 0),
                prev.get("raw_hot", 0),
                threshold_pct=0.1
            )
            duplicate_topics.append({
                "word": word,
                "category": n.get("category", ""),
                "raw_hot": n.get("raw_hot", 0),
                "hot_str": n.get("hot_str", ""),
                "hot_change": hot_change,
            })
        else:
            new_topics.append(n)
    
    return new_topics, duplicate_topics


def clear_cached_data():
    """清空缓存文件"""
    for p in [CACHED_FETCH_META_PATH, CACHED_FETCH_TOPICS_PATH]:
        if p.exists():
            p.unlink()
            logger.info(f"{p.name} 已清空")


# ── LLM 核校 ──

def call_llm_judge(topic_items: list, llm_model="", base_url="", api_key="") -> list:
    """对 failed cycle 的话题补跑 LLM 核校，返回仅 important 的话题"""
    import openai

    if not api_key:
        logger.warning("未找到 API_KEY，跳过 LLM 核校")
        return topic_items

    if not llm_model or not base_url:
        logger.warning("未配置 llm_model 或 llm_base_url，跳过 LLM 核校")
        return topic_items

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    topic_lines = []
    for i, n in enumerate(topic_items):
        cat = n.get("category") or n.get("field_tag") or ""
        topic_lines.append(f"{i+1}. {n.get('word','')} | 分类:{cat} | 热度:{n.get('hot_str','')}")

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
            logger.warning("LLM 核校返回为空，保留全部")
            return topic_items

        result_text = content.strip()
        logger.info(f"LLM 核校完成，响应长度: {len(result_text)} 字")

        important_indices = set()
        for line in result_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):[【\]【】]?(yes|no)[】\]]?", line, re.IGNORECASE)
            if m and m.group(2).lower() == "yes":
                important_indices.add(int(m.group(1)) - 1)

        if not important_indices:
            logger.warning("LLM 核校未解析到 yes，保留全部")
            return topic_items

        result = [topic_items[i] for i in sorted(important_indices) if i < len(topic_items)]
        logger.info(f"LLM 核校: {len(topic_items)} → {len(result)} 条 important")
        return result

    except Exception as e:
        logger.error(f"LLM 核校失败: {e}，保留全部")
        return topic_items


# ── LLM 二次过滤 ──

def call_llm_second_filter(topic_items: list, llm_model="", base_url="", api_key="") -> list:
    """LLM 二次过滤：从累积话题中精选"""
    import openai

    if len(topic_items) <= 1:
        return topic_items

    if not api_key:
        logger.warning("未找到 API_KEY，跳过 LLM 二次过滤")
        return topic_items

    if not llm_model or not base_url:
        logger.warning("未配置 llm_model 或 llm_base_url，跳过 LLM 二次过滤")
        return topic_items

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    topic_lines = []
    for i, n in enumerate(topic_items):
        cat = n.get("category", "")
        topic_lines.append(f"{i+1}. {n['word']} | 分类:{cat} | 热度:{n.get('hot_str','')}")

    topics_text = "\n".join(topic_lines)
    prompt_template = load_prompt("second_filter_prompt")
    prompt = prompt_template.format(topics_text=topics_text)

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
            logger.warning("LLM 二次过滤返回为空，使用全量")
            return topic_items

        result_text = content.strip()
        logger.info(f"LLM 二次过滤完成，响应: {result_text[:200]}")

        selected_indices = []
        for line in result_text.split("\n"):
            line = line.strip().rstrip(".")
            if line and line.isdigit():
                idx = int(line)
                if 1 <= idx <= len(topic_items):
                    selected_indices.append(idx - 1)

        if not selected_indices:
            logger.warning("LLM 二次过滤未解析到有效序号，使用全量")
            return topic_items

        selected = [topic_items[i] for i in selected_indices]
        logger.info(f"LLM 二次过滤: {len(topic_items)} → {len(selected)} 条")
        return selected

    except Exception as e:
        logger.error(f"LLM 二次过滤失败: {e}，使用全量")
        return topic_items


# ── 飞书 ──

@retry(times=3, delay=2, backoff=2, logger=logger)
def _get_feishu_token(app_id: str, app_secret: str) -> str:
    """获取飞书 tenant_access_token"""
    resp = _http_sess.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"获取飞书 token 失败: code={result.get('code')} msg={result.get('msg')}")
    return result["tenant_access_token"]


def _send_feishu_message(token: str, chat_id: str, payload: dict):
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = _http_sess.post(url, headers=headers, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"code={result.get('code')} msg={result.get('msg')}")
    return result["data"]["message_id"]


def _send_feishu_text(token: str, chat_id: str, text: str):
    """发送飞书纯文本消息"""
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    resp = _http_sess.post(url, headers=headers, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"code={result.get('code')} msg={result.get('msg')}")
    return result["data"]["message_id"]


def should_show_feedback_hint() -> bool:
    """检查是否需要显示反馈提示（用户从未反馈过则显示）"""
    if not TASTED_TOPICS_PATH.exists():
        return True
    with open(TASTED_TOPICS_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return False
    return True


def _build_feishu_card(date_str: str, new_topics: list, duplicate_topics: list, show_hint: bool = True) -> dict:
    """构建飞书卡片 content 结构，支持折叠区和热度变化"""
    elements = []
    
    # 新热点区域
    for i, n in enumerate(new_topics):
        cat = n.get("category", "")
        hot = n.get("hot_str", "")
        word = n["word"]
        summary = n.get("summary", "")

        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{i+1}.** {word}  `{cat}`  {hot}"}
        })

        # 摘要副行
        if summary:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"　└ {summary}"}
            })

        elements.append({"tag": "hr"})
    
    # 重复热点折叠区（如果有）
    if duplicate_topics:
        duplicate_elements = []
        for i, n in enumerate(duplicate_topics):
            cat = n.get("category", "")
            hot = n.get("hot_str", "")
            word = n["word"]
            change_str = n.get("hot_change", {}).get("change_str", "")
            summary = n.get("summary", "")

            # 热度变化颜色标记
            change = n.get("hot_change", {}).get("change", 0)
            if change > 0:
                change_display = f"<font color='red'>{change_str}</font>"
            elif change < 0:
                change_display = f"<font color='green'>{change_str}</font>"
            else:
                change_display = ""

            content = f"**{i+1}.** {word}  `{cat}`  {hot} {change_display}"
            duplicate_elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": content.strip()}
            })

            # 摘要副行
            if summary:
                duplicate_elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"　└ {summary}"}
                })
        
        elements.append({
            "tag": "collapsible",
            "header": {"tag": "plain_text", "content": "今日已推送热点"},
            "collapsed": True,
            "elements": duplicate_elements,
        })
    
    # 底部提示（仅首次推送显示）
    if show_hint:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "💬 回复序号评价本次推送，如\"1,3感兴趣\""}
        })
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"🕐 {date_str}"}
    })
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "微博热榜"},
            "template": "red"
        },
        "elements": elements,
    }


def send_push_card(date_str: str, new_topics: list, duplicate_topics: list, app_id: str, app_secret: str, chat_id: str, show_hint: bool = True):
    """发送飞书卡片消息"""
    token = _get_feishu_token(app_id, app_secret)
    card = _build_feishu_card(date_str, new_topics, duplicate_topics, show_hint)

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }

    retry_times = BASE_CONFIG["feishu"]["retry_times"]
    retry_delay = BASE_CONFIG["feishu"]["retry_delay"]
    send_with_retry = retry(times=retry_times, delay=retry_delay, logger=logger)(_send_feishu_message)
    send_with_retry(token, chat_id, payload)
    logger.info("飞书推送卡片发送成功")


def append_pushed_topics(pushed_items: list, cached_count: int):
    """追加推送记录到 pushed_topics.jsonl"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    record = {
        "ts": now.isoformat(),
        "total_fetched": cached_count,
        "topics": [
            {
                "word": n["word"],
                "category": n.get("category", ""),
                "raw_hot": n.get("raw_hot", 0),
                "hot_str": n.get("hot_str", ""),
            }
            for n in pushed_items
        ],
    }

    with open(PUSHED_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"pushed_topics.jsonl 已追加: {len(pushed_items)} 条")


# ── 主流程 ──

def main():
    logger.info("=== 开始推送 ===")

    load_llm_env()
    load_feishu_env()
    llm_model, llm_base_url, llm_api_key = get_llm_creds()
    app_id, app_secret, chat_id = get_feishu_creds()

    cycles, cached = read_cached_data()

    # 从 meta 收集 failed cycle 的候选话题
    failed_candidates = []
    for c in cycles:
        if c.get("llm") == "failed" and "candidates" in c:
            for cand in c["candidates"]:
                cand["cycle_ts"] = c["ts"]
                failed_candidates.append(cand)

    if not cached and not failed_candidates:
        logger.info("缓存为空，无话题可推送")
        return

    failed_ts = {c["ts"] for c in cycles if c.get("llm") == "failed"}

    if failed_candidates:
        logger.info(f"发现 {len(failed_ts)} 个 LLM 失败轮次，合并补跑 judge ({len(failed_candidates)} 条候选)")
        judged_failed = call_llm_judge(failed_candidates, llm_model, llm_base_url, llm_api_key)
        cached = cached + judged_failed
        logger.info(f"failed 话题核校: {len(failed_candidates)} → {len(judged_failed)} 条")

    deduped = deduplicate_topics(cached)

    if len(cycles) <= 1 and not failed_candidates:
        logger.info(f"仅一次抓取 ({len(deduped)} 条)，跳过 LLM 二次过滤")
        selected = deduped
    else:
        selected = call_llm_second_filter(deduped, llm_model, llm_base_url, llm_api_key)

    today_pushed = load_today_pushed_topics()
    new_topics, duplicate_topics = split_new_and_duplicate(selected, today_pushed)
    
    logger.info(f"跨周期去重: 新热点 {len(new_topics)} 条, 重复热点 {len(duplicate_topics)} 条")

    now = datetime.now()
    time_str = now.strftime("%Y年%m月%d日 %H:%M")

    if not app_id or not app_secret or not chat_id:
        logger.error("飞书凭据不完整，需要 feishu_app_id, feishu_app_secret, feishu_chat_id")
        sys.exit(1)

    if not new_topics and duplicate_topics:
        logger.info("本次推送候选全部为今日已推送热点，发送文本通知")
        token = _get_feishu_token(app_id, app_secret)
        _send_feishu_text(token, chat_id, "当前时段无新增微博热点")
        append_pushed_topics(duplicate_topics, len(cached))
        clear_cached_data()
        logger.info(f"=== 推送完成: 全重复，发送文本通知 ===")
        return

    show_hint = should_show_feedback_hint()
    send_push_card(time_str, new_topics, duplicate_topics, app_id, app_secret, chat_id, show_hint)
    append_pushed_topics(new_topics + duplicate_topics, len(cached))
    clear_cached_data()

    logger.info(f"=== 推送完成: 缓存 {len(cached)} → 去重 {len(deduped)} → 新热点 {len(new_topics)} + 重复 {len(duplicate_topics)} ===")


if __name__ == "__main__":
    main()
