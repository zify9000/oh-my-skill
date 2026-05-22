"""微博热榜推送：读缓存 → 去重 → LLM二次过滤 → 推送飞书卡片 → 清空缓存"""
import sys
import json
import os
import argparse
import fcntl
from datetime import datetime

import curl_cffi

from common import (
    SCRIPT_DIR, DATA_DIR, CACHED_TOPICS_PATH, PUSHED_TOPICS_PATH,
    setup_logging, load_base_config, load_prompt, retry, resolve_llm_creds,
)

logger = setup_logging("push")
BASE_CONFIG = load_base_config()
_http_sess = curl_cffi.Session(impersonate="chrome131")


# ── 缓存读写 ──

def read_cached_topics() -> list:
    """读取 cached_topics.jsonl，返回话题列表"""
    if not CACHED_TOPICS_PATH.exists():
        logger.info("缓存文件不存在，无待推送话题")
        return []
    topics = []
    with open(CACHED_TOPICS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    topics.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"跳过无效缓存行: {line[:80]}")
    logger.info(f"从缓存读取 {len(topics)} 条话题")
    return topics


def deduplicate_topics(topics: list) -> list:
    """按 word 去重，保留 cached_at 最新、raw_hot 最高的一条"""
    seen = {}
    for t in topics:
        word = t["word"]
        if word in seen:
            existing = seen[word]
            if t["cached_at"] > existing["cached_at"]:
                seen[word] = t
            elif t["cached_at"] == existing["cached_at"] and t.get("raw_hot", 0) > existing.get("raw_hot", 0):
                seen[word] = t
        else:
            seen[word] = t

    result = sorted(seen.values(), key=lambda x: x.get("raw_hot", 0), reverse=True)
    if len(topics) != len(result):
        logger.info(f"去重: {len(topics)} → {len(result)} 条")
    return result


def clear_cached_topics():
    """清空缓存文件"""
    if CACHED_TOPICS_PATH.exists():
        CACHED_TOPICS_PATH.unlink()
        logger.info("cached_topics.jsonl 已清空")


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
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=BASE_CONFIG["llm"]["temperature"],
            max_tokens=BASE_CONFIG["llm"]["max_tokens"],
            timeout=BASE_CONFIG["llm"]["timeout"],
        )
        content = resp.choices[0].message.content
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

def _get_feishu_token(app_id: str, app_secret: str) -> str:
    resp = _http_sess.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    return resp.json()["tenant_access_token"]


def _send_feishu_message(token: str, chat_id: str, payload: dict):
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = _http_sess.post(url, headers=headers, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"code={result.get('code')} msg={result.get('msg')}")
    return result["data"]["message_id"]


def _build_feishu_card(date_str: str, topic_items: list) -> dict:
    """构建飞书卡片 content 结构"""
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

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "微博热榜"},
            "template": "red"
        },
        "elements": elements,
    }


def send_push_card(date_str: str, topic_items: list, app_id: str, app_secret: str, chat_id: str):
    """发送飞书卡片消息"""
    token = _get_feishu_token(app_id, app_secret)
    card = _build_feishu_card(date_str, topic_items)

    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }

    retry_times = BASE_CONFIG["feishu"]["retry_times"]
    retry_delay = BASE_CONFIG["feishu"]["retry_delay"]
    send_with_retry = retry(times=retry_times, delay=retry_delay)(_send_feishu_message)
    send_with_retry(token, chat_id, payload)
    logger.info("飞书推送卡片发送成功")


def append_pushed_topics(pushed_items: list, cached_count: int):
    """追加推送记录到 pushed_topics.jsonl"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    record = {
        "ts": now.isoformat(),
        "total_fetched": cached_count,
        "topics": [{"word": n["word"], "category": n.get("category", "")} for n in pushed_items],
    }

    with open(PUSHED_TOPICS_PATH, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"pushed_topics.jsonl 已追加: {len(pushed_items)} 条")


# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser(description="微博热榜推送")
    parser.add_argument("--llm-model", default="", help="agent 模式：LLM 模型名")
    parser.add_argument("--llm-base-url", default="", help="agent 模式：LLM API 地址")
    parser.add_argument("--llm-api-key", default="", help="agent 模式：LLM API 密钥")
    parser.add_argument("--feishu-app-id", default="", help="agent 模式：飞书应用 ID")
    parser.add_argument("--feishu-app-secret", default="", help="agent 模式：飞书应用密钥")
    parser.add_argument("--feishu-chat-id", default="", help="agent 模式：飞书群聊 ID")
    args = parser.parse_args()

    logger.info("=== 开始推送 ===")

    cached = read_cached_topics()
    if not cached:
        logger.info("缓存为空，无话题可推送")
        return

    deduped = deduplicate_topics(cached)

    # 只抓取了一次则跳过二次过滤
    timestamps = {t.get("cached_at", "") for t in cached}
    if len(timestamps) == 1:
        logger.info(f"仅一次抓取 ({len(deduped)} 条)，跳过 LLM 二次过滤")
        selected = deduped
    else:
        llm_model, llm_base_url, llm_api_key = resolve_llm_creds(
            BASE_CONFIG, args.llm_model, args.llm_base_url, args.llm_api_key
        )
        selected = call_llm_second_filter(deduped, llm_model, llm_base_url, llm_api_key)

    now = datetime.now()
    time_str = now.strftime("%Y年%m月%d日 %H:%M")

    cred_source = BASE_CONFIG.get("feishu_credential_source", "env")

    if cred_source == "agent":
        app_id = args.feishu_app_id
        app_secret = args.feishu_app_secret
        chat_id = args.feishu_chat_id
    else:
        # 加载 env/.feishu.env
        feishu_env_path = SCRIPT_DIR / "env" / ".feishu.env"
        if feishu_env_path.exists():
            with open(feishu_env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")
            logger.info(f"已加载 {feishu_env_path}")
        app_id = os.environ.get("feishu_app_id", "")
        app_secret = os.environ.get("feishu_app_secret", "")
        chat_id = os.environ.get("feishu_chat_id", "")

    if not app_id or not app_secret or not chat_id:
        logger.error("飞书凭据不完整，需要 feishu_app_id, feishu_app_secret, feishu_chat_id")
        sys.exit(1)

    send_push_card(time_str, selected, app_id, app_secret, chat_id)
    append_pushed_topics(selected, len(cached))
    clear_cached_topics()

    logger.info(f"=== 推送完成: 缓存 {len(cached)} → 去重 {len(deduped)} → 推送 {len(selected)} ===")


if __name__ == "__main__":
    main()
