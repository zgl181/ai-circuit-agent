"""W3 设计 Agent：自然语言 -> {BOM + 连接关系/网表} 结构化 JSON。

对标朋友项目 ScriptService.java 的编排（收 prompt -> 调 LLM -> 解析 -> 返回），
但：① 用 Python；② 真调学校大模型平台（朋友那份是 mock）；
③ 输出同时包含 物料清单(BOM) 与 引脚级连接关系(网表)，为 W5/W6 接 KiCad 铺路。

用法：
    python scripts/design_agent.py "你的自然语言需求"
不传参数则用内置示例。运行后会在 outputs/ 下生成 design.json。
"""
import sys
import json
import os

from llm_client import chat
from pinouts import correct_design_pins

SYSTEM_PROMPT = """你是一个电路板设计助手。用户用自然语言描述一块电路板的需求，
你必须输出该电路的"设计"JSON，不要任何解释、不要 Markdown 代码块。

JSON 结构固定为：
{
  "board": "板子名称",
  "components": [
    {"ref": "元件位号，如 U1/R1/C1/SW1", "type": "元件类别，如 电阻/电容/LED/发光二极管/开关/按钮/晶振/二极管/三极管/运放/运算放大器/稳压器/电感/电位器/蜂鸣器/连接器/单片机/74HC373/芯片",
     "value": "参数值，如 10k/100nF/16MHz/LM358/74HC373/ATmega328P", "package": "封装，如 0805/DIP-20/SOT-23",
     "quantity": 数量(整数), "note": "备注，可空"}
  ],
  "nets": [
    {"name": "网络名，如 VCC/GND/LED_NET", "nodes": [{"ref": "R1", "pin": "1"}, {"ref": "LED1", "pin": "2"}]}
  ]
}
要求：
1. components 覆盖需求里的所有元件，位号唯一。
2. nets 给出主要电气连接（引脚级），node 用 {ref, pin} 指向 components 里的位号与引脚号。
3. 芯片类(运放/74系列/单片机/稳压器/三极管等)的 pin 必须参考该芯片真实数据手册的引脚号；
   拿不准时只连接关键引脚(电源/地/主要信号)，其余引脚可省略，不要编造错误引脚号。
4. 电源网络名请用标准名：VCC / GND / +5V / +3V3 / +12V / -12V 等，便于生成标准电源标注。
5. 只输出 JSON，不要输出其它内容。"""


def parse_json(text: str):
    """稳健解析模型返回的 JSON（兼容它偶尔套的 ```json 围栏）。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e != -1:
        t = t[s : e + 1]
    return json.loads(t)


def design(prompt: str) -> dict:
    """给定一句需求，返回 {board, components, nets} 字典。"""
    raw = chat(SYSTEM_PROMPT, prompt, temperature=0.2)
    data = parse_json(raw)
    # 按真实数据手册纠正已知芯片的错误引脚号（如 DIP 脚号误用于 QFN 符号）
    correct_design_pins(data)
    return data


def main():
    prompt = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "设计一个 LED 闪烁电路，包含 1 个 LED、1 个限流电阻、1 个轻触按钮、1 个 2 节电池座"
    )
    print(f"[需求] {prompt}\n")
    design_data = design(prompt)

    # 简单自校验
    comps = design_data.get("components", [])
    nets = design_data.get("nets", [])
    refs = {c["ref"] for c in comps}
    bad = [n for net in nets for n in net.get("nodes", []) if n["ref"] not in refs]
    print(json.dumps(design_data, ensure_ascii=False, indent=2))
    print(f"\n[自检] 元件 {len(comps)} 个, 网络 {len(nets)} 条"
          + (f", 警告: 以下节点位号不存在 {bad}" if bad else ", 位号引用一致 ✓"))

    os.makedirs("outputs", exist_ok=True)
    with open("outputs/design.json", "w", encoding="utf-8") as f:
        json.dump(design_data, f, ensure_ascii=False, indent=2)
    print("\n[已保存] outputs/design.json")


if __name__ == "__main__":
    main()
