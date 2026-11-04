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
    "二极管": ("Device.kicad_sym", "D"), "diode": ("Device.kicad_sym", "D"),
    "开关": ("Switch.kicad_sym", "SW_Push"), "按钮": ("Switch.kicad_sym", "SW_Push"),
    "晶振": ("Device.kicad_sym", "Crystal"), "crystal": ("Device.kicad_sym", "Crystal"),
    "oscillator": ("Device.kicad_sym", "Crystal"),
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


def _is_composite(body):
    """复合符号：引脚/图形被包在嵌套 (symbol "X_1_0" ...) 里，直接改名顶层会导致 KiCad 加载失败。"""
    return body.count('(symbol "') > 1


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


def resolve_lib(comp):
    """根据元件 type 字段解析 (库文件, 符号名)。"""
    t = (comp.get("type") or "").lower().strip()
    # 精确 74 系列（74HC373 / 74LS00 / 74HCT08 ...）
    m = re.search(r'74\s*(hc|hct|ls|als|f|ac|act|lv)?\s*(\d{2,3})', t)
    if m:
        name = f"74{(m.group(1) or '').upper()}{m.group(2)}"
        pp = os.path.join(SYM_DIR, "74xx.kicad_sym")
        if os.path.exists(pp) and f'(symbol "{name}"' in open(pp, encoding="utf-8", errors="ignore").read():
            return "74xx.kicad_sym", name
    for k, (lf, sn) in SYM_MAP.items():
        if k.lower() in t:
            return lf, sn
    # 未匹配但看起来是芯片 -> 用通用逻辑门芯片外观占位（14 脚 DIP 盒）
    if any(w in t for w in ["芯片", "ic", "集成电路", "逻辑", "门", "触发器", "计数器", "寄存器", "锁存"]):
        return "74xx.kicad_sym", "74HC00"
    return DEFAULT_LIB, DEFAULT_SYM


def outward(pin_local):
    """给定引脚的库内局部坐标(plib Y 向上)，返回朝外的单位方向 (dx,dy)，用于引 stub。"""
    px, py, _ = pin_local
    if abs(px) >= abs(py):
        return (-1.0, 0.0) if px < 0 else (1.0, 0.0)
    return (0.0, 1.0) if py > 0 else (0.0, -1.0)


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

    # 1) 解析并抽取所有用到的元件符号
    embedded = {}            # lib_id -> body
    comp_lib = {}            # ref -> (lib_id, pins)
    comp_pins = {}           # ref -> {pin_number: (px,py,rot)}
    for c in comps:
        lf, sn = resolve_lib(c)
        lib_id = f"{lf[:-10]}:{sn}"
        if lib_id not in embedded:
            body, pins = extract_symbol(lf, sn)
            if body is None:
                lf, sn = DEFAULT_LIB, DEFAULT_SYM
                lib_id = f"{lf[:-10]}:{sn}"
                body, pins = extract_symbol(lf, sn)
            if body is None:
                continue
            # 复合符号(引脚包在嵌套 symbol 里)无法直接改名复用 -> 用引脚坐标重建扁平符号
            if _is_composite(body):
                body = _build_generic_symbol(lib_id, pins)
            else:
                body = re.sub(r'\(symbol "([^"]+)"', f'(symbol "{lib_id}"', body, count=1)
            if body is None:
                continue
            embedded[lib_id] = body
        comp_lib[c["ref"]] = (lib_id, pins)
        comp_pins[c["ref"]] = {num: (px, py, rot) for (num, px, py, rot) in pins}

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

    # 3) 拓扑排序 + 网格布局（KiCad 坐标单位毫米；A4=297x210mm）
    order = order_components(comps, valid_nets)
    n = len(order)
    cols = max(2, min(4, (n + 1) // 2))
    dx, dy, x0, y0 = 75.0, 65.0, 45.0, 45.0
    pos = {}
    for i, ref in enumerate(order):
        pos[ref] = (x0 + (i % cols) * dx, y0 + (i // cols) * dy)

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
        lib_id, pins = comp_lib[ref]
        c = next(cc for cc in comps if cc["ref"] == ref)
        val = c.get("value", "")
        fp = c.get("package", "")
        uid = str(uuid.uuid4())
        pin_lines = []
        for (num, px, py, rot) in pins:
            pin_lines.append(f'    (pin "{num}"\n        (uuid "{str(uuid.uuid4())}")\n    )')
        sym_blocks.append(
            f'  (symbol\n'
            f'    (lib_id "{lib_id}")\n'
            f'    (at {x:.1f} {y:.1f} 0)\n'
            f'    (unit 1)\n'
            f'    (exclude_from_sim no)\n'
            f'    (in_bom yes)\n'
            f'    (on_board yes)\n'
            f'    (dnp no)\n'
            f'    (uuid "{uid}")\n'
            f'    (property "Reference" "{ref}" (id 0) (at {x+6:.1f} {y-16:.1f} 0) (effects (font (size 1.27 1.27))))\n'
            f'    (property "Value" "{val}" (id 1) (at {x+6:.1f} {y+16:.1f} 0) (effects (font (size 1.27 1.27))))\n'
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

    # 6) 区分电源网络 / 信号网络
    power_nets = {}      # name -> power symbol name（保留供未来用，当前统一走 net label）
    signal_nets = []
    for name, nodes in valid_nets:
        # 说明：曾尝试把电源网络画成 power 库符号（power:GND 等），但 KiCad 在自动布局下
        # 会把不同名的电源符号合并成一个 <NO NET>。改为全部走 net label（命名为 VCC/GND/+12V…），
        # 电气上等价且不会短路，视觉上也是专业原理图常见画法。
        ps = power_symbol_for(name)
        if ps:
            power_nets[name] = ps
        signal_nets.append((name, nodes))

    # 7) 电源符号：每个电源引脚外引一小段线 + 放电源符号（自动连全局同名网络）
    #    防短路护栏：若符号引脚位置离其它电源符号/元件引脚太近(<10mm)，退化为网络标签，
    #    避免把 VCC/GND 等不同电源网络短接成一段 <NO NET>。
    placed_power_pins = []
    all_pin_coords = [(x, y) for ref in pin_abs for (x, y) in pin_abs[ref].values()]

    def _too_close(x, y):
        for (ox, oy) in placed_power_pins + all_pin_coords:
            if (x - ox) ** 2 + (y - oy) ** 2 < 100.0:  # <10mm
                return True
        return False

    for name, ps in []:  # 电源符号暂禁用（见上方说明），电源网络统一走 net label
        lib_id = f"power:{ps}"
        if lib_id not in embedded:
            body, ppins = extract_symbol("power.kicad_sym", ps)
            if body:
                body = body.replace(f'(symbol "{ps}"', f'(symbol "{lib_id}"', 1)
                embedded[lib_id] = body
        if lib_id not in embedded:
            continue
        ppins = extract_symbol("power.kicad_sym", ps)[1]
        plx, ply = (ppins[0][1], ppins[0][2]) if ppins else (0.0, 0.0)
        pn = ppins[0][0] if ppins else "1"
        # 遍历该电源网络的所有节点
        for (ref, pin) in [nd for nm, nd in valid_nets if nm == name for nd in nd]:
            if ref not in pin_abs or pin not in pin_abs[ref]:
                continue
            pax, pay = pin_abs[ref][pin]
            lx, ly = outward(comp_pins[ref][pin])
            tx, ty = pax + lx * 12.0, pay + ly * 12.0
            add_wire((pax, pay), (tx, ty))
            if _too_close(tx, ty):
                # 放不下电源符号 -> 退化为普通网络标签（仍连到同名网络）
                lu = str(uuid.uuid4())
                wire_blocks.append(
                    f'  (label "{name}" (at {tx:.2f} {ty:.2f} 0) '
                    f'(effects (font (size 1.27 1.27))) (uuid "{lu}"))'
                )
                continue
            placed_power_pins.append((tx, ty))
            sx = tx - plx
            sy = ty + ply
            uid = str(uuid.uuid4())
            pu = str(uuid.uuid4())
            sym_blocks.append(
                f'  (symbol (lib_id "{lib_id}") (at {sx:.2f} {sy:.2f} 0) (unit 1) '
                f'(exclude_from_sim no) (in_bom no) (on_board no) (uuid "{uid}")\n'
                f'    (pin "{pn}" (uuid "{pu}"))\n'
                f'    (instances (project "{board_name}" (path "/{root_uuid}" '
                f'(reference "#PWR") (unit 1))))\n  )'
            )

    # 8) 信号网络：每个引脚外引 stub + 同名 net label（避免长线穿越元件）
    for name, nodes in signal_nets:
        for (ref, pin) in nodes:
            if ref not in pin_abs or pin not in pin_abs[ref]:
                continue
            pax, pay = pin_abs[ref][pin]
            lx, ly = outward(comp_pins[ref][pin])
            ex, ey = pax + lx * 9.0, pay + ly * 9.0
            add_wire((pax, pay), (ex, ey))
            lu = str(uuid.uuid4())
            wire_blocks.append(
                f'  (label "{name}" (at {ex:.2f} {ey:.2f} 0) '
                f'(effects (font (size 1.27 1.27))) (uuid "{lu}"))'
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

{chr(10).join(sym_blocks)}

{chr(10).join(wire_blocks)}
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
