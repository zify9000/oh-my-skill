"""初始化：将凭据写入 env 文件"""
import argparse

from common import ENV_DIR, FEISHU_ENV_PATH, CREDENTIALS_PATH, setup_logging

logger = setup_logging("init")


def write_env(path, entries: dict):
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in entries.items() if v]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"已写入 {path}")


def main():
    parser = argparse.ArgumentParser(description="更新凭据配置")
    parser.add_argument("--weread-api-key", default="", help="微信读书 API Key")
    parser.add_argument("--feishu-app-id", default="", help="飞书应用 ID")
    parser.add_argument("--feishu-app-secret", default="", help="飞书应用密钥")
    parser.add_argument("--feishu-chat-id", default="", help="飞书群聊 ID")
    args = parser.parse_args()

    changed = False

    if args.weread_api_key:
        write_env(CREDENTIALS_PATH, {"weread_api_key": args.weread_api_key})
        changed = True

    if args.feishu_app_id:
        write_env(FEISHU_ENV_PATH, {
            "feishu_app_id": args.feishu_app_id,
            "feishu_app_secret": args.feishu_app_secret,
            "feishu_chat_id": args.feishu_chat_id,
        })
        changed = True

    if not changed:
        logger.info("未提供任何凭据，无操作")
        print("用法: python3 scripts/init.py --weread-api-key ... [--feishu-* ...]")


if __name__ == "__main__":
    main()
