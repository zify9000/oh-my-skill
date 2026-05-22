"""公共工具：配置、日志、微信读书API、飞书消息"""
import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
ENV_DIR = SCRIPT_DIR / "env"
LOG_DIR = SCRIPT_DIR / "log"

BASE_CONFIG_PATH = SCRIPT_DIR / "config" / "base.yaml"
STATE_FILE = DATA_DIR / "wechat_last_check.json"
CREDENTIALS_PATH = ENV_DIR / ".weread.env"
FEISHU_ENV_PATH = ENV_DIR / ".feishu.env"

GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.3"
MP_BOOK_ID_PREFIX = "MP_WXS_"


def setup_logging(name: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_level = os.environ.get("WEIXIN_FOLLOW_LOG_LEVEL", "INFO").upper()
    log_file = LOG_DIR / f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    return logging.getLogger(name)


def load_env(path: Path):
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_base_config() -> dict:
    import yaml
    if not BASE_CONFIG_PATH.exists():
        return {}
    with open(BASE_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def load_target_config() -> set | None:
    cfg = load_base_config()
    names = cfg.get("follow", [])
    return set(names) if names else None


def format_timestamp(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


# ── 微信读书 API ──

def call_weread_api(api_name: str, api_key: str, **params) -> dict:
    body = {"api_name": api_name, "skill_version": SKILL_VERSION, **params}
    resp = requests.post(
        GATEWAY_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"API 错误: {data.get('errmsg', '未知错误')}")
    return data


def get_api_key() -> str:
    key = os.environ.get("weread_api_key", "")
    if not key:
        raise RuntimeError("weread_api_key 未设置")
    return key


# ── 飞书 ──

def get_feishu_token(app_id: str, app_secret: str) -> str:
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    return resp.json()["tenant_access_token"]


def send_feishu_message(token: str, chat_id: str, payload: dict):
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"飞书发送失败: code={result.get('code')} msg={result.get('msg')}")
