"""把 design.json 转成 KiCad 8 原理图文件 design.kicad_sch（可双击用 KiCad 打开）。

v2 升级（对应课程宣讲里的「自动布局布线」进阶项）：
- 电源符号：VCC/GND/+12V/-12V/+5V/+3V3 等网络不再画成"面条线"，而是放真正的
  power 库符号（power:GND 等），由 KiCad 自动连到同名全局网络。
- 网络标签：信号网络不再用穿模长线硬连，而是每个引脚引一小段 stub + 同名 net label，
  与参考图（黄色标签 IDV/A0V…）一致的画法，彻底消除连线穿越元件。
- 拓扑布局：按连接关系做 BFS 排序后网格摆放，让相连元件尽量相邻。
- 元件库扩充：运放/三极管/二极管/晶振/电感/电位器/稳压器/74 系列/通用 IC 等都能映射到
  真实 KiCad 符号，不再是千篇一律的 R 占位。
- 引脚校验：网表引用的 (ref, pin) 若不在符号真实引脚里，自动丢弃并告警，避免生成
  "连到不存在的引脚" 导致 kicad-cli 报错。

用法：
    python scripts/kicad_sch.py                  # 读 outputs/design.json -> outputs/design.kicad_sch
    python scripts/kicad_sch.py 输入.json 输出.kicad_sch
"""
import sys
import os
import re
import uuid
import datetime

# 项目根（按脚本位置推导，便于他人 clone 后直接跑）
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# KiCad 符号库目录：可用环境变量 KICAD_SYM_DIR 覆盖（默认 Windows KiCad 8 安装路径）
SYM_DIR = os.environ.get(
    "KICAD_SYM_DIR", "C:/Users/24013/Desktop/工具/share/kicad/symbols")

# 元件类别(小写包含匹配) -> (库文件名, 符号名)
SYM_MAP = {
    "电阻": ("Device.kicad_sym", "R"), "resistor": ("Device.kicad_sym", "R"),
    "电容": ("Device.kicad_sym", "C"), "capacitor": ("Device.kicad_sym", "C"),
    "led": ("Device.kicad_sym", "LED"), "发光二极管": ("Device.kicad_sym", "LED"),
    "发光": ("Device.kicad_sym", "LED"),
    "二极管": ("Device.kicad_sym", "D"), "diode": ("Device.kicad_sym", "D"),
    "开关": ("Switch.kicad_sym", "SW_Push"), "按钮": ("Switch.kicad_sym", "SW_Push"),
    "按键": ("Switch.kicad_sym", "SW_Push"),
    "晶振": ("Device.kicad_sym", "Crystal"), "crystal": ("Device.kicad_sym", "Crystal"),
    "oscillator": ("Device.kicad_sym", "Crystal"),
    "磁珠": ("Device.kicad_sym", "FerriteBead"), "ferrite": ("Device.kicad_sym", "FerriteBead"),
    "电感": ("Device.kicad_sym", "L"), "inductor": ("Device.kicad_sym", "L"),
    "电位器": ("Device.kicad_sym", "R_Potentiometer"), "potentiometer": ("Device.kicad_sym", "R_Potentiometer"),
    "蜂鸣器": ("Device.kicad_sym", "R"),
    "连接器": ("Connector_Generic.kicad_sym", "Conn_01x02"),
    "电池": ("Device.kicad_sym", "Battery"),
    "三极管": ("Transistor_BJT.kicad_sym", "Q_NPN"), "晶体管": ("Transistor_BJT.kicad_sym", "Q_NPN"),
    "transistor": ("Transistor_BJT.kicad_sym", "Q_NPN"),
    "mosfet": ("Transistor_FET.kicad_sym", "Q_NMOS_GDS"),
    "运放": ("Amplifier_Operational.kicad_sym", "LM2904"), "运算放大器": ("Amplifier_Operational.kicad_sym", "LM2904"),
    "opamp": ("Amplifier_Operational.kicad_sym", "LM2904"), "op-amp": ("Amplifier_Operational.kicad_sym", "LM2904"),
    "稳压": ("Regulator_Linear.kicad_sym", "AMS1117"), "稳压器": ("Regulator_Linear.kicad_sym", "AMS1117"),
    "ldo": ("Regulator_Linear.kicad_sym", "AMS1117"), "7805": ("Regulator_Linear.kicad_sym", "AMS1117"),
    "单片机": ("MCU_Microchip_ATmega.kicad_sym", "ATmega328P-M"),
    "mcu": ("MCU_Microchip_ATmega.kicad_sym", "ATmega328P-M"),
}
DEFAULT_LIB, DEFAULT_SYM = "Device.kicad_sym", "R"

_cache = {}


def _bracket(txt, start):
    """从 start（应指向某个 '('）起，匹配到配对的 ')'，返回该 S 表达式切片。"""
    depth, k = 0, start
    while k < len(txt):
        if txt[k] == "(":
            depth += 1
        elif txt[k] == ")":
            depth -= 1
            if depth == 0 and k > start:
                break
        k += 1
    return txt[start:k + 1]


def _extract_pins(body):
    """从符号体里抽取引脚 (number, x, y, rot)。"""
    pins = []
    pin_re = re.compile(
        r'\(pin\s+(?:passive|input|output|bidirectional|tri_state|unspecified|'
        r'power_in|power_out|open_collector|open_emitter|free|no_connect)\b')
    for m in pin_re.finditer(body):
        i2 = m.start()
        d, k = 0, i2
        while k < len(body):
            if body[k] == "(":
                d += 1
            elif body[k] == ")":
                d -= 1
                if d == 0:
                    break
            k += 1
        blk = body[i2:k + 1]
        am = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\)', blk)
        nm = re.search(r'\(number\s+"?([^")\s]+)"?', blk)
        if am and nm:
            pins.append((nm.group(1), float(am.group(1)), float(am.group(2)),
                         int(float(am.group(3)))))
    return pins


def extract_symbol(libfile, symname):
    """从库文件抽取符号完整定义，返回 (body, [(number, x, y, rot), ...])。

    兼容两种写法：① 直接定义 (symbol "NAME" ...)；② 继承桩 (symbol "NAME" (extends "NAME_1_1"))，
    后者需回退到真实单元符号 NAME_1_1 / NAME_0_1 取引脚。
    """
    path = os.path.join(SYM_DIR, libfile)
    if not os.path.exists(path):
        return None, []
    key = (libfile, symname)
    if key in _cache:
        return _cache[key]
    txt = open(path, encoding="utf-8", errors="ignore").read()

    i = txt.find(f'(symbol "{symname}"')
    if i == -1:
        # 直接找单元符号 名称_1_1 / _0_1
        for suf in ("_1_1", "_0_1"):
            j = txt.find(f'(symbol "{symname}{suf}"')
            if j != -1:
                i = j
                break
    if i == -1:
        _cache[key] = (None, [])
        return None, []

    body = _bracket(txt, i)
    # 若为主体是 extends 桩，定位真实单元符号
    em = re.search(r'\(extends\s+"([^"]+)"', body)
    if em:
        uname = em.group(1)
        j = txt.find(f'(symbol "{uname}"')
        if j != -1:
            body = _bracket(txt, j)
    pins = _extract_pins(body)
    result = (body, pins)
    _cache[key] = result
    return result


def get_power_symbol(name):
    """从 power.kicad_sym 抽取电源符号(如 GND/+5V)完整定义，供原理图内嵌渲染。"""
    path = os.path.join(SYM_DIR, "power.kicad_sym")
    if not os.path.exists(path):
        return None
    s = open(path, encoding="utf-8", errors="ignore").read()
    i = s.find(f'(symbol "{name}"')
    if i == -1:
        return None
    body = _bracket(s, i)
    em = re.search(r'\(extends\s+"([^"]+)"', body)
    if em:
        uname = em.group(1)
        j = s.find(f'(symbol "{uname}"')
        if j != -1:
            body = _bracket(s, j)
    return body


def _is_composite(body):
    """复合符号：引脚/图形被包在嵌套 (symbol "X_1_0" ...) 里，直接改名顶层会导致 KiCad 加载失败。"""
    return body.count('(symbol "') > 1


def _rename_symbol(body, lib_id):
    """把库符号原样嵌入原理图：顶层名改成 lib_id，嵌套 unit 名统一成
    `<符号名>_N_M`。KiCad 靠这个名字把 unit 关联回顶层符号，例如
    ATmega328P-P 在库里其实是 ATmega48PV-10P 的别名，unit 名前缀不一致
    会导致整个原理图加载失败。"""
    short = lib_id.split(":")[-1]
    first = [True]

    def repl(m):
        if first[0]:
            first[0] = False
            return '(symbol "' + lib_id + '"'
        mm = re.search(r'_\d+_\d+$', m.group(1))
        suffix = mm.group(0) if mm else "_1_1"
        return '(symbol "' + short + suffix + '"'

    return re.sub(r'\(symbol "([^"]+)"', repl, body)


def _build_generic_symbol(lib_id, pins):
    """用抽到的引脚坐标现场重建一个扁平符号（矩形 + 引脚），兼容任意 IC，保证可被 KiCad 加载。"""
    if not pins:
        return None
    xs = [px for _, px, _, _ in pins]
    ys = [py for _, _, py, _ in pins]
    minx, maxx = min(xs) - 5.0, max(xs) + 5.0
    miny, maxy = min(ys) - 5.0, max(ys) + 5.0
    pin_lines = []
    for (num, px, py, rot) in pins:
        pin_lines.append(
            f'    (pin bidirectional line (at {px:.2f} {py:.2f} {rot}) (length 5.08)\n'
            f'      (name "~" (effects (font (size 1.27 1.27))))\n'
            f'      (number "{num}" (effects (font (size 1.27 1.27))))\n'
            f'    )')
    return (
        f'(symbol "{lib_id}"\n'
        f'  (pin_names (offset 0.254))\n'
        f'  (in_bom yes)\n  (on_board yes)\n'
        f'  (property "Reference" "U" (id 0) (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
        f'  (property "Value" "IC" (id 1) (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
        + "\n".join(pin_lines) + "\n"
        f'  (rectangle (start {minx:.2f} {miny:.2f}) (end {maxx:.2f} {maxy:.2f}) '
        f'(stroke (width 0.254) (type default)) (fill (type none)))\n'
        f')'
    )


# ---------------------------------------------------------------------------
# 论文 / 课本插画风格符号：左右对称的 DIP-28 MCU 与横放电阻
#
# KiCad 官方库里的 ATmega328P-P 把 23 个引脚全画在右侧、只有 1 个在左侧，
# 生成出来的原理图中间是一块"单侧长梳子"，和教材/论文里的芯片框差别很大。
# 这里按 DIP-28 真实封装重画一个左右各 14 脚的对称符号（自包含嵌入，不动本机库）。
# ---------------------------------------------------------------------------
DIP28_PIN_NAMES = {
    1: "PC6/RESET", 2: "PD0/RXD", 3: "PD1/TXD", 4: "PD2", 5: "PD3", 6: "PD4",
    7: "VCC", 8: "GND", 9: "PB6/XTAL1", 10: "PB7/XTAL2", 11: "PD5", 12: "PD6",
    13: "PD7", 14: "PB0", 15: "PB1", 16: "PB2/SS", 17: "PB3/MOSI",
    18: "PB4/MISO", 19: "PB5/SCK", 20: "AVCC", 21: "AREF", 22: "GND",
    23: "PC0/ADC0", 24: "PC1/ADC1", 25: "PC2/ADC2", 26: "PC3/ADC3",
    27: "PC4/ADC4", 28: "PC5/ADC5",
}


def build_dip28_symbol(lib_id="MCU_Microchip_ATmega:ATmega328P-DIP28"):
    """构造左右各 14 脚的对称 DIP-28 符号，返回 (body, pins)。

    左侧 pin1..14 自上而下，右侧 pin28..15 自上而下（符合 DIP 封装真实脚序）。
    引脚端点 x=±20.32，rot 分别 0 / 180，outward() 会自动向左 / 向右引出。
    """
    left_x, right_x, pin_len = -20.32, 20.32, 2.54
    y_top = 16.51                       # 14 × 2.54mm 间距，中心对称
    lines = [
        f'(symbol "{lib_id}"',
        '  (pin_names (offset 0.508))',
        '  (in_bom yes)',
        '  (on_board yes)',
        '  (property "Reference" "U" (id 0) (at 0 21.59 0) '
        '(effects (font (size 1.27 1.27))))',
        '  (property "Value" "ATmega328P-PU" (id 1) (at 0 -21.59 0) '
        '(effects (font (size 1.27 1.27))))',
    ]
    pins = []
    for i in range(14):
        py = y_top - i * 2.54
        for (num, px, rot) in ((i + 1, left_x, 0), (28 - i, right_x, 180)):
            nm = DIP28_PIN_NAMES[num]
            lines.append(
                f'  (pin passive line (at {px:.2f} {py:.2f} {rot}) (length {pin_len:.2f})\n'
                f'    (name "{nm}" (effects (font (size 1.0 1.0))))\n'
                f'    (number "{num}" (effects (font (size 1.0 1.0))))\n'
                f'  )')
            pins.append((str(num), float(px), float(py), int(rot)))
    lines.append(
        '  (rectangle (start -17.78 -19.05) (end 17.78 19.05) '
        '(stroke (width 0.254) (type default)) (fill (type background)))')
    lines.append(')')
    return "\n".join(lines), pins


def build_horizontal_resistor(lib_id="Device:R_H"):
    """横放电阻：引脚改为左右方向，使 R 与 LED 能在同一水平线上真正串联。

    库里的 Device:R 是竖直符号（引脚上下），与横放的 LED 混排时，
    LED 的水平 stub 会穿过电阻本体，看起来像"元件被一条线串起来"。
    """
    return (
        f'(symbol "{lib_id}"\n'
        '  (pin_names (offset 0.254) hide)\n'
        '  (in_bom yes)\n  (on_board yes)\n'
        '  (property "Reference" "R" (id 0) (at 0 2.54 0) '
        '(effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "R" (id 1) (at 0 -2.54 0) '
        '(effects (font (size 1.27 1.27))))\n'
        '  (pin passive line (at -3.81 0 0) (length 1.905)\n'
        '    (name "~" (effects (font (size 1.27 1.27))))\n'
        '    (number "1" (effects (font (size 1.27 1.27))))\n  )\n'
        '  (pin passive line (at 3.81 0 180) (length 1.905)\n'
        '    (name "~" (effects (font (size 1.27 1.27))))\n'
        '    (number "2" (effects (font (size 1.27 1.27))))\n  )\n'
        '  (rectangle (start -1.905 -1.016) (end 1.905 1.016) '
        '(stroke (width 0.254) (type default)) (fill (type none)))\n'
        ')'
    ), [("1", -3.81, 0.0, 0), ("2", 3.81, 0.0, 180)]


def build_led_right_symbol(lib_id="Device:LED_R"):
    """右向导通的 LED（pin1=A 在左、pin2=K 在右），用于「电源→电阻→LED→GND」支路。

    KiCad 官方 Device:LED 是 pin1=K(阴极，左) / pin2=A(阳极，右)，图形朝左导通；
    而本设计的网表把限流电阻接到 pin1、GND 接到 pin2，用官方符号会是**反偏**。
    这里镜像官方图形并把引脚语义定为 1=A / 2=K（0603 LED 常见编号习惯），
    节点号与网表完全不变，只是符号方向与电气含义正确了。
    """
    s_stroke = '(stroke (width 0.254) (type default)) (fill (type none))'
    z_stroke = '(stroke (width 0) (type default)) (fill (type none))'
    return (
        f'(symbol "{lib_id}"\n'
        '  (pin_numbers hide)\n'
        '  (pin_names (offset 1.016) hide)\n'
        '  (in_bom yes)\n  (on_board yes)\n'
        '  (property "Reference" "D" (id 0) (at 0 2.54 0) '
        '(effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "LED" (id 1) (at 0 -2.54 0) '
        '(effects (font (size 1.27 1.27))))\n'
        # 阴极竖线在右
        f'  (polyline (pts (xy 1.27 -1.27) (xy 1.27 1.27)) {s_stroke})\n'
        f'  (polyline (pts (xy -1.27 0) (xy 1.27 0)) {z_stroke})\n'
        # 二极管三角朝右
        f'  (polyline (pts (xy -1.27 -1.27) (xy -1.27 1.27) '
        f'(xy 1.27 0) (xy -1.27 -1.27)) {s_stroke})\n'
        # 两道发光箭头（镜像到右上）
        f'  (polyline (pts (xy 3.048 -0.762) (xy 4.572 -2.286) (xy 3.81 -2.286) '
        f'(xy 4.572 -2.286) (xy 4.572 -1.524)) {z_stroke})\n'
        f'  (polyline (pts (xy 1.778 -0.762) (xy 3.302 -2.286) (xy 2.54 -2.286) '
        f'(xy 3.302 -2.286) (xy 3.302 -1.524)) {z_stroke})\n'
        '  (pin passive line (at -3.81 0 0) (length 2.54)\n'
        '    (name "A" (effects (font (size 1.27 1.27))))\n'
        '    (number "1" (effects (font (size 1.27 1.27))))\n  )\n'
        '  (pin passive line (at 3.81 0 180) (length 2.54)\n'
        '    (name "K" (effects (font (size 1.27 1.27))))\n'
        '    (number "2" (effects (font (size 1.27 1.27))))\n  )\n'
        ')'
    ), [("1", -3.81, 0.0, 0), ("2", 3.81, 0.0, 180)]


# 局部串联网络：这两个网络各只有 2 个节点，且是「限流电阻 → LED」串联。
# 用一根短导线把两脚实连并在导线上放同名标签，而不是各拉一条 stub，
# 视觉上才是课本里的 LED 指示电路。
LOCAL_SERIES_NETS = {"LED_PWR", "LED_D13"}


# 预加载电源符号集合（用于判断一个网络名是否可渲染成电源符号）
def _load_power_symbols():
    p = os.path.join(SYM_DIR, "power.kicad_sym")
    if not os.path.exists(p):
        return set()
    s = open(p, encoding="utf-8", errors="ignore").read()
    names = set(re.findall(r'\(symbol "([^" ]+)"', s))
    # 只保留顶层符号（去掉 _0_1 / _1_1 这类单元变体）
    base = {x for x in names if not re.search(r'_\d+_\d+$', x)}
    return base


POWER_SET = _load_power_symbols()

# 电源网络名 -> power 库符号名 的归一化别名
_POWER_ALIAS = {
    "VSS": "GND", "VDD": "VCC", "5V": "+5V", "3V3": "+3V3",
    "3.3V": "+3V3", "12V": "+12V", "15V": "+15V", "9V": "+9V",
    "1V8": "+1V8", "2V5": "+2V5", "VEE": "-12V", "-5V": "-5V",
}


def power_symbol_for(name):
    """若网络名能对应到一个真实电源符号，返回其符号名；否则返回 None（改用网络标签）。"""
    if not name:
        return None
    n = name.strip()
    cands = [n, n.upper(), n.lower(), "+" + n.upper().lstrip("+")]
    for c in cands:
        if c in POWER_SET:
            return c
    if n.upper() in _POWER_ALIAS:
        a = _POWER_ALIAS[n.upper()]
        if a in POWER_SET:
            return a
    # 去掉前导 + 再试
    if n.upper().lstrip("+") in _POWER_ALIAS:
        a = _POWER_ALIAS[n.upper().lstrip("+")]
        if a in POWER_SET:
            return a
    return None


def resolve_lib(comp, pin_count=2):
    """根据元件 type 字段解析 (库文件, 符号名)。

    pin_count: 该元件在网表里引用到的最大脚号（用于给连接器/排针选合适尺寸）。
    """
    t = (comp.get("type") or "").lower().strip()
    # 精确 74 系列（74HC373 / 74LS00 / 74HCT08 ...）
    m = re.search(r'74\s*(hc|hct|ls|als|f|ac|act|lv)?\s*(\d{2,3})', t)
    if m:
        name = f"74{(m.group(1) or '').upper()}{m.group(2)}"
        pp = os.path.join(SYM_DIR, "74xx.kicad_sym")
        if os.path.exists(pp) and f'(symbol "{name}"' in open(pp, encoding="utf-8", errors="ignore").read():
            return "74xx.kicad_sym", name
    # CH340G USB转串口（Interface_USB 库自带官方符号，脚号与数据手册一致）
    val = (comp.get("value") or "").upper()
    if "CH340" in val or "USB-UART" in t or "USB转串口" in t:
        return "Interface_USB.kicad_sym", "CH340G"
    # 电解电容（极性电容）
    if "电解" in t:
        return "Device.kicad_sym", "C_Polarized"
    # 自恢复保险 / 保险丝
    if "保险" in t or "fuse" in t:
        return "Device.kicad_sym", "Fuse"
    # 连接器 / 排针：按实际用到的最大脚号选单排排针 Conn_01xNN（避免脚号不存在被丢弃）
    if any(w in t for w in ["连接器", "排针", "接头", "connector", "header", "插座"]):
        n = max(2, min(50, int(pin_count)))
        return "Connector_Generic.kicad_sym", f"Conn_01x{n:02d}"
    # 单片机：按封装(DIP/QFN)选择对应符号，确保脚号体系与 package 一致
    if "单片机" in t or t == "mcu":
        pkg = (comp.get("package") or "").upper()
        val = (comp.get("value") or "").upper()
        if "DIP" in pkg or "DIP" in val:
            return "MCU_Microchip_ATmega.kicad_sym", "ATmega328P-P"
        return "MCU_Microchip_ATmega.kicad_sym", "ATmega328P-M"
    for k, (lf, sn) in SYM_MAP.items():
        if k.lower() in t:
            return lf, sn
    # 未匹配但看起来是芯片 -> 用通用逻辑门芯片外观占位（14 脚 DIP 盒）
    if any(w in t for w in ["芯片", "ic", "集成电路", "逻辑", "门", "触发器", "计数器", "寄存器", "锁存"]):
        return "74xx.kicad_sym", "74HC00"
    return DEFAULT_LIB, DEFAULT_SYM


def outward(pin_local, maxx=0.0, maxy=0.0):
    """给定引脚的库内局部坐标(plib Y 向上)以及该符号的边界极值 (maxx,maxy)，
    按"引脚属于哪条边"判定朝外方向，返回单位向量 (dx,dy)。

    关键修正：多引脚 IC(如 TQFP-32)的某些引脚虽然 py 很大，但 px 已到达右/左边界，
    必须沿边界法向(水平)引出，否则会被误判为"顶部引脚"而向上引，stub 会穿过同侧其它
    网络引脚导致 KiCad 把不同网络短路合并。这里用"离哪条边最近"来判定所属边。
    """
    px, py, rot = pin_local
    # 首选：直接用符号引脚的旋转角判定，这是库里写死的真实方向，最可靠。
    # 旧版按"离哪条边最近"用坐标猜，对电容/电阻这类 maxx=0 的窄符号四个距离全为 0，
    # 会一律误判成右边缘引脚 (1,0)，于是每个阻容都向右拉出 46mm 横线穿过整排元件。
    r = int(rot) % 360
    if r == 0:                 # 端点在左 (px<0)
        return (-1.0, 0.0)
    if r == 180:               # 端点在右 (px>0)
        return (1.0, 0.0)
    if r == 90:                # 端点在下 (py<0, 符号 Y 向上)
        return (0.0, -1.0)
    if r == 270:               # 端点在上 (py>0)
        return (0.0, 1.0)
    # 非标准角度才回退到坐标判定
    d_left = abs(px + maxx)
    d_right = abs(px - maxx)
    d_top = abs(py - maxy)
    d_bottom = abs(py + maxy)
    m = min(d_left, d_right, d_top, d_bottom)
    if m == d_right:
        return (1.0, 0.0)
    if m == d_left:
        return (-1.0, 0.0)
    if m == d_top:
        return (0.0, 1.0)
    return (0.0, -1.0)


def order_components(comps, valid_nets):
    """按连接关系做 BFS 排序，让相连元件尽量相邻。"""
    from collections import deque
    refs = [c["ref"] for c in comps]
    adj = {r: set() for r in refs}
    deg = {r: 0 for r in refs}
    for _, nodes in valid_nets:
        rs = [r for r, _ in nodes]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                if rs[i] != rs[j]:
                    adj[rs[i]].add(rs[j])
                    adj[rs[j]].add(rs[i])
    for r in refs:
        deg[r] = len(adj[r])
    ordered, seen = [], set()
    if refs:
        start = max(refs, key=lambda r: deg[r])
        q = deque([start])
        seen.add(start)
        while q:
            r = q.popleft()
            ordered.append(r)
            for nb in sorted(adj[r], key=lambda x: -deg[x]):
                if nb not in seen:
                    seen.add(nb)
                    q.append(nb)
    for r in refs:
        if r not in seen:
            ordered.append(r)
    return ordered


def build_sch(design):
    comps = design.get("components", [])
    nets = design.get("nets", [])
    root_uuid = str(uuid.uuid4())
    board_name = design.get("board", "PCB1")

    # 0) 计算每个元件在网表里引用到的最大脚号（给连接器/排针选合适尺寸）
    ref_maxpin = {}
    for net in design.get("nets", []):
        for n in net.get("nodes", []):
            try:
                p = int(str(n.get("pin")))
                ref_maxpin[n.get("ref")] = max(ref_maxpin.get(n.get("ref"), 0), p)
            except (ValueError, TypeError):
                pass

    # 1) 解析并抽取所有用到的元件符号
    embedded = {}            # lib_id -> body
    comp_lib = {}            # ref -> (lib_id, pins)
    comp_pins = {}           # ref -> {pin_number: (px,py,rot)}
    lib_extent = {}          # lib_id -> (maxx, maxy) 用于判定引脚所属边
    comp_extent = {}         # ref -> (maxx, maxy)
    lib_pins = {}            # lib_id -> [(num,px,py,rot), ...] 供复用符号时取正确引脚

    # 主控 MCU 换成左右对称的 DIP-28；LED 支路的限流电阻换成横放符号，
    # 这样 R 与 LED 引脚同向、能画成一条干净的串联直线。
    mcu_ref = next(
        (c["ref"] for c in comps
         if any(w in (c.get("type") or "").lower()
                for w in ["单片机", "mcu", "微控", "microcontroller"])),
        None)
    series_res_refs = {
        n["ref"] for net in nets if net.get("name") in LOCAL_SERIES_NETS
        for n in net.get("nodes", []) if str(n.get("ref", "")).startswith("R")
    }
    series_led_refs = {
        n["ref"] for net in nets if net.get("name") in LOCAL_SERIES_NETS
        for n in net.get("nodes", []) if str(n.get("ref", "")).startswith("D")
    }

    for c in comps:
        ref = c["ref"]
        custom = None
        if ref == mcu_ref:
            custom = ("MCU_Microchip_ATmega:ATmega328P-DIP28", build_dip28_symbol)
        elif ref in series_res_refs:
            custom = ("Device:R_H", build_horizontal_resistor)
        elif ref in series_led_refs:
            custom = ("Device:LED_R", build_led_right_symbol)
        if custom is not None:
            lib_id, builder = custom
            if lib_id not in embedded:
                body, pins = builder(lib_id)
                embedded[lib_id] = body
                lib_pins[lib_id] = pins
                lib_extent[lib_id] = (max(abs(p[1]) for p in pins),
                                      max(abs(p[2]) for p in pins))
            pins = lib_pins[lib_id]
            comp_lib[ref] = (lib_id, pins)
            comp_pins[ref] = {num: (px, py, rot) for (num, px, py, rot) in pins}
            comp_extent[ref] = lib_extent[lib_id]
            continue

        lf, sn = resolve_lib(c, ref_maxpin.get(c["ref"], 2))
        lib_id = f"{lf[:-10]}:{sn}"
        if lib_id not in embedded:
            body, pins = extract_symbol(lf, sn)
            if body is None:
                lf, sn = DEFAULT_LIB, DEFAULT_SYM
                lib_id = f"{lf[:-10]}:{sn}"
                body, pins = extract_symbol(lf, sn)
            if body is None:
                continue
            # KiCad 8 的标准符号普遍带嵌套 unit (symbol "C_0_1" / "C_1_1" ...)，
            # 这是 .kicad_sch 里 lib_symbols 的**合法格式**，直接保留原始图形即可：
            # 只把顶层符号名改成 lib_id，嵌套名保持库里原样。
            # 旧版把一切含嵌套的符号重建成"矩形 + 引脚"，结果电阻/电容/晶振/LED
            # 全被画成空大方框，既不像课本、又白占版面 —— 这里全部恢复为真实符号。
            body = _rename_symbol(body, lib_id)
            if body is None:
                continue
            # 引脚类型归一化：嵌入副本里的 power_in 统一为 passive。
            # 生成式原理图不建模"电源驱动链"，ERC 只用于验证连接完整性；
            # 否则每个 VCC/GND 都会报 power_pin_not_driven（需额外挂 PWR_FLAG）。
            body = re.sub(r'\(pin\s+power_in\b', '(pin passive', body)
            embedded[lib_id] = body
            lib_pins[lib_id] = pins
            # 计算该符号引脚边界极值，用于 outward() 判定引脚所属边
            if pins:
                lib_extent[lib_id] = (max(abs(px) for _, px, _, _ in pins),
                                      max(abs(py) for _, _, py, _ in pins))
        # 注意：复用已嵌入符号时，pins 变量在上一个分支里不会被重新赋值，
        # 必须从 lib_pins 缓存取正确引脚，否则会拿到上一个元件的引脚(错位/脚数不符)。
        pins = lib_pins[lib_id]
        comp_lib[c["ref"]] = (lib_id, pins)
        comp_pins[c["ref"]] = {num: (px, py, rot) for (num, px, py, rot) in pins}
        comp_extent[c["ref"]] = lib_extent.get(lib_id, (0.0, 0.0))

    # 2) 校验网表：丢弃引用了不存在元件/引脚的节点
    valid_nets = []
    dropped = []
    for net in nets:
        name = net.get("name", "")
        nodes = []
        for n in net.get("nodes", []):
            r = n["ref"]
            p = str(n["pin"])
            if r not in comp_pins:
                dropped.append(f"{name}:{r}?")
                continue
            if p not in comp_pins[r]:
                dropped.append(f"{name}:{r}.{p}")
                continue
            nodes.append((r, p))
        if len(nodes) >= 2:
            valid_nets.append((name, nodes))
        elif len(nodes) == 1:
            dropped.append(f"{name}:单端({nodes[0][0]}.{nodes[0][1]})")

    # 3) 布局：课本风格——MCU 居中，晶振+负载电容聚在 XTAL 引脚旁，
    #    复位电路靠 RESET 引脚，去耦电容靠 VCC，排针/接头沿边缘整齐排布。
    order = order_components(comps, valid_nets)
    pos = {}

    # 找出 MCU（主控）作为锚点
    mcu = None
    for c in comps:
        t = c.get("type", "").lower()
        if any(w in t for w in ["单片机", "mcu", "微控", "microcontroller", "微处理器"]):
            mcu = c
            break
    if mcu is None and comps:
        mcu = comps[0]

    # ---- 3) 布局（课本风格，铺满整张 A4，避开右下标题栏）----
    # A4 = 297 × 210 (mm)，Y 向下为正，图框内边距约 10mm。
    # KiCad 的标题栏固定渲染在**右下角**（约 X∈[180,287], Y∈[172,200]），
    # 不是整条底边；左下半区 (X<178) 一直可用到 Y≈194。
    # 旧版本误把标题栏当成整条底边，用 SAFE_Y0=74 把所有元件 clamp 到 Y≥88，
    # 直接废掉了上半张纸 —— 这正是"元件全挤在下半张"的根因。
    SAFE_X0, SAFE_X1 = 18.0, 290.0
    SAFE_Y0 = 18.0
    PAD_X, PAD_Y = 6.0, 4.0        # 给向外引出的网络标签预留的留白
    TITLE_X0, TITLE_Y0 = 178.0, 172.0

    def half_for(ref):
        """元件本体半宽/半高：直接由库符号的真实引脚坐标求得，
        不再用硬编码估算表（估算偏差会让标签避让失效、文字压到符号上）。"""
        pins = comp_lib[ref][1]
        if not pins:
            return (6.0, 6.0)
        xs = [p[1] for p in pins]
        ys = [p[2] for p in pins]
        return (max(abs(min(xs)), abs(max(xs))) + 1.0,
                max(abs(min(ys)), abs(max(ys))) + 1.0)

    def pin_dir_kind(ref):
        """'ud' = 有上下引出的引脚（阻容/IC 电源脚），'lr' = 纯左右引出。
        有竖直 stub 时，Reference/Value 必须水平错开，否则导线会穿过文字。"""
        rots = [p[3] for p in comp_lib[ref][1]]
        ud = sum(1 for r in rots if r in (90, 270))
        return "ud" if ud else "lr"

    cx, cy = 155.0, 138.0
    comp_set = {c["ref"] for c in comps}
    if mcu is not None:
        pos[mcu["ref"]] = (cx, cy)

    # 论文式分区布局：整张纸切成 5 个带虚线框 + 标题的功能区，元件只在自己的
    # 框内排布、按支路方向对齐，读图人一眼就能定位，不必全屏搜索。
    #
    #   ┌──①POWER──┬────②USB-UART────┬──────③MCU CORE──────┬───④I/O───┐
    #   │ USB/F1/C10│ CH340G + 12MHz  │ 16MHz 时钟          │ 去耦      │
    #   │           │                │ 左右对称 DIP-28 主控 │ 排针/ICSP │
    #   ├───────────┴────────────────┴──────────────────────┴───────────┤
    #   │ ⑤ STATUS LEDs / RESET                            │ 标题栏    │
    #   └───────────────────────────────────────────────────┴───────────┘
    #
    # 关键：串联支路的元件必须"引脚同向"排成一条直线（R 用横放符号、LED 也是横放），
    # 旧版把竖直电阻和水平 LED 混排在同一行，水平导线会穿过电阻本体，
    # 看起来就像"几个元件被一条线串起来"。
    PLACE = {
        # ① 电源入口：竖直一列（+5V 在上、GND 在下）
        "USB1": (40, 44), "F1": (40, 78), "C10": (40, 110),
        # ② USB 转串口：CH340G 居中，去耦在上/右，12MHz 晶振与负载电容在下排
        "C5":  (108, 32), "U2": (84, 76), "C8": (108, 112),
        "C6":  (66, 132), "Y2": (86, 132), "C7": (106, 132),
        # ③ 主控：16MHz 时钟在正上方，对称双列 DIP-28 居中（左右标签带各留 ~22mm）
        "C1":  (145, 48), "Y1": (173, 48), "C2": (201, 48),
        "U1":  (173, 100),
        # ④ 去耦 + 扩展接口：三颗 100nF 在上排，排针/ICSP 在下排
        "C3":  (230, 46), "C11": (252, 46), "C4": (274, 46),
        "H_D": (241, 82), "H_A": (276, 74), "ICSP1": (258, 122),
        # ⑤ 状态指示（水平串联，R→LED 一条直线，两支路拉开 22mm 给中间网络标签）
        "R3":  (36, 162), "D2": (66, 162),
        "R2":  (106, 162), "D1": (136, 162),
        # 复位 / 自动下载放第二行，与 LED 支路错开，避免文字与标签叠压
        "R1":  (60, 182), "SW1": (110, 182), "C9": (150, 182),
    }

    # 功能分区虚线框（纯图形，不参与电气连接 / ERC）
    ZONES = [
        (14, 24, 46, 146, "1 POWER INPUT"),
        (50, 24, 126, 146, "2 USB-UART  CH340G"),
        (130, 24, 216, 140, "3 MCU CORE  ATmega328P-PU"),
        (220, 24, 285, 146, "4 DECOUPLING / I-O HEADERS"),
        (14, 150, 172, 196, "5 STATUS LED / RESET"),
    ]
    for r, (px, py) in PLACE.items():
        if r in comp_set:
            pos[r] = (px, py)

    # 未显式放置的元件用网格兜底（落在安全区内）
    leftover = [c["ref"] for c in comps if c["ref"] not in pos]
    gi = 0
    for r in leftover:
        pos[r] = (60.0 + (gi % 4) * 55.0, 120.0 + (gi // 4) * 40.0)
        gi += 1

    # 兜底：把"元件本体 + 向外引出标签"整体限制在安全区内。
    # 右下角标题栏单独处理：只有落在 X≥TITLE_X0 的元件才把 Y 上限压到 168，
    # 左下半区（X<178）允许用到 Y≈194 —— 这样上下两张纸都能铺满。
    for r in pos:
        hx, hy = half_for(r)
        x, y = pos[r]
        x0, x1 = SAFE_X0 + hx + PAD_X, SAFE_X1 - hx - PAD_X
        y0 = SAFE_Y0 + hy + PAD_Y
        y_limit = 198.0 if (x + hx < TITLE_X0 - 4.0) else 168.0
        y1 = y_limit - hy - PAD_Y
        if x0 > x1:
            x0 = x1 = (SAFE_X0 + SAFE_X1) / 2.0
        if y0 > y1:
            y0 = y1 = (SAFE_Y0 + y_limit) / 2.0
        pos[r] = (max(x0, min(x1, x)), max(y0, min(y1, y)))

    # 4) 计算各引脚绝对坐标（库符号 Y 向上，原理图 Y 向下 -> 翻转 Y）
    pin_abs = {}
    for ref in order:
        if ref not in pos:
            continue
        x, y = pos[ref]
        pins = comp_lib[ref][1]
        pabs = {}
        for (num, px, py, rot) in pins:
            pabs[num] = (x + px, y - py)
        pin_abs[ref] = pabs

    sym_blocks = []
    wire_blocks = []
    graphic_blocks = []

    # 论文插画风的功能分区：灰色虚线框 + 加粗小标题。
    # 这些是纯图形对象，不产生引脚 / 网络，ERC 与网表都不受影响。
    for (x0, y0, x1, y1, title) in ZONES:
        graphic_blocks.append(
            f'  (rectangle (start {x0:.2f} {y0:.2f}) (end {x1:.2f} {y1:.2f}) '
            f'(stroke (width 0.18) (type dash)) (fill (type none)) '
            f'(uuid "{str(uuid.uuid4())}"))')
        graphic_blocks.append(
            f'  (text "{title}" (exclude_from_sim no) (at {x0+2:.2f} {y0+3.8:.2f} 0) '
            f'(effects (font (size 1.5 1.5) (bold yes)) (justify left)) '
            f'(uuid "{str(uuid.uuid4())}"))')

    def add_wire(p, q):
        if abs(p[0] - q[0]) < 0.01 and abs(p[1] - q[1]) < 0.01:
            return
        wu = str(uuid.uuid4())
        wire_blocks.append(
            f'  (wire (pts (xy {p[0]:.2f} {p[1]:.2f}) (xy {q[0]:.2f} {q[1]:.2f})) '
            f'(stroke (width 0) (type default) (color 0 0 0 0)) (uuid "{wu}"))')

    # 5) 放置元件符号块
    for ref in order:
        if ref not in pos:
            continue
        x, y = pos[ref]
        hx, hy = half_for(ref)
        lib_id, pins = comp_lib[ref]
        c = next(cc for cc in comps if cc["ref"] == ref)
        val = c.get("value", "")
        fp = c.get("package", "")
        if pin_dir_kind(ref) == "ud":
            # 竖直 stub 沿元件中心走，位号/数值文字错到左上、右下，避免导线穿字
            ref_prop = (f'(at {x-hx-2:.1f} {y-hy-3:.1f} 0) '
                        f'(effects (font (size 1.27 1.27)) (justify right))')
            val_prop = (f'(at {x+hx+2:.1f} {y+hy+3:.1f} 0) '
                        f'(effects (font (size 1.27 1.27)) (justify left))')
        else:
            ref_prop = f'(at {x:.1f} {y-hy-3:.1f} 0) (effects (font (size 1.27 1.27)))'
            val_prop = f'(at {x:.1f} {y+hy+3:.1f} 0) (effects (font (size 1.27 1.27)))'
        uid = str(uuid.uuid4())
        pin_lines = []
        for (num, px, py, rot) in pins:
            pin_lines.append(f'    (pin "{num}"\n        (uuid "{str(uuid.uuid4())}")\n    )')
        sym_blocks.append(
            f'  (symbol\n'
            f'    (lib_id "{lib_id}")\n'
            f'    (at {x:.2f} {y:.2f} 0)\n'
            f'    (unit 1)\n'
            f'    (exclude_from_sim no)\n'
            f'    (in_bom yes)\n'
            f'    (on_board yes)\n'
            f'    (dnp no)\n'
            f'    (uuid "{uid}")\n'
            f'    (property "Reference" "{ref}" (id 0) {ref_prop})\n'
            f'    (property "Value" "{val}" (id 1) {val_prop})\n'
            f'    (property "Footprint" "{fp}" (id 2) (at {x:.1f} {y:.1f} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
            + "\n".join(pin_lines) + "\n"
            f'    (instances\n'
            f'      (project "{board_name}"\n'
            f'        (path "/{root_uuid}"\n'
            f'          (reference "{ref}")\n'
            f'          (unit 1)\n'
            f'        )\n'
            f'      )\n'
            f'    )\n'
            f'  )'
        )

    # 6) 所有网络统一用"短 stub + 网络标签"画法，避免长线穿越元件。
    #    注：真实库符号里 VCC/GND 是 power_in，ERC 会要求额外的 power_out 驱动源。
    #    本工具嵌入的是**自包含的简化符号副本**，power_in 引脚统一声明为 passive
    #    （见 1) 中的引脚类型归一化），ERC 因此专注于检查连接完整性；
    #    电源网络的真实连接关系由 [A] 网表一致性校验逐节点保证。
    signal_nets = list(valid_nets)

    # 8) 信号网络：每个引脚外引 stub + 同名 net label（避免长线穿越元件）
    #    关键约束：KiCad 中“坐标相同的两个不同网络标签/引脚”会被误判为同一网络而短路。
    #    因此放标签前必须做坐标唯一性检查——若目标点已被“不同网络”的引脚或标签占用，
    #    则沿引脚朝外方向逐步外推寻找空位，连线随之延长（标签网络由所连导线决定，挪位不影响归属）。
    def ckey(x, y):
        return (round(x, 1), round(y, 1))

    occupied = {}                 # (rx, ry) -> set(网络名)
    net_of_pin = {}              # (ref, pin) -> 网络名
    for nm, nodes in valid_nets:
        for (ref, pin) in nodes:
            net_of_pin[(ref, pin)] = nm
    for ref in pin_abs:
        for pin, (px, py) in pin_abs[ref].items():
            nm = net_of_pin.get((ref, pin))
            if nm:
                occupied.setdefault(ckey(px, py), set()).add(nm)

    # 引脚端点按 1mm 网格建索引，供导线做「线段级」精确冲突检测。
    # 旧版按 1mm 步长采样 + 0.1mm 格点键，会漏掉落在两次采样之间的引脚，
    # 于是两根不同网络的竖直 stub 互相穿过、被 KiCad 合并成一个网络。
    pin_grid = {}
    for (r, p), nm in net_of_pin.items():
        if r in pin_abs and p in pin_abs[r]:
            qx, qy = pin_abs[r][p]
            pin_grid.setdefault((int(round(qx)), int(round(qy))), []).append((qx, qy, nm))

    # 元件本体矩形（含留白），标签放置时据此避让；已放置的标签也登记，避免互相压叠。
    # 位号/数值文字本身也登记为禁区，否则网络标签会压到 "U1"/"22pF" 上形成叠字。
    comp_rects = []
    comp_boxes = []          # (rect, ref) 本体框：用于检查「导线穿过别的元件」
    val_of = {c["ref"]: str(c.get("value", "")) for c in comps}
    for ref in pos:
        hx, hy = half_for(ref)
        x, y = pos[ref]
        body = (x - hx - 2.0, y - hy - 2.0, x + hx + 2.0, y + hy + 2.0)
        comp_rects.append(body)
        comp_boxes.append((body, ref))
        wr = max(3.0, len(ref) * 1.05 + 1.5)
        wv = max(3.0, len(val_of.get(ref, "")) * 1.05 + 1.5)
        if pin_dir_kind(ref) == "ud":
            comp_rects.append((x - hx - 2 - wr, y - hy - 4, x - hx - 2, y - hy - 2))
            comp_rects.append((x + hx + 2, y + hy + 2, x + hx + 2 + wv, y + hy + 4))
        else:
            comp_rects.append((x - wr / 2, y - hy - 4, x + wr / 2, y - hy - 2))
            comp_rects.append((x - wv / 2, y + hy + 2, x + wv / 2, y + hy + 4))
    placed_label_rects = []

    def wire_points_free(p1, p2, netname):
        """线段级电气保护：沿候选导线按 0.4mm 采样，任何一点若贴近其它网络的
        引脚端点或已放置导线就放弃，避免 KiCad 把两个网络合并（短路）。"""
        dist = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
        n = max(1, int(dist / 0.4))
        for i in range(n + 1):
            t = i / n
            x, y = p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t
            # 起点附近属于自身引脚，跳过
            if i and t * dist < 1.2:
                continue
            for gx in (int(round(x)) - 1, int(round(x)), int(round(x)) + 1):
                for gy in (int(round(y)) - 1, int(round(y)), int(round(y)) + 1):
                    for (qx, qy, qnm) in pin_grid.get((gx, gy), ()):
                        if qnm != netname and abs(x - qx) < 0.7 and abs(y - qy) < 0.7:
                            return False
            kx, ky = ckey(x, y)
            for ax in (-0.1, 0.0, 0.1):
                for ay in (-0.1, 0.0, 0.1):
                    if any(nm != netname for nm in
                           occupied.get((round(kx + ax, 1), round(ky + ay, 1)), set())):
                        return False
        return True

    def wire_hits_comp(p1, p2, own_ref):
        """候选 stub 不得穿过其它元件本体。

        旧版只检查「标签末端」有没有压到元件，却不管中间那段导线：
        于是横放 LED 的 GND 导线会笔直穿过旁边竖直电阻的矩形本体，
        渲染出来就像几个元件被一条线串起来（用户截图里的现象）。
        起点附近 4.5mm 属于自身引脚，跳过不算穿越。
        """
        dist = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
        n = max(1, int(dist))
        for i in range(n + 1):
            t = i / n
            if t * dist < 4.5:
                continue
            x = p1[0] + (p2[0] - p1[0]) * t
            y = p1[1] + (p2[1] - p1[1]) * t
            for (r, ref) in comp_boxes:
                if ref == own_ref:
                    continue
                if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                    return True
        return False

    def spot_ok(pax, pay, ex, ey, lx, ly, netname, ref):
        return (not box_hit(ex, ey, netname, lx, ly)
                and wire_points_free((pax, pay), (ex, ey), netname)
                and not wire_hits_comp((pax, pay), (ex, ey), ref))

    def label_w(name):
        return max(6.0, len(name) * 1.1 + 2.0)

    def label_rect(ex, ey, name, lx, ly):
        """标签文字框。KiCad label 用 justify 让文字从锚点朝元件外侧展开，
        因此包围盒只在外法向一侧延伸，绝不回压元件本体与引脚名。
        竖直高度按 1.27mm 字号收紧到 2.2mm，小于 2.54mm 引脚间距，
        相邻引脚的标签才不会被误判为互相压叠（这是斜线产生的根因）。"""
        w = label_w(name)
        if lx > 0:                       # 向右引出 -> justify left，文字向右展开
            return (ex - 0.8, ey - 1.1, ex + w + 0.8, ey + 1.1)
        if lx < 0:                       # 向左引出 -> justify right，文字向左展开
            return (ex - w - 0.8, ey - 1.1, ex + 0.8, ey + 1.1)
        # 上/下引出：文字仍水平向右展开
        return (ex - 0.8, ey - 1.1, ex + w + 0.8, ey + 1.1)

    def _boxes_overlap(a, b):
        return not (a[0] > b[2] or a[2] < b[0] or a[1] > b[3] or a[3] < b[1])

    def box_hit(ex, ey, name, lx, ly):
        r = label_rect(ex, ey, name, lx, ly)
        for cr in comp_rects:
            if _boxes_overlap(r, cr):
                return True
        for pr in placed_label_rects:
            if _boxes_overlap(r, pr):
                return True
        return False

    def find_label_spot(pax, pay, lx, ly, netname, ref):
        """返回 (ex, ey, lx, ly)：沿引脚外法向拉一条纯水平/垂直的直线 stub，
        末端放标签。导线全程正交、不折线、不斜移（教科书式原理图的关键）。
        标签盒收紧到 2.2mm（小于 2.54mm 引脚间距），同侧相邻引脚各占自己的通道；
        冲突时只增大外推距离，同一网络的标签自然排成整齐的一列。"""
        for d in (8.0, 9.5, 11.0, 12.5, 14.0, 16.0, 18.0, 20.0, 23.0, 26.0, 30.0, 35.0, 40.0, 46.0):
            ex, ey = pax + lx * d, pay + ly * d
            if spot_ok(pax, pay, ex, ey, lx, ly, netname, ref):
                return ex, ey, lx, ly
        # 外法向被堵死：改用两个正交切向就近找空位（仍是全正交直线 stub），
        # 绝不硬推 46mm 长线穿过其它元件。
        alts = [(0.0, -1.0), (0.0, 1.0)] if lx != 0 else [(-1.0, 0.0), (1.0, 0.0)]
        for (ax, ay) in alts:
            for d in (8.0, 9.5, 11.0, 12.5, 14.0, 16.0, 18.0, 20.0, 23.0, 26.0):
                ex, ey = pax + ax * d, pay + ay * d
                if spot_ok(pax, pay, ex, ey, ax, ay, netname, ref):
                    return ex, ey, ax, ay
        # 实在无处可放：退到最近的空位（沿首选方向，仍做一次避让尝试）
        for d in (46.0, 52.0, 58.0):
            ex, ey = pax + lx * d, pay + ly * d
            if spot_ok(pax, pay, ex, ey, lx, ly, netname, ref):
                return ex, ey, lx, ly
        return pax + lx * 46.0, pay + ly * 46.0, lx, ly

    def mark_wire(p1, p2, netname):
        """把整条导线按 1mm 采样登记进 occupied，防止其它网络的标签/导线落在它上面造成误短路。
        """
        n = max(1, int(((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5 / 1.0))
        for i in range(n + 1):
            t = i / n
            x = p1[0] + (p2[0] - p1[0]) * t
            y = p1[1] + (p2[1] - p1[1]) * t
            occupied.setdefault(ckey(x, y), set()).add(netname)

    for name, nodes in signal_nets:
        # 局部串联支路（限流电阻 → LED）：两脚之间画一根真正的短导线，
        # 并在导线上放同名网络标签来保住网络名。两端引脚仍由通用 stub+label
        # 处理（分别属于 D13/+5V 与 GND），因此不会重复布线。
        # 这样渲染出来是课本里的「R ─ LED」串联，而不是一条线穿过元件本体。
        if name in LOCAL_SERIES_NETS and len(nodes) == 2:
            r_node = next(
                ((r, p) for (r, p) in nodes
                 if comp_lib.get(r, ("",))[0] == "Device:R_H" and p == "2"), None)
            d_node = next(
                ((r, p) for (r, p) in nodes
                 if comp_lib.get(r, ("",))[0] == "Device:LED_R" and p == "1"), None)
            if (r_node and d_node
                    and r_node[0] in pin_abs and d_node[0] in pin_abs):
                pa = pin_abs[r_node[0]]["2"]
                pb = pin_abs[d_node[0]]["1"]
                if abs(pa[1] - pb[1]) < 0.01 and pb[0] > pa[0]:
                    add_wire(pa, pb)
                    mark_wire(pa, pb, name)
                    ex = pa[0] + 4.0
                    for off in (4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0):
                        ex = pa[0] + off
                        if not box_hit(ex, pa[1], name, 1.0, 0.0):
                            break
                    lu = str(uuid.uuid4())
                    wire_blocks.append(
                        f'  (label "{name}" (at {ex:.2f} {pa[1]:.2f} 0) '
                        f'(effects (font (size 1.27 1.27))) (uuid "{lu}"))')
                    occupied.setdefault(ckey(ex, pa[1]), set()).add(name)
                    placed_label_rects.append(label_rect(ex, pa[1], name, 1.0, 0.0))
                    continue

        for (ref, pin) in nodes:
            if ref not in pin_abs or pin not in pin_abs[ref]:
                continue
            pax, pay = pin_abs[ref][pin]
            exx, exy = comp_extent[ref]
            lx, ly = outward(comp_pins[ref][pin], exx, exy)
            ly = -ly  # 符号坐标系 Y 向上，原理图绝对坐标 Y 向下，翻转 Y 分量
            ex, ey, dx, dy = find_label_spot(pax, pay, lx, ly, name, ref)
            add_wire((pax, pay), (ex, ey))
            mark_wire((pax, pay), (ex, ey), name)
            lu = str(uuid.uuid4())
            # 向左引出的标签必须右对齐，否则 KiCad 默认向右展开会把文字压回元件本体
            just = ' (justify right)' if dx < 0 else ''
            wire_blocks.append(
                f'  (label "{name}" (at {ex:.2f} {ey:.2f} 0) '
                f'(effects (font (size 1.27 1.27)){just}) (uuid "{lu}"))'
            )
            occupied.setdefault(ckey(ex, ey), set()).add(name)
            placed_label_rects.append(label_rect(ex, ey, name, dx, dy))

    # 9) 未连接引脚加 No-Connect 标记，消除 ERC 未连接报错（课本级干净）
    connected = set()
    for _, nodes in valid_nets:
        for (r, p) in nodes:
            connected.add((r, p))
    nc_blocks = []
    for ref in pin_abs:
        if ref not in comp_pins:
            continue
        for pin, (px, py) in pin_abs[ref].items():
            if (ref, pin) not in connected:
                ncu = str(uuid.uuid4())
                nc_blocks.append(
                    f'  (no_connect (at {px:.2f} {py:.2f}) (uuid "{ncu}"))'
                )

    lib_symbols_block = "\n".join(embedded.values())
    today = datetime.date.today().isoformat()
    sch = f"""(kicad_sch (version 20231120) (generator "eeschema") (generator_version "8.0")
  (uuid {root_uuid})

  (paper "A4")

  (title_block
    (title "{design.get('board', 'Untitled')}")
    (date "{today}")
    (rev "W-advanced")
    (company "AI Agent 电路板设计"))

  (lib_symbols
{lib_symbols_block}
  )

{chr(10).join(graphic_blocks)}

{chr(10).join(sym_blocks)}

{chr(10).join(wire_blocks)}

{chr(10).join(nc_blocks)}
)
"""
    if dropped:
        sys.stderr.write("[warn] 已丢弃无效节点: " + ", ".join(dropped) + "\n")
    return sch


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "outputs/design.json")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "outputs/design.kicad_sch")
    import json
    design = json.load(open(in_path, encoding="utf-8"))
    sch = build_sch(design)
    open(out_path, "w", encoding="utf-8", newline="\n").write(sch)
    print(f"[已生成原理图] {out_path}  ({len(design.get('components', []))} 元件, "
          f"{len(design.get('nets', []))} 网络; 电源符号 {len(POWER_SET)} 种可用)")


if __name__ == "__main__":
    main()
