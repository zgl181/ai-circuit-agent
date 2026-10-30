"""把 design.json 转成 KiCad 标准网表文件 design.net。

这是"网表"的标准产物：元件清单 + 引脚级连接，可直接被 pcbnew 读入做 PCB 布局。
对应作业要求的"网表 JSON"的落地文件形态。

用法：
    python scripts/kicad_net.py            # 读 outputs/design.json -> outputs/design.net
    python scripts/kicad_net.py 输入.json 输出.net
"""
import sys
import json
import datetime


# 元件类别 -> (KiCad 库名, 默认 part)。仅用于网表里标注"元件来自哪个库"。
LIB_MAP = {
    "电阻": ("Device", "R"), "resistor": ("Device", "R"),
    "电容": ("Device", "C"), "capacitor": ("Device", "C"),
    "led": ("Device", "LED"), "发光二极管": ("Device", "LED"),
    "开关": ("Device", "SW_Push"), "按钮": ("Device", "SW_Push"),
    "晶振": ("Device", "CRYSTAL"), "crystal": ("Device", "CRYSTAL"),
    "mcu": ("MCU_Microchip", "ATmega328P"), "单片机": ("MCU_Microchip", "ATmega328P"),
    "连接器": ("Connector", "Conn_01x02"), "电池": ("Battery", "BatteryHolder_2xAA"),
}


def lib_for(comp: dict):
    t = (comp.get("type") or "").lower()
    for k, v in LIB_MAP.items():
        if k.lower() in t:
            return v
    return ("Device", "R")  # 兜底


def build_netlist(design: dict) -> str:
    comps = design.get("components", [])
    nets = design.get("nets", [])
    today = datetime.date.today().isoformat()

    lines = ['(export (version "E")',
             f'  (design',
             f'    (source "design.json")',
             f'    (date "{today}")',
             f'    (tool "workbuddy-agent"))']

    # 元件段
    lines.append("  (components")
    for c in comps:
        lib, part = lib_for(c)
        ref = c["ref"]
        val = c.get("value", "")
        fp = c.get("package", "")
        lines.append(f'    (comp (ref "{ref}") (value "{val}") (footprint "{fp}")')
        lines.append(f'      (libsource (lib "{lib}") (part "{part}")))')
    lines.append("  )")

    # 网络段
    lines.append("  (nets")
    for i, net in enumerate(nets, start=1):
        name = net.get("name", f"N${i}")
        lines.append(f'    (net (code "{i}") (name "{name}")')
        for node in net.get("nodes", []):
            lines.append(f'      (node (ref "{node.get("ref","")}") (pin "{node.get("pin","")}"))')
        lines.append("    )")
    lines.append("  )")

    lines.append(")")
    return "\n".join(lines) + "\n"


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else "outputs/design.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "outputs/design.net"
    with open(in_path, encoding="utf-8") as f:
        design = json.load(f)
    netlist = build_netlist(design)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(netlist)
    print(f"[已生成网表] {out_path}  ({len(design.get('components', []))} 元件, "
          f"{len(design.get('nets', []))} 网络)")


if __name__ == "__main__":
    main()
