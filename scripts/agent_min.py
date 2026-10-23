"""W2 最小 Agent：自然语言 -> BOM 结构化 JSON。

对标朋友项目 ScriptService.java 的编排：收 prompt -> 调 LLM -> 解析 -> 返回。
这是 MVP 的地基：先让模型稳定吐出"物料清单"JSON，后面 W3/W4 再加连接关系、
W5/W6 再接 KiCad 生成原理图。

用法：
    python scripts/agent_min.py "你的自然语言需求"
不传参数则用内置示例。
"""
import sys
import json

from llm_client import chat

SYSTEM_PROMPT = """你是一个电路板设计助手。用户用自然语言描述一块电路板的需求，
你必须输出该电路的 BOM（物料清单）JSON，不要任何解释、不要 Markdown 代码块。

JSON 结构固定为：
{
  "board": "板子名称",
  "components": [
    {"ref": "元件位号，如 U1/R1/C1", "type": "元件类别，如 MCU/电阻/电容",
     "value": "参数值，如 10k/100nF/16MHz", "package": "封装，如 0805/DIP-28",
     "quantity": 数量(整数), "note": "备注"}
  ]
}
只输出 JSON，不要输出其它内容。"""


def parse_json(text: str):
    """把模型返回的 JSON 稳健地解析出来（兼容它偶尔套的 ```json 围栏）。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        t = t.split("```", 2)[1]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e != -1:
        t = t[s : e + 1]
    return json.loads(t)


def design_bom(prompt: str) -> dict:
    """给定一句需求，返回 BOM 字典。"""
    raw = chat(SYSTEM_PROMPT, prompt, temperature=0.2)
    return parse_json(raw)


def main():
    prompt = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "设计一个 Arduino Uno 最小系统板，含 ATmega328P、16MHz 晶振、复位电路、3.3V 电源"
    )
    print(f"[需求] {prompt}\n")
    bom = design_bom(prompt)
    print(json.dumps(bom, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
