"""偏好初始化脚本：Step 1 领域关键词→语义匹配分类，Step 2 用户选择→生成配置"""
import sys
import json
import re
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import openai

from common import (
    SCRIPT_DIR, DATA_DIR, CONFIG_DIR,
    CATEGORY_STORE_PATH, RULE_CONFIG_PATH, INITIALIZED_PATH,
    setup_logging, load_base_config, load_llm_env, load_prompt, resolve_llm_creds, validate_llm_creds,
)

logger = setup_logging("init")
CONFIG = load_base_config()


# ── Step 1: 领域关键词 → 语义匹配分类 ──

def load_categories() -> list:
    """从 topic_category.json 加载全部分类"""
    if not CATEGORY_STORE_PATH.exists():
        logger.error("topic_category.json 不存在，请先运行 fetch.py 抓取数据")
        sys.exit(1)
    with open(CATEGORY_STORE_PATH, encoding="utf-8") as f:
        store = json.load(f)
    return store.get("categories", [])


def call_llm_match_categories(domain_keywords: list, categories: list, llm_model="", base_url="", api_key="") -> dict:
    """LLM 根据领域关键词从分类列表中选出 liked/disliked 各10个"""
    issues = validate_llm_creds(llm_model, base_url, api_key)
    if issues:
        logger.error(f"LLM 凭据异常: {'; '.join(issues)}")
        sys.exit(1)

    domain_kw_str = "、".join(domain_keywords)
    category_list = "\n".join(f"{i+1}. {category}" for i, category in enumerate(categories))

    prompt_template = load_prompt("init_keywords_prompt")
    prompt = prompt_template.format(domain_keywords=domain_kw_str, category_list=category_list)

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            timeout=60,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.error("LLM 返回为空")
            sys.exit(1)

        liked = []
        disliked = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+):(liked|disliked):(.+)$", line)
            if m:
                cat_name = m.group(3).strip()
                if cat_name in categories:
                    if m.group(2) == "liked":
                        liked.append(cat_name)
                    else:
                        disliked.append(cat_name)

        if not liked and not disliked:
            logger.error("LLM 输出未匹配到任何分类")
            sys.exit(1)

        logger.info(f"语义匹配结果: liked={len(liked)}, disliked={len(disliked)}")
        return {"liked": liked, "disliked": disliked}

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        sys.exit(1)


def step_domain_keywords(args):
    """Step 1: 领域关键词 → 语义匹配"""
    domain_keywords = args.domain_kw
    if len(domain_keywords) != 3:
        logger.error("请提供恰好3个领域关键词")
        sys.exit(1)

    categories = load_categories()
    logger.info(f"加载 {len(categories)} 个分类，领域关键词: {domain_keywords}")

    llm_model, llm_base_url, llm_api_key = resolve_llm_creds(
        CONFIG, args.llm_model, args.llm_base_url, args.llm_api_key
    )
    result = call_llm_match_categories(domain_keywords, categories, llm_model, llm_base_url, llm_api_key)

    output = {
        "domain_keywords": domain_keywords,
        "liked": result["liked"],
        "disliked": result["disliked"],
    }
    print(json.dumps(output, ensure_ascii=False))


# ── Step 2: 用户选择 → 生成配置 ──

def call_llm_generate_criteria(domain_keywords, liked, disliked, recall, llm_model="", base_url="", api_key="") -> tuple:
    """LLM 根据用户偏好生成 yes/no 判断标准，返回 (yes_criteria, no_criteria)"""
    issues = validate_llm_creds(llm_model, base_url, api_key)
    if issues:
        logger.error(f"LLM 凭据异常: {'; '.join(issues)}")
        sys.exit(1)

    domain_kw_str = "、".join(domain_keywords)
    liked_str = "、".join(liked)
    disliked_str = "、".join(disliked)
    recall_str = "、".join(recall) if recall else "无"

    prompt_template = load_prompt("init_criteria_prompt")
    prompt = prompt_template.format(
        domain_keywords=domain_kw_str,
        liked_categories=liked_str,
        disliked_categories=disliked_str,
        recall_keywords=recall_str,
    )

    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4096,
            timeout=120,
        )
        content = resp.choices[0].message.content
        if not content:
            logger.error("LLM 返回为空")
            sys.exit(1)

        yes_criteria = ""
        no_criteria = ""

        yes_match = re.search(r"===yes===\s*\n(.*?)(?=\n===no===)", content, re.DOTALL)
        if yes_match:
            yes_criteria = yes_match.group(1).strip()

        no_match = re.search(r"===no===\s*\n(.*)", content, re.DOTALL)
        if no_match:
            no_criteria = no_match.group(1).strip()

        if not yes_criteria or not no_criteria:
            logger.error("无法解析 LLM 输出中的判断标准")
            sys.exit(1)

        logger.info(f"判断标准生成完成: yes={len(yes_criteria)}字, no={len(no_criteria)}字")
        return yes_criteria, no_criteria

    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        sys.exit(1)


def generate_rule_yaml(disliked, recall):
    """生成 rule.yaml 内容"""
    # category_exclude: 用户不喜欢的分类
    category_exclude = list(disliked)

    # recall_keywords: 用户的召回关键词
    recall_keywords = list(recall)

    rule_config = {
        "category_exclude": category_exclude,
        "recall_keywords": recall_keywords,
    }

    # 备份旧文件
    if RULE_CONFIG_PATH.exists():
        backup_path = RULE_CONFIG_PATH.with_suffix(".yaml.bak")
        RULE_CONFIG_PATH.rename(backup_path)
        logger.info(f"旧 rule.yaml 已备份为 {backup_path.name}")

    with open(RULE_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(rule_config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"rule.yaml 已生成: exclude={len(category_exclude)}, recall={len(recall_keywords)}")
    return rule_config


def write_initialized(domain_keywords, liked, disliked, recall, yes_criteria, no_criteria):
    """写入 .initialized 标记文件"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "initialized_at": datetime.now().isoformat(),
        "domain_keywords": domain_keywords,
        "liked_categories": liked,
        "disliked_categories": disliked,
        "recall_keywords": recall,
        "yes_criteria": yes_criteria,
        "no_criteria": no_criteria,
    }
    with open(INITIALIZED_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    logger.info(".initialized 标记文件已写入")


def step_choices(args):
    """Step 2: 用户选择 → LLM 生成判断标准（待确认）"""
    domain_keywords = list(args.domain_kw)
    liked = [x.strip() for x in args.liked.split(",") if x.strip()]
    disliked = [x.strip() for x in args.disliked.split(",") if x.strip()]
    recall = [x.strip() for x in args.recall.split(",") if x.strip()] if args.recall else []

    if len(domain_keywords) < 2:
        logger.error("请提供至少2个领域关键词")
        sys.exit(1)
    if len(liked) < 5:
        logger.error(f"感兴趣的分类至少选5个，当前{len(liked)}个")
        sys.exit(1)
    if len(disliked) < 5:
        logger.error(f"不感兴趣的分类至少选5个，当前{len(disliked)}个")
        sys.exit(1)

    llm_model, llm_base_url, llm_api_key = resolve_llm_creds(
        CONFIG, args.llm_model, args.llm_base_url, args.llm_api_key
    )
    yes_criteria, no_criteria = call_llm_generate_criteria(
        domain_keywords, liked, disliked, recall, llm_model, llm_base_url, llm_api_key
    )

    output = {
        "status": "pending_confirm",
        "domain_keywords": domain_keywords,
        "liked": liked,
        "disliked": disliked,
        "recall": recall,
        "yes_criteria": yes_criteria,
        "no_criteria": no_criteria,
        "next_step": "确认后执行: python3 scripts/init/feature.py confirm --file <保存上述JSON的文件路径>",
    }
    print(json.dumps(output, ensure_ascii=False))


def step_confirm(args):
    """Step 3: 用户确认 → 写入配置"""
    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)

    domain_keywords = data["domain_keywords"]
    liked = data["liked"]
    disliked = data["disliked"]
    recall = data.get("recall", [])
    yes_criteria = data["yes_criteria"]
    no_criteria = data["no_criteria"]

    rule_config = generate_rule_yaml(disliked, recall)
    write_initialized(domain_keywords, liked, disliked, recall, yes_criteria, no_criteria)

    output = {
        "status": "ok",
        "rule": rule_config,
    }
    print(json.dumps(output, ensure_ascii=False))


def main():
    load_llm_env()
    parser = argparse.ArgumentParser(description="偏好初始化")
    subparsers = parser.add_subparsers(dest="step", required=True)

    # Step 1: domain-keywords
    p1 = subparsers.add_parser("domain-keywords", help="领域关键词→语义匹配分类")
    p1.add_argument("--domain-kw", nargs="*", required=True, help="领域关注关键词（2-5个）")
    p1.add_argument("--llm-model", default="", help="LLM 模型名")
    p1.add_argument("--llm-base-url", default="", help="LLM API 地址")
    p1.add_argument("--llm-api-key", default="", help="LLM API 密钥")

    # Step 2: choices
    p2 = subparsers.add_parser("choices", help="用户选择→LLM 生成判断标准（待确认）")
    p2.add_argument("--domain-kw", nargs="*", required=True, help="领域关注关键词")
    p2.add_argument("--liked", required=True, help="逗号分隔的感兴趣分类")
    p2.add_argument("--disliked", required=True, help="逗号分隔的不感兴趣分类")
    p2.add_argument("--recall", default="", help="逗号分隔的召回关键词")
    p2.add_argument("--llm-model", default="", help="LLM 模型名")
    p2.add_argument("--llm-base-url", default="", help="LLM API 地址")
    p2.add_argument("--llm-api-key", default="", help="LLM API 密钥")

    # Step 3: confirm
    p3 = subparsers.add_parser("confirm", help="确认判断标准 → 写入配置")
    p3.add_argument("--file", required=True, help="choices 输出的 JSON 文件路径（可含用户修改）")

    args = parser.parse_args()

    if args.step == "domain-keywords":
        step_domain_keywords(args)
    elif args.step == "choices":
        step_choices(args)
    elif args.step == "confirm":
        step_confirm(args)


if __name__ == "__main__":
    main()
