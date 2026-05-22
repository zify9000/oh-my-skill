"""公共工具：配置、日志、B站API、飞书消息"""
import os
import sys
import json
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from curl_cffi import requests as curl

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
ENV_DIR = SCRIPT_DIR / "env"
LOG_DIR = SCRIPT_DIR / "log"

BASE_CONFIG_PATH = SCRIPT_DIR / "config" / "base.yaml"
STATE_FILE = DATA_DIR / "bili_last_check.json"
CREDENTIALS_PATH = ENV_DIR / ".bili.env"
FEISHU_ENV_PATH = ENV_DIR / ".feishu.env"

BILI_API = "https://api.bilibili.com"
SEARCH_URL = f"{BILI_API}/x/web-interface/search/type"
SPACE_INFO_URL = f"{BILI_API}/x/space/acc/info"
ARC_SEARCH_URL = f"{BILI_API}/x/space/arc/search"
DYNAMIC_FEED_URL = f"{BILI_API}/x/polymer/web-dynamic/v1/feed/space"

TRACKED_DYNAMIC_TYPES = {"DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]

_bili_session = None
_wbi_keys = None


def setup_logging(name: str) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_level = os.environ.get("BILI_FOLLOW_LOG_LEVEL", "INFO").upper()
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


def load_target_config() -> list | None:
    cfg = load_base_config()
    names = cfg.get("follow", [])
    return names if names else None


def format_timestamp(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


# ── WBI 签名 ──

def _fetch_wbi_keys() -> tuple[str, str]:
    """从 B站 nav 接口获取 WBI 签名密钥（每日轮换，缓存到内存）"""
    global _wbi_keys
    if _wbi_keys is not None:
        return _wbi_keys
    resp = requests.get(
        f"{BILI_API}/x/web-interface/nav",
        headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
        timeout=10,
    )
    data = resp.json().get("data", {}).get("wbi_img", {})
    img_key = data.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
    sub_key = data.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
    _wbi_keys = (img_key, sub_key)
    return _wbi_keys


def _get_mixin_key(raw: str) -> str:
    from functools import reduce
    return reduce(lambda s, i: s + raw[i], MIXIN_KEY_ENC_TAB, "")[:32]


def sign_wbi(params: dict) -> dict:
    """为请求参数附加 w_rid 和 wts（WBI 签名）"""
    from hashlib import md5
    from urllib.parse import urlencode

    img_key, sub_key = _fetch_wbi_keys()
    mixin_key = _get_mixin_key(img_key + sub_key)

    signed = dict(params)
    signed["wts"] = int(time.time())
    signed = dict(sorted(signed.items()))
    # 过滤 !'()* 字符
    filtered = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in signed.items()}
    query = urlencode(filtered)
    signed["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
    return signed


# ── B站 API Session ──

def _get_session():
    global _bili_session
    if _bili_session is None:
        cookies = {}
        sessdata = os.environ.get("bili_sessdata", "")
        jct = os.environ.get("bili_jct", "")
        buvid3 = os.environ.get("bili_buvid3", "")
        if sessdata:
            cookies["SESSDATA"] = sessdata
        if jct:
            cookies["bili_jct"] = jct
        if buvid3:
            cookies["buvid3"] = buvid3
        if not cookies:
            cookies = {"buvid3": "infoc", "buvid4": "infoc"}

        _bili_session = curl.Session()
        _bili_session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.bilibili.com",
        })
        _bili_session.cookies.update(cookies)
    return _bili_session


def api_call(url: str, params: dict, timeout: int = 15, referer: str | None = None) -> dict:
    s = _get_session()
    h = {"Referer": referer} if referer else {}
    for _ in range(3):
        resp = s.get(url, params=params, headers=h, impersonate="chrome131", timeout=timeout)
        if resp.status_code in (412, 429):
            time.sleep(3 + random.random() * 2)
            continue
        resp.raise_for_status()
        data = resp.json()
        code = data.get("code", -1)
        if code == -799:
            time.sleep(5 + random.random() * 3)
            continue
        if code == -412:
            time.sleep(3 + random.random() * 2)
            continue
        if code != 0:
            raise RuntimeError(f"B站API错误: code={code} message={data.get('message', 'unknown')}")
        return data
    raise RuntimeError(f"B站API请求被拒: url={url}")


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
