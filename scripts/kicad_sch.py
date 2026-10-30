"""把 design.json 转成 KiCad 8 原理图文件 design.kicad_sch（可双击用 KiCad 打开）。

关键结论（已用 kicad-cli 实测验证）：
- 采用「内嵌符号」方案：把用到的真实符号体写进 (lib_symbols ...)，
  文件名带库前缀（如 "Device:R"），生成的 .kicad_sch 自包含、无需 sym-lib-table。
- 放置符号块必须用 KiCad 8 真实格式：
    (symbol
      (lib_id "Device:R")
      (at X Y 0)               # 注意：必须 3 个参数 (x y 旋转)
      (unit 1)
      (uuid "...")
      (property "Reference" "R1" (id 0) (at ... 0) (effects ...))
      (pin "1" (uuid "..."))   # 每个引脚一行
      (instances
        (project "PROJECT"
          (path "/<根原理图uuid>" (reference "R1") (unit 1)))))  # path 用根uuid，不是位号

用法：
    python scripts/kicad_sch.py                 # 读 outputs/design.json -> outputs/design.kicad_sch
    python scripts/kicad_sch.py 输入.json 输出.kicad_sch
"""
import sys
import os
import re
import uuid

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
    "开关": ("Switch.kicad_sym", "SW_Push"), "按钮": ("Switch.kicad_sym", "SW_Push"),
    "晶振": ("Device.kicad_sym", "C"), "crystal": ("Device.kicad_sym", "C"),
    "mcu": ("MCU_Microchip_ATmega.kicad_sym", "ATmega328P-M"),
    "单片机": ("MCU_Microchip_ATmega.kicad_sym", "ATmega328P-M"),
    "连接器": ("Connector_Generic.kicad_sym", "Conn_01x02"),
    "电池": ("Device.kicad_sym", "Battery"),
}
DEFAULT_LIB, DEFAULT_SYM = "Device.kicad_sym", "R"

_cache = {}


def extract_symbol(libfile, symname):
    """从库文件抽取 (symbol "symname" ...) 完整定义，返回 (body, [(number, x, y, rot), ...])。"""
    path = os.path.join(SYM_DIR, libfile)
    if not os.path.exists(path):
        return None, []
    key = (libfile, symname)
    if key in _cache:
        return _cache[key]
    txt = open(path, encoding="utf-8", errors="ignore").read()
    i = txt.find(f'(symbol "{symname}"')
    if i == -1:
        _cache[key] = (None, [])
        return None, []
    depth, j = 0, i
    while j < len(txt):
        if txt[j] == "(":
            depth += 1
        elif txt[j] == ")":
            depth -= 1
        if depth == 0 and j > i:
            break
        j += 1
    body = txt[i:j + 1]
    # 抽取引脚：只匹配真正的引脚定义 (pin <电气类型> ...)，排除 (pin_numbers)/(pin_names)
    pins = []
    pin_re = re.compile(
        r'\(pin\s+(?:passive|input|output|bidirectional|tri_state|unspecified|'
        r'power_in|power_out|open_collector|open_emitter|free|no_connect)\b')
    for m in pin_re.finditer(body):
        i = m.start()          # "(" 的位置
        d, k = 0, i
        while k < len(body):
            if body[k] == "(":
                d += 1
            elif body[k] == ")":
                d -= 1
                if d == 0:
                    break
            k += 1
        blk = body[i:k + 1]
        am = re.search(r'\(at\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\)', blk)
        nm = re.search(r'\(number\s+"?([^")\s]+)"?', blk)
        if am and nm:
            pins.append((nm.group(1), float(am.group(1)), float(am.group(2)),
                         int(float(am.group(3)))))
    result = (body, pins)
    _cache[key] = result
    return result


def resolve_lib(comp):
    t = (comp.get("type") or "").lower()
    for k, (lf, sn) in SYM_MAP.items():
        if k.lower() in t:
            return lf, sn
    return DEFAULT_LIB, DEFAULT_SYM


def build_sch(design):
    comps = design.get("components", [])
    nets = design.get("nets", [])
    root_uuid = str(uuid.uuid4())

    # 1) 收集要内嵌的符号 -> (lib_id "Lib:Sym", body)
    embedded = {}
    comp_lib = {}
    for c in comps:
        lf, sn = resolve_lib(c)
        lib_id = f"{lf[:-10]}:{sn}"  # Device.kicad_sym -> Device
        if lib_id not in embedded:
            body, pins = extract_symbol(lf, sn)
            if body is None:  # 符号找不到，退化成 R
                lf, sn = DEFAULT_LIB, DEFAULT_SYM
                lib_id = f"{lf[:-10]}:{sn}"
                body, pins = extract_symbol(lf, sn)
            if body is None:
                continue
            # 顶层符号改名带库前缀
            body = body.replace(f'(symbol "{sn}"', f'(symbol "{lib_id}"', 1)
            embedded[lib_id] = body
        comp_lib[c["ref"]] = (lib_id, embedded[lib_id])

    # 2) 布局：网格排列（KiCad 坐标单位是毫米，A4 页面 297x210mm，必须落在纸内）
    cols, dx, dy, x0, y0 = 3, 85.0, 70.0, 40.0, 45.0
    pos = {}
    sym_blocks = []
    pin_abs = {}  # ref -> {pin_number: (abs_x, abs_y)}
    for i, c in enumerate(comps):
        x = x0 + (i % cols) * dx
        y = y0 + (i // cols) * dy
        pos[c["ref"]] = (x, y)
        lib_id, body = comp_lib[c["ref"]]
        _, pins = extract_symbol(*resolve_lib(c))
        ref, val, fp = c["ref"], c.get("value", ""), c.get("package", "")
        uid = str(uuid.uuid4())
        pabs = {}
        pin_lines = []
        for (num, px, py, rot) in pins:
            pu = str(uuid.uuid4())
            pin_lines.append(f'    (pin "{num}"\n        (uuid "{pu}")\n    )')
            # 库符号坐标 Y 向上、原理图 Y 向下：放置后需翻转 Y
            pabs[num] = (x + px, y - py)
        pin_abs[c["ref"]] = pabs
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
            f'    (property "Reference" "{ref}" (id 0) (at {x+5:.1f} {y-15:.1f} 0) (effects (font (size 1.27 1.27))))\n'
            f'    (property "Value" "{val}" (id 1) (at {x+5:.1f} {y+15:.1f} 0) (effects (font (size 1.27 1.27))))\n'
            f'    (property "Footprint" "{fp}" (id 2) (at {x:.1f} {y:.1f} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
            + "\n".join(pin_lines) + "\n"
            f'    (instances\n'
            f'      (project "{design.get("board", "PCB1")}"\n'
            f'        (path "/{root_uuid}"\n'
            f'          (reference "{ref}")\n'
            f'          (unit 1)\n'
            f'        )\n'
            f'      )\n'
            f'    )\n'
            f'  )'
        )

    # 3) 连线：每个网络的成员引脚用「正交折线」链式连起来，并在自家导线上贴网络名
    wire_blocks = []

    def add_wire(p, q, segs):
        if abs(p[0] - q[0]) < 0.01 and abs(p[1] - q[1]) < 0.01:
            return
        segs.append((p, q))
        wu = str(uuid.uuid4())
        wire_blocks.append(
            f'  (wire (pts (xy {p[0]:.2f} {p[1]:.2f}) (xy {q[0]:.2f} {q[1]:.2f})) '
            f'(stroke (width 0) (type default) (color 0 0 0 0)) (uuid "{wu}"))'
        )

    for net in nets:
        pts = []
        for n in net.get("nodes", []):
            pa = pin_abs.get(n["ref"], {}).get(str(n["pin"]))
            if pa:
                pts.append(pa)
        segs = []
        for a, b in zip(pts, pts[1:]):
            if abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01:
                add_wire(a, b, segs)
            else:
                mid = (b[0], a[1])   # 先横后竖
                add_wire(a, mid, segs)
                add_wire(mid, b, segs)
        # 网络名贴在「本网络第一条导线」的中点（保证属于同一网络，不会误连）
        if segs:
            p, q = segs[0]
            lx, ly = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            lu = str(uuid.uuid4())
            wire_blocks.append(
                f'  (label "{net.get("name", "")}" (at {lx:.2f} {ly:.2f} 0) '
                f'(effects (font (size 1.27 1.27))) (uuid "{lu}"))'
            )

    lib_symbols_block = "\n".join(embedded.values())
    today = __import__("datetime").date.today().isoformat()
    sch = f"""(kicad_sch (version 20231120) (generator "eeschema") (generator_version "8.0")
  (uuid {root_uuid})

  (paper "A4")

  (title_block
    (title "{design.get('board', 'Untitled')}")
    (date "{today}")
    (rev "W3-demo")
    (company "AI Agent 电路板设计"))

  (lib_symbols
{lib_symbols_block}
  )

{chr(10).join(sym_blocks)}

{chr(10).join(wire_blocks)}
)
"""
    return sch


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "outputs/design.json")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "outputs/design.kicad_sch")
    import json
    design = json.load(open(in_path, encoding="utf-8"))
    sch = build_sch(design)
    open(out_path, "w", encoding="utf-8", newline="\n").write(sch)
    print(f"[已生成原理图] {out_path}  ({len(design.get('components', []))} 元件, "
          f"{len(design.get('nets', []))} 网络)")


if __name__ == "__main__":
    main()
