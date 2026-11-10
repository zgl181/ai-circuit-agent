"""把 design.json 变成 KiCad 8 的 PCB 板文件 design.kicad_pcb。

与 kicad_sch.py 的「自包含文本生成」不同，PCB 走 pcbnew Python API：
直接从 KiCad 官方 .pretty 封装库加载真实封装（含焊盘、丝印、Courtyard），
绑定引脚级网络，按功能分区自动布局（shelf packing），画 Edge.Cuts 板框。

必须用 KiCad 自带的 Python 运行（项目的 .venv 里没有 pcbnew）：

    C:/Users/24013/Desktop/工具/bin/python.exe scripts/kicad_pcb.py

可选参数：
    --design outputs/design.json        设计输入
    --out outputs/design.kicad_pcb      板文件输出
    --layout outputs/board_layout.json  布局覆盖文件 {ref: [x_mm, y_mm]}（供 DRC 闭环微调）
    --save-layout outputs/board_layout.json  导出本次实际采用的布局

环境变量 KICAD_FP_DIR 可覆盖封装库根目录。
"""
import argparse
import json
import os
import subprocess
import sys

try:
    import pcbnew
except ImportError:
    sys.exit(
        "本脚本需要 pcbnew，请用 KiCad 自带的 Python 运行，例如：\n"
        r'  C:/Users/24013/Desktop/工具/bin/python.exe scripts/kicad_pcb.py'
    )

FP_ROOT = os.environ.get(
    "KICAD_FP_DIR", r"C:/Users/24013/Desktop/工具/share/kicad/footprints"
)

# 板框 140 x 62 mm：元件实际占 y=8..56，收紧高度避免下半幅整片空白；
# 四周留 >=6mm 边距，避免 edge_clearance 违规
BOARD_W, BOARD_H = 140.0, 62.0
ZONE_GAP = 3.0

# ref -> (封装库昵称, 封装名)。全部取自 KiCad 8 官方库。
FOOTPRINTS = {
    "U1":    ("Package_DIP", "DIP-28_W7.62mm"),
    "U2":    ("Package_SO",  "SOIC-16_3.9x9.9mm_P1.27mm"),
    "Y1":    ("Crystal",     "Crystal_HC49-U_Vertical"),
    "Y2":    ("Crystal",     "Crystal_HC49-U_Vertical"),
    "C1":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C2":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C3":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C4":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C5":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C6":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C7":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C8":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C9":    ("Capacitor_SMD", "C_0603_1608Metric"),
    "C10":   ("Capacitor_THT", "CP_Radial_D5.0mm_P2.00mm"),
    "C11":   ("Capacitor_SMD", "C_0603_1608Metric"),
    "R1":    ("Resistor_SMD", "R_0603_1608Metric"),
    "R2":    ("Resistor_SMD", "R_0603_1608Metric"),
    "R3":    ("Resistor_SMD", "R_0603_1608Metric"),
    # 自恢复保险丝借 1206 封装占位（两焊盘，电气一致），报告中如实注明
    "F1":    ("Resistor_SMD", "R_1206_3216Metric"),
    "D1":    ("LED_SMD",     "LED_0603_1608Metric"),
    "D2":    ("LED_SMD",     "LED_0603_1608Metric"),
    "SW1":   ("Button_Switch_THT", "SW_PUSH_6mm"),
    # 选屏蔽焊盘不挡信号脚的版本：Molex-105017 有 8 个 pin6 外壳焊盘
    # 把 1–5 号信号脚围死，自动布线时线引不出来；此版本仅 2 个且不挡路。
    "USB1":  ("Connector_USB", "USB_Micro-B_Wuerth_614105150721_Vertical"),
    "ICSP1": ("Connector_PinHeader_2.54mm", "PinHeader_2x03_P2.54mm_Vertical"),
    "H_D":   ("Connector_PinHeader_2.54mm", "PinHeader_1x14_P2.54mm_Vertical"),
    "H_A":   ("Connector_PinHeader_2.54mm", "PinHeader_1x08_P2.54mm_Vertical"),
}

# 五个功能区，与原理图的虚线分区一一对应：(x0, y0, x1, y1, [refs])
ZONES = [
    ("POWER INPUT",      8.0,   8.0,  40.0, 40.0,
     ["USB1", "F1", "C10"]),
    ("USB-UART CH340G", 44.0,   8.0,  80.0, 40.0,
     ["U2", "Y2", "C5", "C6", "C7", "C8"]),
    ("MCU CORE",        84.0,   8.0, 110.0, 58.0,
     ["U1", "Y1", "C1", "C2"]),
    ("DECOUPLING / IO", 114.0,  8.0, 134.0, 58.0,
     ["H_D", "H_A", "ICSP1", "C3", "C4", "C11"]),
    ("STATUS / RESET",   8.0,  48.0,  80.0, 58.0,
     ["R1", "R2", "R3", "D1", "D2", "SW1", "C9"]),
]


def mm(v):
    return pcbnew.FromMM(float(v))


def pt(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def to_mm(v):
    return pcbnew.ToMM(v)


def footprint_size(fp):
    """封装外形（含焊盘、不含丝印文字），mm。"""
    bb = fp.GetBoundingBox(False, False)
    return to_mm(bb.GetWidth()), to_mm(bb.GetHeight())


def place_center(fp, cx, cy):
    """把封装的几何中心放到 (cx, cy)。

    官方封装的原点不一定在中心（排针/DIP 常在 pin1），因此放一次后
    读回真实包围盒，按中心偏差校正一次。
    """
    fp.SetPosition(pt(cx, cy))
    bb = fp.GetBoundingBox(False, False)
    c = bb.GetCenter()
    dx = cx - to_mm(c.x)
    dy = cy - to_mm(c.y)
    fp.SetPosition(pt(cx + dx, cy + dy))
    return fp.GetPosition()


def auto_layout(sizes):
    """分区内 shelf packing：按元件宽度依次摆，放不下就换行，行高取该行最大高度。

    返回 {ref: [cx_mm, cy_mm]}。坐标是元件几何中心，便于 DRC 闭环整体微调。
    """
    layout = {}
    for name, x0, y0, x1, _y1, refs in ZONES:
        x, y, row_h = x0, y0, 0.0
        for ref in refs:
            w, h = sizes[ref]
            if x + w > x1 and x > x0:
                x = x0
                y += row_h + ZONE_GAP
                row_h = 0.0
            layout[ref] = [round(x + w / 2.0, 3), round(y + h / 2.0, 3)]
            x += w + ZONE_GAP
            row_h = max(row_h, h)
    return layout


def add_outline(board):
    """用四条 Edge.Cuts 线段画闭合矩形板框（矩形图元有时不被 DRC 认作轮廓）。"""
    corners = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H)]
    seg_shape = getattr(pcbnew, "SHAPE_T_SEGMENT", getattr(pcbnew, "S_SEGMENT", 0))
    for i in range(4):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % 4]
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(seg_shape)
        s.SetStart(pt(x1, y1))
        s.SetEnd(pt(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.05))
        board.Add(s)


def add_tracks(board, net_items, routes):
    """把 DRC 闭环下发的走线画到板上。

    routes: [{"net": 网络名, "layer": "F.Cu"|"B.Cu",
              "points": [[x_mm, y_mm], ...], "vias": [[x_mm, y_mm], ...]}]
    跨层路径由闭环拆成多条同层 route，换层点以 via 连接。
    """
    layer_ids = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
    segments = 0
    seen_vias = set()
    for r in routes:
        item = net_items.get(r["net"])
        if item is None:
            continue
        layer = layer_ids.get(r.get("layer", "F.Cu"), pcbnew.F_Cu)
        pts = r.get("points", [])
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pt(x1, y1))
            t.SetEnd(pt(x2, y2))
            t.SetWidth(mm(r.get("width", 0.25)))
            t.SetLayer(layer)
            t.SetNet(item)
            board.Add(t)
            segments += 1
        for vx, vy in r.get("vias", []):
            key = (round(vx, 3), round(vy, 3), r["net"])
            if key in seen_vias:
                continue  # 同位置同网络的重复过孔会触发 holes_co_located
            seen_vias.add(key)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pt(vx, vy))
            v.SetWidth(mm(0.6))
            v.SetDrill(mm(0.3))
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNet(item)
            board.Add(v)
    return segments


def add_ground_planes(board, net_items):
    """F.Cu / B.Cu 全板铺 GND 铜皮。

    这是真实 PCB 的标准做法：铜皮填充时会自动避让其它网络的焊盘与走线，
    既不会短路，又一次性连通所有 GND 焊盘，让自动布线不必为地网络画 20 根线。
    用全连接（非热焊盘）避免 DRC 的 starved_thermal 违规。
    """
    gnd = net_items.get("GND")
    if gnd is None:
        return 0
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(gnd)
        z.SetLocalClearance(mm(0.25))
        try:
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        except Exception:
            pass
        outline = z.Outline()
        outline.NewOutline()
        for x, y in ((1, 1), (BOARD_W - 1, 1),
                     (BOARD_W - 1, BOARD_H - 1), (1, BOARD_H - 1)):
            outline.Append(mm(x), mm(y))
        board.Add(z)
    return 2


def fill_zones_in_file(path):
    """在独立进程里加载板子并填充铜皮。

    pcbnew 的 ZONE_FILLER 在「同一进程反复新建 BOARD 并填充」时会段错误，
    因此闭环最后一轮通过 --fill-only 单独起一个进程完成填充。
    """
    b = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(path, b)
    return len(list(b.Zones()))


def build(design_path, out_path, layout_path=None, save_layout_path=None,
          routes=None, planes=True):
    with open(design_path, encoding="utf-8") as f:
        data = json.load(f)

    comps = {c["ref"]: c for c in data["components"]}
    board = pcbnew.BOARD()

    # 1) 网络：先建 NETINFO_ITEM，再逐个焊盘绑定
    net_items = {}
    for net in data["nets"]:
        item = pcbnew.NETINFO_ITEM(board, net["name"])
        board.Add(item)
        net_items[net["name"]] = item

    # 2) 加载真实封装
    fps = {}
    sizes = {}
    for ref, comp in comps.items():
        if ref not in FOOTPRINTS:
            raise KeyError("元件 %s 没有封装映射（type=%s）" % (ref, comp.get("type")))
        lib, name = FOOTPRINTS[ref]
        fp = pcbnew.FootprintLoad(os.path.join(FP_ROOT, lib + ".pretty"), name)
        if fp is None:
            raise RuntimeError("封装加载失败 %s:%s" % (lib, name))
        try:
            fp.SetFPID(pcbnew.LIB_ID(lib, name))
        except Exception:
            pass
        fp.SetReference(ref)
        fp.SetValue(str(comp.get("value", "")))
        board.Add(fp)
        fps[ref] = fp
        sizes[ref] = footprint_size(fp)

    # 3) 布局：默认 shelf packing，可被闭环下发的 layout 覆盖
    layout = auto_layout(sizes)
    if layout_path and os.path.isfile(layout_path):
        with open(layout_path, encoding="utf-8") as f:
            override = json.load(f)
        for ref, xy in override.items():
            if ref in layout:
                layout[ref] = [float(xy[0]), float(xy[1])]
    for ref, (cx, cy) in layout.items():
        place_center(fps[ref], cx, cy)

    # 4) 引脚级网络绑定
    bound, missing = 0, []
    for net in data["nets"]:
        item = net_items[net["name"]]
        for node in net.get("nodes", []):
            ref, pin = node["ref"], str(node["pin"])
            fp = fps.get(ref)
            if fp is None:
                missing.append((net["name"], ref, pin, "no footprint"))
                continue
            hit = [p for p in fp.Pads() if p.GetNumber() == pin]
            if not hit:
                missing.append((net["name"], ref, pin, "no such pad"))
                continue
            for pad in hit:
                pad.SetNet(item)
                bound += 1

    # 5) 闭环下发的走线 + 板框 + GND 铺铜 + 保存
    track_segments = add_tracks(board, net_items, routes or [])
    add_outline(board)
    ground_planes = add_ground_planes(board, net_items) if planes else 0
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    pcbnew.SaveBoard(out_path, board)

    if save_layout_path:
        with open(save_layout_path, "w", encoding="utf-8") as f:
            json.dump(layout, f, ensure_ascii=False, indent=2)

    stats = {
        "footprints": len(fps),
        "nets": len(net_items),
        "pad_bindings": bound,
        "routes": len(routes or []),
        "track_segments": track_segments,
        "ground_planes": ground_planes,
        "missing_bindings": missing,
        "board_mm": [BOARD_W, BOARD_H],
        "zones": [{"name": z[0], "rect": list(z[1:5]), "refs": z[5]} for z in ZONES],
        "layout": layout,
    }
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="outputs/design.json")
    ap.add_argument("--out", default="outputs/design.kicad_pcb")
    ap.add_argument("--layout", default=None)
    ap.add_argument("--save-layout", default="outputs/board_layout.json")
    ap.add_argument("--routes", default=None, help="走线 JSON，由 drc_loop.py 生成")
    ap.add_argument("--fill-only", action="store_true",
                    help="只加载已有板子并填充铜皮（独立进程，避开 pcbnew 段错误）")
    ap.add_argument("--no-planes", action="store_true",
                    help="不铺 GND 铜皮（DRC 闭环的中间轮使用）")
    a = ap.parse_args()

    if a.fill_only:
        print("已填充 %d 个铜皮：%s" % (fill_zones_in_file(a.out), a.out))
        return

    routes = None
    if a.routes and os.path.isfile(a.routes):
        with open(a.routes, encoding="utf-8") as f:
            routes = json.load(f)

    st = build(a.design, a.out, a.layout, a.save_layout, routes,
               planes=not a.no_planes)
    if not a.no_planes:
        subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--fill-only",
             "--out", a.out], check=True
        )
    print("已生成", a.out)
    print("封装 %d 个 / 网络 %d 条 / 焊盘绑定 %d 处 / 走线 %d 段" %
          (st["footprints"], st["nets"], st["pad_bindings"], st["track_segments"]))
    if st["missing_bindings"]:
        print("警告：以下节点未绑定焊盘", st["missing_bindings"])
    else:
        print("引脚级网络绑定完整，无遗漏")


if __name__ == "__main__":
    main()
