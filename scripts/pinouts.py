"""常用芯片的「信号名 -> 引脚号」权威映射（按所用 KiCad 符号的真实封装编号）。

为什么需要它：LLM 生成引脚时常按错的封装套号（例如把 28 脚 DIP 的脚号塞进 32 脚
QFN/TQFP 的符号里，导致 RESET 接到 PD3、晶振接到 VCC 之类）。这里按"信号名"强制纠正，
保证 design.json 与生成的 .kicad_sch 都用正确脚号。

后续要支持更多芯片，只需在 PINOUTS 里加一条即可。
"""

# 每个芯片：识别关键字 -> {signals: {信号名: [脚号...]}}
# 脚号以 KiCad 符号 ATmega328P-M（QFN-32/TQFP-32）的真实物理编号为基准。
PINOUTS = {
    "ATmega328P": {
        "package": "QFN-32 / TQFP-32 (KiCad: ATmega328P-M)",
        "signals": {
            # 数字电源 VCC（pin 4、6）；pin 18 是 AVCC(模拟电源)，单独成网
            "VCC": [4, 6],
            "AVCC": [18],
            "GND": [3, 5, 21, 33],
            # 复位（PC6/RESET，注意不是 1 脚！1 脚是 PD3）
            "RESET": [29],
            # 外部晶振（PB6/PB7）
            "XTAL1": [7],
            "XTAL2": [8],
            # SPI / ISP 下载接口（PB3/PB4/PB5）
            "MOSI": [15], "ISP_MOSI": [15],
            "MISO": [16], "ISP_MISO": [16],
            "SCK": [17], "ISP_SCK": [17],
        },
    },
}

# 网络名 -> 信号别名：让 "VCC"/"+5V"/"5V" 等都归一到对应信号脚。
ALIASES = {
    "VCC": "VCC", "+5V": "VCC", "5V": "VCC", "VDD": "VCC",
    "AVCC": "AVCC",
    "GND": "GND", "VSS": "GND",
    "RESET": "RESET", "RST": "RESET", "MR": "RESET",
    "XTAL1": "XTAL1", "OSC1": "XTAL1", "CLKIN": "XTAL1",
    "XTAL2": "XTAL2", "OSC2": "XTAL2", "CLKOUT": "XTAL2",
    "MOSI": "MOSI", "ISP_MOSI": "MOSI", "SIMO": "MOSI",
    "MISO": "MISO", "ISP_MISO": "MISO", "SOMI": "MISO",
    "SCK": "SCK", "ISP_SCK": "SCK", "SCLK": "SCK", "CLK": "SCK",
}

# 芯片识别：value 字段里包含这些关键字即视为该芯片。
CHIP_KEYS = {
    "ATmega328P": ["ATMEGA328P", "ATMEGA328", "MEGA328P", "MEGA328"],
}


def _chip_key(value):
    if not value:
        return None
    v = value.upper()
    for key, kws in CHIP_KEYS.items():
        if any(k.upper() in v for k in kws):
            return key
    return None


def correct_design_pins(design: dict) -> dict:
    """对已知芯片，按信号名纠正网表里错误的引脚号。原地修改并返回 design。"""
    comps = design.get("components", [])
    nets = design.get("nets", [])
    fix_chips = {}  # ref -> {信号: [脚号]}
    for c in comps:
        key = _chip_key(c.get("value", ""))
        if key:
            fix_chips[c["ref"]] = PINOUTS[key]["signals"]

    if not fix_chips:
        return design

    for net in nets:
        name = (net.get("name") or "").strip().upper()
        sig = ALIASES.get(name)
        if not sig:
            continue
        keep = []                       # 非已知芯片的节点原样保留
        expand = {}                     # ref -> set(脚号)，每个芯片在一个信号网上只展开一次
        for node in net.get("nodes", []):
            ref = node.get("ref")
            if ref in fix_chips and sig in fix_chips[ref]:
                expand.setdefault(ref, set()).update(fix_chips[ref][sig])
            else:
                keep.append(node)
        new_nodes = list(keep)
        for ref, pins in expand.items():
            for p in sorted(pins, key=lambda x: int(x)):
                new_nodes.append({"ref": ref, "pin": str(p)})
        net["nodes"] = new_nodes
    return design
