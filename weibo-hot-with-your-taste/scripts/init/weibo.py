"""微博登录：通过 Chromium headless 扫码，导出完整 Cookie

依赖: pip install nodriver

QR 图片保存至 /tmp/weibo_login_qr.png，agent 展示给用户扫码。

使用:
  python scripts/init/weibo.py
  python scripts/init/weibo.py --no-sandbox            # Docker/snap 环境
  python scripts/init/weibo.py --browser-path /opt/chrome/google-chrome
"""

import sys
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


def _check_nodriver():
    """检查 nodriver 是否安装，未安装则给出安装提示"""
    try:
        import nodriver  # noqa: F401
    except ImportError:
        print(
            "错误: 未安装 nodriver 包。\n"
            f"请运行: {sys.executable} -m pip install 'nodriver>=0.50'\n"
            f"当前 Python: {sys.executable}\n"
            f"注意: 确保使用安装了 nodriver 的 Python 环境（如 miniconda）而非系统 Python。",
            file=sys.stderr,
        )
        sys.exit(1)


async def _login(timeout: int = 180, sandbox: bool = True, browser_path: str = "") -> dict:
    import nodriver as uc

    logger.info(f"正在启动 Chromium（headless 模式, sandbox={sandbox}）...")
    browser = await uc.start(
        headless=True,
        sandbox=sandbox,
        browser_executable_path=browser_path or None,
    )
    tab = browser.main_tab

    await tab.get(LOGIN_URL)

    # 等待页面 JavaScript 完成初始化（过早 evaluate 会打断 QR 码渲染）
    await tab.sleep(15)

    # 轮询等待 QR 码出现
    qr_data = {}
    for attempt in range(15):
        if attempt > 0:
            await tab.sleep(2)
        qr_result = await tab.evaluate("""
            (() => {
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    if (img.width > 50 && /(?:v2\\.)?qr\\.weibo\\.cn/.test(img.src)) {
                        return JSON.stringify({found: true, src: img.src});
                    }
                }
                return JSON.stringify({found: false, total: imgs.length});
            })()
        """)
        qr_data = json.loads(qr_result)
        if qr_data.get("found"):
            break
        logger.info(f"等待二维码加载... ({attempt + 1}/15)")

    if qr_data.get("found"):
        logger.info(f"下载二维码: {qr_data['src'][:80]}...")
        urlretrieve(qr_data["src"], str(QR_IMAGE_PATH))
        logger.info(f"二维码已保存: {QR_IMAGE_PATH}")
    else:
        img_count = qr_data.get("total", 0)
        if img_count > 10:
            hint = "页面可能加载了微博信息流而非登录页（检测到大量内容图片）。请重试或检查 IP 是否被限制。"
        elif img_count == 0:
            hint = "页面未加载任何图片，可能是网络不通或 Chromium 无法渲染页面。"
        else:
            hint = "页面已加载但未找到 QR 码图片，可能是微博页面结构发生了变化。"
        raise RuntimeError(
            f"二维码加载失败：等待 {15 * 2 + 5} 秒后仍未找到 (共 {img_count} 张图片)。"
            f"{hint}"
        )

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
    _check_nodriver()
    parser = argparse.ArgumentParser(description="微博登录")
    parser.add_argument("--timeout", type=int, default=180, help="扫码超时（秒）")
    parser.add_argument("--no-sandbox", dest="sandbox", action="store_false",
                        help="禁用 Chromium 沙箱（Docker/snap 环境可能需要）")
    parser.add_argument("--browser-path", type=str, default="",
                        help="指定 Chromium 浏览器路径（默认自动检测）")
    args = parser.parse_args()

    try:
        cookie_dict = asyncio.run(
            _login(
                timeout=args.timeout,
                sandbox=args.sandbox,
                browser_path=args.browser_path,
            )
        )
    except TimeoutError:
        print(json.dumps({"action": "timeout", "message": "登录超时"}, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        # 同时输出到 stderr 和日志，确保终端可见
        msg = f"登录失败: {e}"
        print(msg, file=sys.stderr)
        logger.error(msg)
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
