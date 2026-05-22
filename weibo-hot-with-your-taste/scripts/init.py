"""初始化脚本：将 agent 凭据写入 env 文件，并切换 base.yaml 为 env 模式"""
import sys
import argparse

from common import SCRIPT_DIR, CONFIG_DIR, setup_logging, load_base_config

logger = setup_logging("init")

BASE_CONFIG_PATH = CONFIG_DIR / "base.yaml"
LLM_ENV_PATH = SCRIPT_DIR / "env" / ".llm.env"
FEISHU_ENV_PATH = SCRIPT_DIR / "env" / ".feishu.env"


def write_env_file(path, entries: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in entries.items() if v]
    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    logger.info(f"已写入 {path}")


def update_base_yaml(config: dict):
    """将 base.yaml 中 source=agent 的项改为 env"""
    import yaml

    changed = False

    if config.get("llm_credential_source") == "agent":
        config["llm_credential_source"] = "env"
        changed = True

    if config.get("feishu_credential_source") == "agent":
        config["feishu_credential_source"] = "env"
        changed = True

    if changed:
        with open(BASE_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info(f"已更新 {BASE_CONFIG_PATH}")
    else:
        logger.info("base.yaml 无需变更")


def main():
    parser = argparse.ArgumentParser(description="初始化凭据配置")
    parser.add_argument("--llm-model", default="", help="LLM 模型名")
    parser.add_argument("--llm-base-url", default="", help="LLM API 地址")
    parser.add_argument("--llm-api-key", default="", help="LLM API 密钥")
    parser.add_argument("--feishu-app-id", default="", help="飞书应用 ID")
    parser.add_argument("--feishu-app-secret", default="", help="飞书应用密钥")
    parser.add_argument("--feishu-chat-id", default="", help="飞书群聊 ID")
    args = parser.parse_args()

    config = load_base_config()
    changed = False

    if args.llm_api_key:
        write_env_file(LLM_ENV_PATH, {
            "llm_model": args.llm_model,
            "llm_base_url": args.llm_base_url,
            "llm_api_key": args.llm_api_key,
        })
        changed = True

    if args.feishu_app_id:
        write_env_file(FEISHU_ENV_PATH, {
            "feishu_app_id": args.feishu_app_id,
            "feishu_app_secret": args.feishu_app_secret,
            "feishu_chat_id": args.feishu_chat_id,
        })
        changed = True

    if changed:
        update_base_yaml(config)

    if not changed:
        logger.info("未提供任何凭据，无操作")
        print("用法: python3 scripts/init.py --llm-model ... --llm-base-url ... --llm-api-key ... [--feishu-* ...]")


if __name__ == "__main__":
    main()
