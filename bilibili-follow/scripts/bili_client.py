"""B站API客户端：封装请求、限流、重试、WBI签名、代理"""

import json
import os
import time
import random
import logging
import threading
from hashlib import md5
from functools import reduce
from pathlib import Path
from urllib.parse import urlencode

import requests
from curl_cffi import requests as curl

from common import DATA_DIR, CREDENTIALS_PATH, load_env

# ── 常量 ──

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

WBI_CACHE_PATH = DATA_DIR / ".wbi_keys.json"
WBI_CACHE_TTL = 6 * 3600  # 6小时


# ── 令牌桶限流器 ──

class TokenBucket:
    """线程安全的令牌桶，控制API请求速率"""

    def __init__(self, rate: float, burst: int = 1):
        """
        rate: 每秒允许的请求数（如 0.2 = 每5秒1个请求）
        burst: 桶容量，允许短时突发
        """
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 60.0) -> bool:
        """等待直到获取一个令牌，超时返回False"""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                self._last_refill = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

                # 计算需要等待的时间
                wait = (1.0 - self._tokens) / self.rate

            if time.monotonic() + wait > deadline:
                return False
            time.sleep(wait)


# ── WBI签名 ──

def _get_mixin_key(raw: str) -> str:
    return reduce(lambda s, i: s + raw[i], MIXIN_KEY_ENC_TAB, "")[:32]


def _load_wbi_cache() -> tuple[str, str] | None:
    """从文件加载WBI密钥缓存，过期返回None"""
    if not WBI_CACHE_PATH.exists():
        return None
    try:
        with open(WBI_CACHE_PATH) as f:
            cache = json.load(f)
        if time.time() - cache.get("ts", 0) < WBI_CACHE_TTL:
            return cache["img_key"], cache["sub_key"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_wbi_cache(img_key: str, sub_key: str):
    """持久化WBI密钥到文件"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(WBI_CACHE_PATH, "w") as f:
        json.dump({"img_key": img_key, "sub_key": sub_key, "ts": time.time()}, f)


# ── BiliClient ──

class BiliClient:
    """B站API客户端，封装限流、重试、签名、代理"""

    def __init__(self, config: dict | None = None):
        """
        config 来自 base.yaml 的 bili 段，支持字段：
          request_interval: 请求间隔秒数（默认5）
          retry_max: 最大重试次数（默认3）
          retry_base_wait: 重试基础等待秒数（默认10）
          proxy: 代理地址，如 "socks5://127.0.0.1:1080" 或 "http://127.0.0.1:7890"
        """
        cfg = config or {}
        interval = cfg.get("request_interval", 5)
        # rate = 1/interval，burst=1 表示不允许突发
        self._bucket = TokenBucket(rate=1.0 / interval, burst=1)
        self._retry_max = cfg.get("retry_max", 3)
        self._retry_base_wait = cfg.get("retry_base_wait", 10)
        self._proxy = cfg.get("proxy", "")
        self._session: curl.Session | None = None
        self._wbi_keys: tuple[str, str] | None = None
        self._logger = logging.getLogger("bili_client")

    # ── Session 管理 ──

    def _get_session(self) -> curl.Session:
        if self._session is not None:
            return self._session

        load_env(CREDENTIALS_PATH)
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

        self._session = curl.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.bilibili.com",
        })
        self._session.cookies.update(cookies)
        return self._session

    def _get_proxies(self) -> dict | None:
        """构建代理配置，供 curl_cffi 使用"""
        if not self._proxy:
            return None
        # curl_cffi 的 proxies 格式
        return {"https": self._proxy, "http": self._proxy}

    # ── WBI签名 ──

    def _fetch_wbi_keys(self) -> tuple[str, str]:
        """获取WBI签名密钥，优先文件缓存 → 内存缓存 → API获取"""
        # 1. 文件缓存
        cached = _load_wbi_cache()
        if cached:
            self._wbi_keys = cached
            return cached

        # 2. 内存缓存
        if self._wbi_keys is not None:
            return self._wbi_keys

        # 3. API获取
        self._bucket.acquire()
        resp = requests.get(
            f"{BILI_API}/x/web-interface/nav",
            headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
            proxies=self._get_proxies(),
            timeout=10,
        )
        data = resp.json().get("data", {}).get("wbi_img", {})
        img_key = data.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
        sub_key = data.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
        self._wbi_keys = (img_key, sub_key)
        _save_wbi_cache(img_key, sub_key)
        return self._wbi_keys

    def sign_wbi(self, params: dict) -> dict:
        """为请求参数附加 w_rid 和 wts（WBI签名）"""
        img_key, sub_key = self._fetch_wbi_keys()
        mixin_key = _get_mixin_key(img_key + sub_key)

        signed = dict(params)
        signed["wts"] = int(time.time())
        signed = dict(sorted(signed.items()))
        filtered = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in signed.items()}
        query = urlencode(filtered)
        signed["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
        return signed

    # ── 请求核心 ──

    def api_call(self, url: str, params: dict, timeout: int = 15,
                 referer: str | None = None) -> dict:
        """带限流和指数退避重试的API请求"""
        s = self._get_session()
        h = {"Referer": referer} if referer else {}

        for attempt in range(self._retry_max):
            # 限流：等待令牌
            self._bucket.acquire()

            # 请求间随机抖动（0.5~2秒），避免机械节奏
            jitter = random.uniform(0.5, 2.0)
            time.sleep(jitter)

            try:
                resp = s.get(
                    url, params=params, headers=h,
                    impersonate="chrome131", timeout=timeout,
                    proxies=self._get_proxies(),
                )
            except Exception as e:
                self._logger.warning(f"请求异常 (attempt {attempt+1}): {e}")
                self._backoff(attempt)
                continue

            # HTTP 层面反爬
            if resp.status_code in (412, 429):
                self._logger.warning(f"HTTP {resp.status_code} (attempt {attempt+1})")
                self._backoff(attempt)
                continue

            resp.raise_for_status()
            data = resp.json()
            code = data.get("code", -1)

            # B站业务码反爬
            if code in (-799, -412):
                self._logger.warning(f"B站API code={code} (attempt {attempt+1})")
                self._backoff(attempt)
                continue

            if code != 0:
                raise RuntimeError(
                    f"B站API错误: code={code} message={data.get('message', 'unknown')}"
                )
            return data

        raise RuntimeError(f"B站API请求失败，已重试{self._retry_max}次: url={url}")

    def _backoff(self, attempt: int):
        """指数退避 + 随机抖动：base * 2^attempt + jitter"""
        wait = self._retry_base_wait * (2 ** attempt) + random.uniform(0, 5)
        self._logger.info(f"退避等待 {wait:.1f}s 后重试...")
        time.sleep(wait)

    # ── 业务API ──

    def search_up(self, name: str) -> dict | None:
        """搜索UP主，返回 {uid, name} 或 None"""
        data = self.api_call(SEARCH_URL, {"search_type": "bili_user", "keyword": name})
        results = data.get("data", {}).get("result", [])
        if not results:
            return None
        for r in results:
            if r.get("uname", "").strip() == name.strip():
                return {"uid": str(r["mid"]), "name": r["uname"]}
        first = results[0]
        return {"uid": str(first.get("mid", "")), "name": first.get("uname", "")}

    def fetch_up_info(self, uid: str) -> dict:
        """获取UP主基本信息"""
        data = self.api_call(SPACE_INFO_URL, {"mid": uid})
        d = data.get("data", {})
        return {"name": d.get("name", ""), "sign": d.get("sign", ""), "face": d.get("face", "")}

    def fetch_latest_video(self, uid: str) -> dict | None:
        """获取UP主最新视频"""
        params = self.sign_wbi({"mid": uid, "ps": 1, "order": "pubdate"})
        data = self.api_call(
            ARC_SEARCH_URL, params,
            referer=f"https://space.bilibili.com/{uid}/video",
        )
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
        if not vlist:
            return None
        v = vlist[0]
        return {
            "bvid": v.get("bvid", ""), "title": v.get("title", ""),
            "desc": v.get("description", ""), "cover": v.get("pic", ""),
            "pubdate": v.get("created", 0),
        }

    def fetch_latest_dynamic(self, uid: str) -> dict | None:
        """获取UP主最新图文动态"""
        data = self.api_call(
            DYNAMIC_FEED_URL, {"host_mid": uid},
            referer=f"https://space.bilibili.com/{uid}/dynamic",
        )
        items = data.get("data", {}).get("items", [])
        for item in items:
            dtype = item.get("type", "")
            if dtype not in TRACKED_DYNAMIC_TYPES:
                continue
            modules = item.get("modules", {})
            desc = modules.get("module_dynamic", {}).get("desc", {})
            desc_text = desc.get("text", "") if isinstance(desc, dict) and desc else ""
            images = []
            major = modules.get("module_dynamic", {}).get("major", {})
            if major.get("type") == "MAJOR_TYPE_DRAW":
                for img in major.get("draw", {}).get("items", []):
                    images.append(img.get("src", ""))
            author = modules.get("module_author", {})
            return {
                "id_str": item.get("id_str", ""), "content": desc_text,
                "images": images, "timestamp": author.get("pub_ts", 0),
            }
        return None
