"""微博登录：通过 Chromium 扫码 + 访客验证，导出完整 Cookie

依赖: pip install nodriver

自动适配环境：
  - 有 DISPLAY：弹出浏览器窗口，用户直接扫码
  - 无 DISPLAY：headless 模式，QR 图片保存至 /tmp/weibo_login_qr.png，agent 展示给用户

使用:
  python scripts/init/weibo.py
"""

import sys
import os
import json
import asyncio
import argparse
from pathlib import Path
from urllib.request import urlretrieve

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from common import SCRIPT_DIR, setup_logging

logger = setup_logging("weibo_login")
WEIBO_ENV_PATH = SCRIPT_DIR / "env" / ".weibo.env"
QR_IMAGE_PATH = Path("/tmp/weibo_login_qr.png")

LOGIN_URL = "https://weibo.com/newlogin?tabtype=weibo&openLoginLayer=1"


async def _login(timeout: int = 180) -> dict:
    import nodriver as uc

    use_headless = not os.environ.get("DISPLAY")
    mode = "headless" if use_headless else "headful"
    logger.info(f"正在启动 Chromium（{mode} 模式）...")
    browser = await uc.start(headless=use_headless)
    tab = browser.main_tab

    await tab.get(LOGIN_URL)
    await tab.sleep(5)

    # 提取 QR 图片并下载
    qr_result = await tab.evaluate("""
        (() => {
            const imgs = document.querySelectorAll('img');
            for (const img of imgs) {
                if (img.src.includes('qr.weibo.cn') && img.width > 50) {
                    return JSON.stringify({found: true, src: img.src});
                }
            }
            return JSON.stringify({found: false, total: imgs.length});
        })()
    """)

    qr_data = json.loads(qr_result)
    if qr_data.get("found"):
        logger.info(f"下载二维码: {qr_data['src'][:80]}...")
        urlretrieve(qr_data["src"], str(QR_IMAGE_PATH))
        logger.info(f"二维码已保存: {QR_IMAGE_PATH}")
    else:
        logger.warning(f"未找到二维码 (共 {qr_data.get('total', 0)} 张图片)")

    print(json.dumps({
        "action": "scan_qr_code",
        "qr_image_path": str(QR_IMAGE_PATH),
        "qr_image_url": qr_data.get("src", ""),
        "message": "请用微博 App 扫描二维码登录",
        "timeout": timeout,
    }, ensure_ascii=False))

    logger.info("等待扫码登录...")
    for _ in range(timeout):
        await tab.sleep(1)
        url = tab.target.url if hasattr(tab.target, "url") else ""
        if url and "login" not in url and "passport" not in url and "signin" not in url:
            logger.info(f"登录完成: {url[:80]}")
            break
    else:
        browser.stop()
        raise TimeoutError("登录超时")

    await tab.sleep(3)

    cookies = await browser.cookies.get_all()
    cookie_dict = {}
    for c in cookies:
        name = c.name if hasattr(c, "name") else c.get("name", "")
        value = c.value if hasattr(c, "value") else c.get("value", "")
        if name and value:
            cookie_dict[name] = value

    logger.info(f"提取到 {len(cookie_dict)} 个 Cookie: {list(cookie_dict.keys())}")
    browser.stop()
    return cookie_dict


def _save(cookie_dict: dict):
    WEIBO_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    sub = cookie_dict.get("SUB", "")
    lines = [f"weibo_sub={sub}"]
    for name, value in cookie_dict.items():
        lines.append(f"weibo_{name}={value}")
    lines.append(f"weibo_cookies_json={json.dumps(cookie_dict)}")
    with open(WEIBO_ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logger.info(f"已保存 {len(cookie_dict)} 个 Cookie 到 {WEIBO_ENV_PATH}")


def main():
    parser = argparse.ArgumentParser(description="微博登录")
    parser.add_argument("--timeout", type=int, default=180, help="扫码超时（秒）")
    args = parser.parse_args()

    try:
        cookie_dict = asyncio.run(_login(timeout=args.timeout))
    except TimeoutError:
        print(json.dumps({"action": "timeout", "message": "登录超时"}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        logger.error(f"登录失败: {e}")
        sys.exit(1)

    if not cookie_dict.get("SUB"):
        logger.error("未获取到 SUB Cookie")
        sys.exit(1)

    _save(cookie_dict)
    print(json.dumps({
        "action": "login_success",
        "message": f"登录成功，已保存 {len(cookie_dict)} 个 Cookie",
        "cookies": list(cookie_dict.keys()),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
