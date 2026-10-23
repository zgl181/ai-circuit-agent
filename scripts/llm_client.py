"""LLM 客户端：读取 .env，调用学校大模型平台（OpenAI 兼容接口）。

对标朋友项目 server/.../config/DeepSeekClient.java 的 callDeepSeekApi()，
区别：① 用 Python；② 这里是**真·调用**（朋友的那个被 mock 关掉了）；
③ 接的是学校平台，不是 DeepSeek。
"""
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL")
MODEL = os.getenv("LLM_MODEL")

if not API_KEY or API_KEY.startswith("你的"):
    raise RuntimeError("请在 .env 中填入 LLM_API_KEY（学校平台生成，sk- 开头）")

# OpenAI SDK 兼容任何 OpenAI 格式端点（学校平台就是）
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def chat(system: str, user: str, temperature: float = 0.2) -> str:
    """发一次对话，返回模型文本。"""
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""
