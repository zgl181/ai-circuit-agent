"""本地设计兜底：学校 LLM 平台不可用时的内置电路模板。

覆盖主页四个快捷输入与常见 LED 电路，输出与 LLM 相同结构的
{board, components, nets} 字典，直接喂给 kicad_net / kicad_sch。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ATMEGA_TMPL = os.path.join(HERE, "templates", "atmega328p_min.json")


def _load_atmega():
    with open(ATMEGA_TMPL, encoding="utf-8") as f:
        return json.load(f)


def _count(prompt, pattern, default=1):
    m = re.search(pattern, prompt)
    return int(m.group(1)) if m else default


def _led_circuit(board, led_count=1, with_button=True, with_battery=True):
    """电池 -> 按钮 -> 限流电阻 -> LED -> 地，论文式串联支路。"""
    components = []
    nets = []

    if with_battery:
        components.append({
            "ref": "BT1", "type": "电池", "value": "2×AA 3V",
            "package": "BatteryHolder_2AA", "quantity": 1, "note": "2 节电池座",
        })
    if with_button:
        components.append({
            "ref": "SW1", "type": "按钮", "value": "轻触开关",
            "package": "SW_PUSH", "quantity": 1, "note": "按键控制",
        })

    for i in range(1, led_count + 1):
        components.append({
            "ref": f"R{i}", "type": "电阻", "value": "330Ω",
            "package": "0805", "quantity": 1, "note": "限流电阻",
        })
        components.append({
            "ref": f"D{i}", "type": "发光二极管", "value": "红色 LED",
            "package": "LED_0805", "quantity": 1, "note": "指示/闪烁",
        })

    vcc_nodes, sw_nodes, gnd_nodes = [], [], []
    if with_battery:
        vcc_nodes.append({"ref": "BT1", "pin": "1"})
        gnd_nodes.append({"ref": "BT1", "pin": "2"})
    if with_button:
        if vcc_nodes:
            vcc_nodes.append({"ref": "SW1", "pin": "1"})
        sw_nodes.append({"ref": "SW1", "pin": "2"})
    for i in range(1, led_count + 1):
        sw_nodes.append({"ref": f"R{i}", "pin": "1"})
        # LOCAL_SERIES_NETS 识别这两个名字，会画"电阻→LED"真实短导线
        series_name = "LED_PWR" if i == 1 else ("LED_D13" if i == 2 else f"LED_N{i}")
        nets.append({
            "name": series_name,
            "nodes": [{"ref": f"R{i}", "pin": "2"}, {"ref": f"D{i}", "pin": "1"}],
        })
        gnd_nodes.append({"ref": f"D{i}", "pin": "2"})

    if vcc_nodes:
        nets.append({"name": "VCC", "nodes": vcc_nodes})
    if sw_nodes:
        nets.append({"name": "SW_OUT", "nodes": sw_nodes})
    if gnd_nodes:
        nets.append({"name": "GND", "nodes": gnd_nodes})

    return {"board": board, "components": components, "nets": nets}


def local_design(prompt: str) -> dict:
    """按关键词匹配内置模板；未命中时回退到官方 ATmega 最小系统。"""
    p = (prompt or "").lower()

    if "atmega" in p or " arduino" in p or "最小系统" in p or "单片机" in p:
        return _load_atmega()

    if "led" in p or "发光" in p or "灯" in p:
        led_count = _count(prompt, r"(\d+)\s*(?:个|只|颗)?\s*(?:led|发光二极管|灯)", 1)
        if any(k in p for k in ("双", "两个", "2 个", "2个")):
            led_count = max(led_count, 2)
        led_count = max(1, min(led_count, 4))
        with_button = any(k in p for k in ("按钮", "按键", "轻触", "开关"))
        with_battery = any(k in p for k in ("电池", "供电", "电池座"))
        if "流水" in p:
            board = "双 LED 流水灯" if led_count == 2 else f"{led_count} LED 流水灯"
        elif "闪烁" in p:
            board = "LED 闪烁电路"
        else:
            board = "电池供电开关灯" if with_battery else "LED 指示电路"
        return _led_circuit(board, led_count, with_button, with_battery)

    # 其他需求：给出经过验证的官方最小系统作为可运行参考设计
    return _load_atmega()
