"""初始化：将 agent 飞书凭据写入 env 文件，切换 base.yaml 为 env 模式"""
import argparse

from common import ENV_DIR, FEISHU_ENV_PATH, BASE_CONFIG_PATH, setup_logging, load_base_config

logger = setup_logging("init")


def update_base_yaml(config: dict):
    import yaml
    if config.get("feishu_credential_source") != "agent":
        logger.info("base.yaml 无需变更")
        return
    config["feishu_credential_source"] = "env"
    with open(BASE_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"已更新 {BASE_CONFIG_PATH}")


def main():
    parser = argparse.ArgumentParser(description="初始化飞书凭据")
    parser.add_argument("--feishu-app-id", default="", help="飞书应用 ID")
    parser.add_argument("--feishu-app-secret", default="", help="飞书应用密钥")
    parser.add_argument("--feishu-chat-id", default="", help="飞书群聊 ID")
    args = parser.parse_args()

    if not args.feishu_app_id:
        logger.info("未提供飞书凭据，无操作")
        return

    config = load_base_config()
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"feishu_app_id={args.feishu_app_id}",
        f"feishu_app_secret={args.feishu_app_secret}",
        f"feishu_chat_id={args.feishu_chat_id}",
    ]
    FEISHU_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"已写入 {FEISHU_ENV_PATH}")
    update_base_yaml(config)


if __name__ == "__main__":
    main()
