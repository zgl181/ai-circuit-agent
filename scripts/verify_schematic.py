"""原理图自检工具：一次性验证生成结果的「电气正确性」与「排版质量」。

用法：
    python scripts/verify_schematic.py

依赖已生成的产物（由 kicad_sch.py 与 kicad-cli 产生）：
    outputs/design.json       设计意图（BOM + 网表）
    outputs/design.kicad_sch  生成的原理图
    outputs/design.net        kicad-cli 反导出的网表（ground truth）
    outputs/erc.txt            ERC 报告（kicad-cli sch erc --output outputs/erc.txt）

检查项：
    A. 网表一致性：design.net 反导出的每个网络与引脚，必须与 design.json 完全一致；
       且同一引脚不得出现在多个网络（短路检测）。
    B. 电气规则：ERC 报告中不得有 error（warning 仅提示，不判失败）。
    C. 排版几何：元件互不重叠、标签不压元件、标签不互相叠字、全部落在 A4 图框内。

退出码 0 = 全部通过；1 = 存在失败项。可用于 CI / 答辩演示。
"""
import json
import os
import re
import sys

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "outputs"))

# A4 横向图框：SVG 中边框线约在 11mm 处，右下角为标题栏
SAFE = (11.0, 286.0, 11.0, 199.0)  # x0, x1, y0, y1

# 各符号半宽/半高(mm)，用于估算元件本体包围盒
HALF = {
    "ATmega328P-M": (15, 20), "ATmega328P-P": (16, 32),
    "CH340G": (6, 10), "Crystal": (4, 4), "C": (4, 3),
    "C_Polarized": (4, 4), "Fuse": (4, 3), "R": (3, 3), "LED": (3, 3),
    "SW_Push": (4, 4), "Conn_01x05": (3, 6), "Conn_01x06": (3, 8),
    "Conn_01x08": (3, 11), "Conn_01x14": (3, 18),
}


def read(path):
    with open(os.path.join(OUT, path), encoding="utf-8", errors="ignore") as f:
        return f.read()


def boxes_overlap(a, b):
    return not (a[0] > b[2] or a[2] < b[0] or a[1] > b[3] or a[3] < b[1])


# ---------------------------------------------------------------- A. 网表一致性
def check_netlist():
    design = json.loads(read("design.json"))
    json_nets = {
        n["name"]: {(p["ref"], str(p["pin"])) for p in n["nodes"]}
        for n in design["nets"]
    }
    net_text = read("design.net")
    nets = {}
    for m in re.finditer(
        r'\(net \(code "[^"]*"\) \(name "([^"]*)"\)(.*?)\)\s*(?=\(net|\Z)',
        net_text, re.S,
    ):
        name = m.group(1).lstrip("/")
        nodes = {
            (nm.group(1), nm.group(2))
            for nm in re.finditer(r'\(node \(ref "([^"]*)"\) \(pin "([^"]*)"\)', m.group(2))
        }
        nets[name] = nodes
    # unconnected-* 是 KiCad 为未连接引脚生成的伪网络，属正常现象
    nc_count = sum(1 for k in nets if k.startswith("unconnected-"))
    nets = {k: v for k, v in nets.items() if not k.startswith("unconnected-")}

    missing = set(json_nets) - set(nets)
    extra = set(nets) - set(json_nets)
    mismatch = []
    for name in set(json_nets) & set(nets):
        if json_nets[name] != nets[name]:
            mismatch.append((name, sorted(json_nets[name] - nets[name]),
                             sorted(nets[name] - json_nets[name])))
    pin_to_nets = {}
    for name, nodes in nets.items():
        for ref_pin in nodes:
            pin_to_nets.setdefault(ref_pin, set()).add(name)
    shorts = {k: v for k, v in pin_to_nets.items() if len(v) > 1}

    print(f"[A] 网表一致性：design.json {len(json_nets)} 个网络 / "
          f"design.net {len(nets)} 个（另有 {nc_count} 个 NC 伪网络，符合预期）")
    print(f"    缺失网络       : {sorted(missing) if missing else 'NONE'}")
    print(f"    多余网络       : {sorted(extra) if extra else 'NONE'}")
    print(f"    节点不一致     : {len(mismatch)}")
    for p in mismatch[:10]:
        print("       ", p)
    print(f"    跨网络重复引脚 : {len(shorts)}  (短路!)")
    for k, v in list(shorts.items())[:10]:
        print("       ", k, "in", sorted(v))
    ok = not (missing or extra or mismatch or shorts)
    print(f"    => {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- B. ERC
def check_erc():
    path = os.path.join(OUT, "erc.txt")
    if not os.path.exists(path):
        path = os.path.join(OUT, "erc.json")
    if not os.path.exists(path):
        print("[B] ERC：报告不存在 => FAIL")
        return False
    text = open(path, encoding="utf-8", errors="ignore").read()
    errors = len(re.findall(r";\s*error", text))
    warnings = len(re.findall(r";\s*warning", text))
    kinds = {}
    for k in re.findall(r"^\[([a-z_]+)\]", text, re.M):
        kinds[k] = kinds.get(k, 0) + 1
    print(f"[B] ERC：{errors} errors / {warnings} warnings  {kinds}")
    print(f"    => {'PASS' if errors == 0 else 'FAIL'}")
    return errors == 0


# ---------------------------------------------------------------- C. 排版几何
def check_geometry():
    sch = read("design.kicad_sch")
    comps = []
    for m in re.finditer(
        r'\(symbol\n\s*\(lib_id "([^"]+)"\)\n\s*\(at ([\d.\-]+) ([\d.\-]+) (\d+)\)', sch
    ):
        lib_id, x, y = m.group(1), float(m.group(2)), float(m.group(3))
        hx, hy = HALF.get(lib_id.split(":")[-1], (6, 6))
        comps.append((lib_id, x - hx, y - hy, x + hx, y + hy))

    # 标签包围盒与生成器一致：justify right 时文字向左展开，否则向右展开
    labels = []
    pat = (r'\(label "([^"]*)" \(at ([\d.\-]+) ([\d.\-]+) (\d+)\)\s*'
           r'\(effects\s*\(font \(size [\d.]+ [\d.]+\)\)\s*(\(justify ([a-z]+)\))?')
    for m in re.finditer(pat, sch):
        name, x, y = m.group(1), float(m.group(2)), float(m.group(3))
        just = m.group(6) or "left"
        w = 2 + len(name) * 1.1
        if just == "right":
            box = (x - w - 0.8, y - 1.1, x + 0.8, y + 1.1)
        else:
            box = (x - 0.8, y - 1.1, x + w + 0.8, y + 1.1)
        labels.append((name, box))

    comp_over = [
        (comps[i][0], comps[j][0])
        for i in range(len(comps))
        for j in range(i + 1, len(comps))
        if boxes_overlap(comps[i][1:5], comps[j][1:5])
    ]
    lab_on_comp = [
        (name, cb[0]) for name, lb in labels for cb in comps
        if boxes_overlap(lb, cb[1:5])
    ]
    off = []
    for cb in comps:
        if cb[1] < SAFE[0] or cb[3] > SAFE[1] or cb[2] < SAFE[2] or cb[4] > SAFE[3]:
            off.append(cb[0])
    for name, lb in labels:
        if lb[0] < SAFE[0] or lb[2] > SAFE[1] or lb[1] < SAFE[2] or lb[3] > SAFE[3]:
            off.append("LABEL:" + name)
    lab_coll = [
        (labels[i][0], labels[j][0])
        for i in range(len(labels))
        for j in range(i + 1, len(labels))
        if boxes_overlap(labels[i][1], labels[j][1])
    ]

    print(f"[C] 排版几何：{len(comps)} 元件 / {len(labels)} 标签")
    print(f"    元件互相重叠   : {len(comp_over)}")
    print(f"    标签压住元件   : {len(lab_on_comp)}")
    print(f"    超出图框       : {len(off)}")
    print(f"    标签互相叠字   : {len(lab_coll)}")
    for label, items in (("元件重叠", comp_over), ("压元件", lab_on_comp),
                         ("越界", off), ("叠字", lab_coll)):
        for it in items[:8]:
            print(f"       [{label}] {it}")
    ok = not (comp_over or lab_on_comp or off or lab_coll)
    print(f"    => {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    print("=" * 60)
    print("原理图自检：电气正确性 + 排版质量")
    print("=" * 60)
    results = [check_netlist(), check_erc(), check_geometry()]
    print("-" * 60)
    print("TOTAL:", "ALL PASS ✓" if all(results) else "FAILED ✗")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
