"""公共工具：配置加载、日志、格式化"""
import os
import sys
import time as time_module
import logging
from datetime import datetime
from pathlib import Path

os.environ["TZ"] = "Asia/Shanghai"
time_module.tzset()

SCRIPT_DIR = Path(__file__).parent
CONFIG_DIR = SCRIPT_DIR / "config"
DATA_DIR = SCRIPT_DIR / "data"
LOG_DIR = SCRIPT_DIR / "log"

BASE_CONFIG_PATH = CONFIG_DIR / "base.yaml"
RULE_CONFIG_PATH = CONFIG_DIR / "rule.yaml"
PROMPT_PATH = CONFIG_DIR / "prompt.yaml"
ALL_TOPICS_PATH = DATA_DIR / "all_topics.jsonl"
RULE_CHECKED_TOPICS_PATH = DATA_DIR / "ruleChecked_topics.jsonl"
PUSHED_TOPICS_PATH = DATA_DIR / "pushed_topics.jsonl"
CATEGORY_STORE_PATH = DATA_DIR / "topic_category.json"
CACHED_FETCH_META_PATH = DATA_DIR / "cached_fetch_meta.jsonl"
CACHED_FETCH_TOPICS_PATH = DATA_DIR / "cached_fetch_topics.jsonl"
# 以下路径已废弃，保留向后兼容
CACHED_TOPICS_PATH = DATA_DIR / "cached_topics.jsonl"
INITIALIZED_PATH = CONFIG_DIR / ".initialized"


def setup_logging(name: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_level = os.environ.get("WEIBO_HOT_NEWS_LOG_LEVEL", "INFO").upper()
    log_file = LOG_DIR / f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(name)


def _load_env_file(filename: str):
    """加载 env 文件中的 key=value 到 os.environ"""
    env_path = SCRIPT_DIR / "env" / filename
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


def load_llm_env():
    """加载 .llm.env"""
    _load_env_file(".llm.env")


def load_feishu_env():
    """加载 .feishu.env"""
    _load_env_file(".feishu.env")


def get_llm_creds() -> tuple:
    """从环境变量读取 LLM 凭据"""
    return (
        os.environ.get("llm_model", ""),
        os.environ.get("llm_base_url", ""),
        os.environ.get("llm_api_key", ""),
    )


def get_feishu_creds() -> tuple:
    """从环境变量读取飞书凭据"""
    return (
        os.environ.get("feishu_app_id", ""),
        os.environ.get("feishu_app_secret", ""),
        os.environ.get("feishu_chat_id", ""),
    )


def load_base_config() -> dict:
    import yaml

    cfg = {}
    if BASE_CONFIG_PATH.exists():
        with open(BASE_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}

    return cfg


def load_rule_config() -> dict:
    import yaml

    if not RULE_CONFIG_PATH.exists():
        return {"category_exclude": [], "keyword_recall": []}
    with open(RULE_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def load_prompt(key: str) -> str:
    import yaml

    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"prompt.yaml 不存在: {PROMPT_PATH}")
    with open(PROMPT_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data[key]


def load_user_prefs() -> dict | None:
    """加载用户偏好数据（从 .initialized 文件），未初始化返回 None"""
    if not INITIALIZED_PATH.exists():
        return None
    import json
    with open(INITIALIZED_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_judge_prompt() -> str:
    """加载 judge_prompt 模板并填充用户偏好变量，返回仅剩 {topics_text}/{topics_count} 的模板"""
    template = load_prompt("judge_prompt")
    prefs = load_user_prefs()
    if not prefs:
        # 未初始化时用空值填充偏好变量，避免 format 报错
        return template.format(
            keywords="未设置",
            liked_categories="未设置",
            disliked_categories="未设置",
            recall_keywords="无",
            yes_criteria="（未设置判断标准，请先运行初始化）",
            no_criteria="（未设置判断标准，请先运行初始化）",
            topics_text="{topics_text}",
            topics_count="{topics_count}",
        )
    return template.format(
        keywords="/".join(prefs.get("keywords", [])),
        liked_categories="、".join(prefs.get("liked_categories", [])),
        disliked_categories="、".join(prefs.get("disliked_categories", [])),
        recall_keywords="、".join(prefs.get("recall_keywords", [])),
        yes_criteria=prefs.get("yes_criteria", ""),
        no_criteria=prefs.get("no_criteria", ""),
        topics_text="{topics_text}",
        topics_count="{topics_count}",
    )


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
                    logging.getLogger("common").warning(f"第{attempt}次失败: {e}，{current_delay}秒后重试")
                    time_module.sleep(current_delay)
                    current_delay *= backoff
            return None
        return wrapper
    return decorator


def format_hotness(raw_hot) -> str:
    if raw_hot >= 10_000_000:
        return f"{raw_hot / 10_000_000:.1f}千万"
    elif raw_hot >= 10_000:
        return f"{raw_hot / 10_000:.1f}万"
    elif raw_hot >= 1000:
        return f"{raw_hot / 1000:.1f}千"
    return str(raw_hot)


def clean_word(w: str) -> str:
    return w.strip("#") if w else ""
