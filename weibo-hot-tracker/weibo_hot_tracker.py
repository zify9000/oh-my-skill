#!/opt/hermes/.venv/bin/python3
"""
微博热榜追踪脚本，筛选政治/经济以及科技类热点话题，推送飞书
"""

import sys
import json
import re
import os
import time as time_module
import logging
from datetime import datetime, timezone
from pathlib import Path
import requests as req
import curl_cffi

os.environ["TZ"] = "Asia/Shanghai"
time_module.tzset()

# curl_cffi会话（用于模拟浏览器）和requests会话
_http_sess = curl_cffi.Session(impersonate="chrome131")
sess = req.Session()


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
    """加载配置文件，从 config.yaml 读取，环境变量优先"""
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
                    for key in global_cfg:
                        if key in cfg and isinstance(cfg[key], dict):
                            cfg[key].update(global_cfg[key])
                        else:
                            cfg[key] = global_cfg[key]
                logger.info(f"已加载全局配置文件: {global_cfg_path}")
        except Exception as e:
            logger.warning(f"加载全局配置文件失败 {global_cfg_path}: {e}")
    
    feishu_chat_id = os.environ.get("FEISHU_HOME_CHANNEL")
    if feishu_chat_id:
        if "feishu" not in cfg:
            cfg["feishu"] = {}
        cfg["feishu"]["chat_id"] = feishu_chat_id
    
    return cfg


CONFIG = load_config()

# 报告保存目录
REPORT_DIR = Path(__file__).parent / "skill_result"

# 过滤规则常量（转为集合提高查找效率）
EXCLUDE_CATS = set(CONFIG["filter"]["exclude_categories"])
STARKW_THIRD = set(CONFIG["filter"]["star_keywords"]["level_3"])
STARKW_SECOND = set(CONFIG["filter"]["star_keywords"]["level_2"])


def is_interested(category=None, field_tag=None, word_scheme=None):
    """
    判断话题是否感兴趣（规则匹配）
    
    Args:
        category: 话题分类
        field_tag: 领域标签
        word_scheme: 话题关键词
    
    Returns:
        3: 重点话题（level_3 关键词匹配）
        2: 要闻话题（level_2 关键词匹配）
        0: 不感兴趣（排除分类匹配或无匹配）
    """
    text = " ".join(filter(None, [category or "", field_tag or "", word_scheme or ""]))
    
    if category and any(kw in category for kw in EXCLUDE_CATS):
        return 0
    if field_tag and any(kw in field_tag for kw in EXCLUDE_CATS):
        return 0
    
    for kw in STARKW_THIRD:
        if kw in text:
            return 3
    
    for kw in STARKW_SECOND:
        if kw in text:
            return 2
    
    return 0


def retry(times=3, delay=5, backoff=2):
    """
    重试装饰器，用于网络请求失败时自动重试
    
    Args:
        times: 最大重试次数
        delay: 初始重试延迟（秒）
        backoff: 延迟倍增系数
    
    Returns:
        装饰后的函数，失败时自动重试
    """
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
    """
    格式化热度数值为可读格式
    
    Args:
        raw_hot: 原始热度数值
    
    Returns:
        格式化后的热度字符串（如 "1.2千万", "5.6万", "3.2千"）
    """
    if raw_hot >= 10_000_000:
        return f"{raw_hot/10_000_000:.1f}千万"
    elif raw_hot >= 10_000:
        return f"{raw_hot/10_000:.1f}万"
    elif raw_hot >= 1000:
        return f"{raw_hot/1000:.1f}千"
    return str(raw_hot)


def call_llm_judge(topics: list) -> list:
    """
    调用LLM评估话题重要性
    
    Args:
        topics: 待评估的话题列表
    
    Returns:
        带有star字段的话题列表，star=3表示重点，star=2表示要闻
        如果调用失败或缺少API密钥，返回None（将使用规则兜底）
    """
    import openai
    
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
    
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    if cfg_path.exists():
        import yaml
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        model_cfg = cfg.get("model", {})
        raw_key = model_cfg.get("api_key", "")
        if raw_key.startswith("${") and raw_key.endswith("}"):
            env_var = raw_key[2:-1]
            api_key = os.environ.get(env_var, "")
        else:
            api_key = raw_key
        llm_model = model_cfg.get("default", CONFIG["llm"]["model"])
        base_url = model_cfg.get("base_url", CONFIG["llm"]["base_url"])
    else:
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        llm_model = CONFIG["llm"]["model"]
        base_url = os.environ.get("MINIMAX_BASE_URL", CONFIG["llm"]["base_url"])
    
    if not api_key:
        logger.warning("未找到 API_KEY，跳过 LLM 评估，使用规则")
        return None
    
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
        star_map = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):[【\[【]?(重点|要闻)[】\]]?", line)
            if m:
                idx = int(m.group(1))
                star = 3 if m.group(2) == "重点" else 2
                star_map[idx] = star
        
        logger.info(f"解析出 {len(star_map)} 条星级标注")
        
        for i, t in enumerate(topics):
            t["star"] = star_map.get(i + 1, 2)
            t["reason"] = ""
        
        return topics
    
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}，使用规则评估")
        return None


def _send_feishu_message(token: str, chat_id: str, payload: dict):
    """
    发送消息到飞书（内部方法）
    
    Args:
        token: 飞书租户访问令牌
        chat_id: 目标聊天ID
        payload: 消息内容
    
    Returns:
        True表示发送成功
    
    Raises:
        Exception: 发送失败时抛出异常
    """
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = _http_sess.post(url, headers=headers, json=payload, timeout=15)
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"code={result.get('code')} msg={result.get('msg')}")
    return True


def send_to_feishu_card(date_str: str, topics: list[dict]):
    """
    发送微博热榜卡片到飞书
    
    Args:
        date_str: 日期时间字符串
        topics: 话题列表（仅包含★★★重点话题）
    
    Returns:
        True表示发送成功，False表示失败
    """
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        logger.error("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return False
    
    chat_id = CONFIG["feishu"]["chat_id"]
    
    try:
        auth_resp = _http_sess.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10
        )
        token = auth_resp.json()["tenant_access_token"]
    except Exception as e:
        logger.error(f"Feishu Auth 失败: {e}")
        return False
    
    elements = []
    
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**共 {len(topics)} 条★★★重点**，政经科技类"
        }
    })
    
    elements.append({"tag": "hr"})
    
    for t in topics:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"🔴 {t['word']}"
            }
        })
    
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"🕐 {date_str}"
        }
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
    
    retry_times = CONFIG["feishu"]["retry_times"]
    retry_delay = CONFIG["feishu"]["retry_delay"]
    
    try:
        send_with_retry = retry(times=retry_times, delay=retry_delay)(_send_feishu_message)
        send_with_retry(token, chat_id, payload)
        logger.info("Feishu 卡片发送成功")
        return True
    except Exception as e:
        logger.error(f"Feishu 发送失败: {e}")
        return False


def save_report(report_text, topics, all_topics, feishu_ok):
    """
    保存报告到本地文件
    
    Args:
        report_text: 报告文本内容
        topics: 发送的话题列表
        all_topics: 抓取的全部话题列表
        feishu_ok: 飞书发送是否成功
    """
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


def main():
    """
    主函数：微博热榜追踪主流程
    
    流程：
    1. 抓取微博热榜数据
    2. 预筛选：排除娱乐、明星等非目标分类
    3. 调用LLM评估话题重要性（失败时使用规则兜底）
    4. 筛选★★★重点话题
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
                   for kw in EXCLUDE_CATS)
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
        three_star = [t for t in prefiltered if is_interested(t["category"], t["field_tag"], t["word"]) == 3]
        two_star = [t for t in prefiltered if is_interested(t["category"], t["field_tag"], t["word"]) == 2]
        for t in three_star: t["star"] = 3
        for t in two_star: t["star"] = 2
    else:
        three_star = [t for t in judged if t.get("star") == 3]
        two_star = [t for t in judged if t.get("star") == 2]
    
    logger.info(f"★★★重点 {len(three_star)} 条，★★要闻 {len(two_star)} 条")
    
    lines = [f"📊 微博热榜 {time_str}（政经科技）"]
    
    if three_star:
        lines.append("【★★★ 重点】")
        for t in three_star:
            lines.append(f"🔴 {t['word']}")
    
    if two_star:
        lines.append("【★★ 要闻】")
        for t in two_star:
            lines.append(f"🟡 {t['word']}")
    
    report_text = "\n".join(lines)
    
    if three_star:
        feishu_ok = send_to_feishu_card(time_str, three_star)
    else:
        feishu_ok = False
        logger.info("无★★★重点话题，跳过飞书推送")
    
    save_report(
        report_text,
        topics=[t["word"] for t in three_star],
        all_topics=[t["word"] for t in all_topics],
        feishu_ok=feishu_ok
    )
    
    if not feishu_ok and three_star:
        logger.error("飞书推送失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
