"""
W1 环境自检：验证「学校大模型平台」的密钥 + 接口地址 + 模型名 是否可用。

用法（在项目根目录、已激活 .venv 的终端里）：
    python scripts/test_llm.py            # 列出可用模型 + 发一句测试对话
    python scripts/test_llm.py --list     # 只列模型（不知道模型名时先跑这个）

前置：
    .env 里已填好 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
"""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_KEY = os.getenv("LLM_API_KEY", "").strip()
BASE_URL = os.getenv("LLM_BASE_URL", "https://xplt.sdu.edu.cn:4000/v1").strip()
MODEL = os.getenv("LLM_MODEL", "").strip()

if not API_KEY or API_KEY.startswith("你的"):
    print("[x] 还没填 LLM_API_KEY，请打开 .env 填写平台生成的密钥。")
    sys.exit(1)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def list_models() -> list[str]:
    print(f"[i] 接口地址: {BASE_URL}")
    print("[i] 正在拉取可用模型列表...")
    try:
        resp = client.models.list()
    except Exception as e:  # noqa: BLE001
        print(f"[x] 拉取模型失败: {type(e).__name__}: {e}")
        print("    常见原因：base_url 缺 /v1、需要校园网/VPN、密钥无效。")
        return []
    names = sorted(m.id for m in resp.data)
    print(f"[✓] 共 {len(names)} 个模型：")
    for n in names:
        print("    -", n)
    return names


def test_chat(model: str) -> bool:
    print(f"\n[i] 测试对话，模型 = {model}")
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个简洁的助手。"},
                {"role": "user", "content": "只回复四个字：环境正常"},
            ],
            temperature=0,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[x] 对话失败: {type(e).__name__}: {e}")
        return False
    print("[✓] 模型回复:", r.choices[0].message.content)
    print("[✓] W1 通过：密钥、接口、模型全部可用了。")
    return True


if __name__ == "__main__":
    only_list = "--list" in sys.argv
    names = list_models()

    if only_list:
        sys.exit(0 if names else 1)

    if not MODEL or MODEL.startswith("你的"):
        if names:
            print("\n[!] .env 里的 LLM_MODEL 还没填。请从上面列表里挑一个填进 .env，再跑一次。")
        sys.exit(1)

    ok = test_chat(MODEL)
    sys.exit(0 if ok else 1)
