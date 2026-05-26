"""初始化：将凭据写入 env 文件。支持 CLI 传入或扫码获取 B站 Cookie。"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from curl_cffi import requests as curl

from common import ENV_DIR, FEISHU_ENV_PATH, CREDENTIALS_PATH, setup_logging

logger = setup_logging("init")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"


def write_env(path, entries: dict):
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in entries.items() if v]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"已写入 {path}")


# ── B站 Cookie 扫码获取 ──

def _display_qr(url: str):
    try:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print(f"\n📎 请复制链接用 B站 App 打开：{url}\n")


def _generate_qrcode(session: curl.Session) -> tuple:
    resp = session.get(GENERATE_URL, headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}, impersonate="chrome131", timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"生成二维码失败: {data.get('message', data)}")
    return data["data"]["qrcode_key"], data["data"]["url"]


def _poll_until_login(session: curl.Session, qrcode_key: str, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    last_msg = None
    messages = {86101: "⏳ 等待扫码...", 86090: "📱 已扫码，请在手机上确认登录", 86038: "⏰ 二维码已过期"}
    while time.time() < deadline:
        try:
            resp = session.get(POLL_URL, params={"qrcode_key": qrcode_key}, headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}, impersonate="chrome131", timeout=10)
            data = resp.json()
        except Exception as e:
            print(f"  ⚠ 网络波动: {e}，2s 后重试...")
            time.sleep(2)
            continue
        if data.get("code") != 0:
            time.sleep(2)
            continue
        code = data["data"].get("code")
        if code == 0:
            print("  ✅ 登录成功！")
            return dict(resp.cookies)
        msg = messages.get(code, f"❓ 未知状态码 {code}")
        if msg != last_msg:
            print(f"  {msg}")
            last_msg = msg
        time.sleep(2)
    raise TimeoutError(f"登录超时 ({timeout}s)")


def _extract_cookies(cookies: dict) -> dict:
    result = {}
    sessdata = cookies.get("SESSDATA", "")
    if sessdata:
        result["bili_sessdata"] = sessdata
    jct = cookies.get("bili_jct", "")
    if jct:
        result["bili_jct"] = jct
    buvid3 = cookies.get("buvid3", "")
    if buvid3:
        result["bili_buvid3"] = buvid3
    else:
        result["bili_buvid3"] = str(uuid.uuid4()).upper()[:37] + "infoc"
    return result


def refresh_bili_cookie():
    print("🔐 B站 登录 · 二维码扫码\n")
    print("📡 正在生成登录二维码...")
    session = curl.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        qrcode_key, url = _generate_qrcode(session)
    except Exception as e:
        print(f"❌ 生成二维码失败: {e}")
        sys.exit(1)
    _display_qr(url)
    print("👆 请用 B站 App 扫描上方二维码\n")
    try:
        cookies = _poll_until_login(session, qrcode_key)
    except (TimeoutError, RuntimeError) as e:
        print(f"❌ {e}")
        sys.exit(1)
    cookie_map = _extract_cookies(cookies)
    if not cookie_map.get("bili_sessdata"):
        print("⚠️ 未获取到 SESSDATA！")
        sys.exit(1)
    write_env(CREDENTIALS_PATH, cookie_map)
    print("\n🎉 B站 Cookie 已就绪")


# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser(description="凭据配置")
    parser.add_argument("--bili-sessdata", default="", help="B站 SESSDATA cookie")
    parser.add_argument("--bili-jct", default="", help="B站 bili_jct cookie")
    parser.add_argument("--bili-buvid3", default="", help="B站 buvid3 cookie")
    parser.add_argument("--refresh-bili-cookie", action="store_true", help="扫码获取 B站 Cookie")
    parser.add_argument("--feishu-app-id", default="", help="飞书应用 ID")
    parser.add_argument("--feishu-app-secret", default="", help="飞书应用密钥")
    parser.add_argument("--feishu-chat-id", default="", help="飞书群聊 ID")
    args = parser.parse_args()

    if args.refresh_bili_cookie:
        refresh_bili_cookie()

    changed = False

    if args.bili_sessdata:
        write_env(CREDENTIALS_PATH, {
            "bili_sessdata": args.bili_sessdata,
            "bili_jct": args.bili_jct,
            "bili_buvid3": args.bili_buvid3,
        })
        changed = True

    if args.feishu_app_id:
        write_env(FEISHU_ENV_PATH, {
            "feishu_app_id": args.feishu_app_id,
            "feishu_app_secret": args.feishu_app_secret,
            "feishu_chat_id": args.feishu_chat_id,
        })
        changed = True

    if not changed and not args.refresh_bili_cookie:
        logger.info("未提供任何凭据，无操作")
        print("用法: python3 scripts/init.py --refresh-bili-cookie  [--feishu-* ...]")


if __name__ == "__main__":
    main()
