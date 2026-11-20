"""LLM 客户端：读取 .env，调用学校大模型平台（OpenAI 兼容接口）。

对标朋友项目 server/.../config/DeepSeekClient.java 的 callDeepSeekApi()，
区别：① 用 Python；② 这里是**真·调用**（朋友的那个被 mock 关掉了）；
③ 接的是学校平台，不是 DeepSeek。
"""
import os
import time
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL")
MODEL = os.getenv("LLM_MODEL")

if not API_KEY or API_KEY.startswith("你的"):
    raise RuntimeError("请在 .env 中填入 LLM_API_KEY（学校平台生成，sk- 开头）")

# OpenAI SDK 兼容任何 OpenAI 格式端点（学校平台就是）
# timeout：单次请求超时；max_retries：SDK 自身对连接错误/5xx 的重试次数。
client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60.0, max_retries=2)


def _is_transient(exc) -> bool:
    """判断是否为可重试的瞬时故障。

    学校平台网络抖动时会把"所有连接尝试失败"包装成 401 auth_error，
    这不是真的密钥错误，重试通常能成功；真正的 invalid_api_key 不重试。
    """
    msg = str(exc).lower()
    if "invalid_api_key" in msg or "incorrect api key" in msg:
        return False
    transient = (
        "connection" in msg or "connection attempts failed" in msg
        or "timeout" in msg or "timed out" in msg
        or "apiconnectionerror" in type(exc).__name__.lower()
        or "ratelimit" in type(exc).__name__.lower()
        or "internal_server" in type(exc).__name__.lower()
        or "service_unavailable" in type(exc).__name__.lower()
    )
    return transient


def chat(system: str, user: str, temperature: float = 0.2) -> str:
    """发一次对话，返回模型文本；对瞬时网络故障额外重试。"""
    last = None
    for attempt in range(4):  # SDK 内部已重试 2 次，这里再兜底 4 轮
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            last = exc
            if not _is_transient(exc) or attempt == 3:
                raise
            time.sleep(1.0 + attempt)  # 1s, 2s, 3s 递增退避
    raise last
