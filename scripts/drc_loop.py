"""DRC 闭环自动布线：让 KiCad 的 DRC 报告反过来驱动走线，直到未连接项归零。

闭环逻辑（每轮）：
    生成板子 -> kicad-cli pcb drc --format json -> 解析「项目之间的连接缺失」
    -> 每条缺失连接给出两个焊盘的精确坐标与网络名
    -> 双层网格 A* 自动布线（避开其它网络的焊盘与已布走线）
    -> 把新走线写回板子 -> 重新跑 DRC -> 统计未连接项是否下降

violations 与 unconnected_items 分开统计：前者是设计规则违规（必须 0），
后者是尚未布线的连接（闭环的目标是把它们降到 0）。

必须用 KiCad 自带 Python 运行（需要 pcbnew）：

    C:/Users/24013/Desktop/工具/bin/python.exe scripts/drc_loop.py
"""
import argparse
import collections
import heapq
import json
import math
import os
import re
import subprocess
import sys
import time

import pcbnew

import kicad_pcb as kp

KICAD_CLI = os.environ.get(
    "KICAD_CLI", r"C:/Users/24013/Desktop/工具/bin/kicad-cli.exe"
)

GRID = 0.5            # 布线网格 mm
CLEAR_MM = 0.36       # 障碍外扩（> 默认 clearance 0.2 + 半线宽 0.125）
TRACK_W = 0.25
VIA_D = 0.6
VIA_COST = 2.5        # 换层代价：压低它，让长线愿意走空的 B.Cu，别把 F.Cu 堵死
MAX_ITER = 8
POWER_NETS = {"+5V"}

GX = int(kp.BOARD_W / GRID)
GY = int(kp.BOARD_H / GRID)
LAYER_NAME = {0: "F.Cu", 1: "B.Cu"}


# ---------------------------------------------------------------- 占用网格

def build_occupancy(board, routes):
    """(gx, gy, layer) -> 占用该格的网络名集合。

    同网络的焊盘/走线可以互相穿过；其它网络一律视为障碍。
    """
    occ = collections.defaultdict(set)

    def mark_rect(x0, y0, x1, y1, net, layers):
        gx0 = max(0, int(math.floor((x0 - CLEAR_MM) / GRID)))
        gy0 = max(0, int(math.floor((y0 - CLEAR_MM) / GRID)))
        gx1 = min(GX, int(math.ceil((x1 + CLEAR_MM) / GRID)))
        gy1 = min(GY, int(math.ceil((y1 + CLEAR_MM) / GRID)))
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                for L in layers:
                    occ[(gx, gy, L)].add(net)

    for fp in board.GetFootprints():
        for pad in fp.Pads():
            net = pad.GetNetname() or ""
            bb = pad.GetBoundingBox()
            x0 = kp.to_mm(bb.GetX())
            y0 = kp.to_mm(bb.GetY())
            x1 = x0 + kp.to_mm(bb.GetWidth())
            y1 = y0 + kp.to_mm(bb.GetHeight())
            layers = (0, 1) if pad.IsOnLayer(pcbnew.B_Cu) else (0,)
            mark_rect(x0, y0, x1, y1, net, layers)

    for r in routes:
        net = r["net"]
        layers = (0,) if r.get("layer") == "F.Cu" else (1,)
        for (ax, ay), (bx, by) in zip(r["points"], r["points"][1:]):
            mark_rect(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by),
                      net, layers)
        for vx, vy in r.get("vias", []):
            mark_rect(vx - VIA_D / 2, vy - VIA_D / 2,
                      vx + VIA_D / 2, vy + VIA_D / 2, net, (0, 1))
    return occ


def blocked(occ, gx, gy, layer, net):
    if gx < 2 or gy < 2 or gx > GX - 2 or gy > GY - 2:
        return True
    owners = occ.get((gx, gy, layer))
    if not owners:
        return False
    return any(o != net for o in owners)


def via_ok(occ, gx, gy, net):
    """过孔位置周围不能有别的网络，避免 via-to-pad clearance 违规。"""
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for L in (0, 1):
                owners = occ.get((gx + dx, gy + dy, L))
                if owners and any(o != net for o in owners):
                    return False
    return True


def pad_layers(board, x, y):
    """给定焊盘中心坐标，返回它所在的层集合（THT 两层，SMD 仅 F.Cu）。"""
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            p = pad.GetPosition()
            if abs(kp.to_mm(p.x) - x) < 0.3 and abs(kp.to_mm(p.y) - y) < 0.3:
                return (0, 1) if pad.IsOnLayer(pcbnew.B_Cu) else (0,)
    return (0,)


# ---------------------------------------------------------------- A* 布线

def astar(occ, p0, p1, net, start_layers, end_layers, margin=40):
    sx, sy = int(round(p0[0] / GRID)), int(round(p0[1] / GRID))
    ex, ey = int(round(p1[0] / GRID)), int(round(p1[1] / GRID))
    xlo, xhi = max(2, min(sx, ex) - margin), min(GX - 2, max(sx, ex) + margin)
    ylo, yhi = max(2, min(sy, ey) - margin), min(GY - 2, max(sy, ey) + margin)

    def h(gx, gy, L):
        return abs(gx - ex) + abs(gy - ey) + (0 if L in end_layers else 1.5)

    # 密集连接器（如 Micro-USB 0.65mm 间距）外扩后，焊盘周围一圈会被
    # 邻近网络的障碍覆盖，走线连「挤出来」都做不到。端点及其 3x3 邻域
    # 强制放行，让 A* 能先把线引到开阔处再换层；实际间距由下一轮 DRC 把关。
    endpoints = {
        (gx, gy)
        for cx, cy in ((sx, sy), (ex, ey))
        for gx in range(cx - 1, cx + 2)
        for gy in range(cy - 1, cy + 2)
    }

    def blk(gx, gy, L):
        if (gx, gy) in endpoints:
            return False
        return blocked(occ, gx, gy, L, net)

    counter = 0
    heap = []
    came = {}
    gscore = {}
    for L in start_layers:
        if blk(sx, sy, L):
            continue
        gscore[(sx, sy, L)] = 0.0
        heapq.heappush(heap, (h(sx, sy, L), 0.0, counter, (sx, sy, L)))
        counter += 1

    goal = None
    while heap:
        _f, g, _c, state = heapq.heappop(heap)
        gx, gy, L = state
        if g > gscore.get(state, float("inf")) + 1e-9:
            continue
        if (gx, gy) == (ex, ey) and L in end_layers:
            goal = state
            break
        # 同层四方向
        for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = gx + ddx, gy + ddy
            if nx < xlo or nx > xhi or ny < ylo or ny > yhi:
                continue
            if blk(nx, ny, L):
                continue
            ns = (nx, ny, L)
            ng = g + 1.0
            if ng < gscore.get(ns, float("inf")) - 1e-9:
                gscore[ns] = ng
                came[ns] = state
                heapq.heappush(heap, (ng + h(nx, ny, L), ng, counter, ns))
                counter += 1
        # 换层（打过孔）
        if via_ok(occ, gx, gy, net):
            nl = 1 - L
            ns = (gx, gy, nl)
            if not blk(gx, gy, nl):
                ng = g + VIA_COST
                if ng < gscore.get(ns, float("inf")) - 1e-9:
                    gscore[ns] = ng
                    came[ns] = state
                    heapq.heappush(heap, (ng + h(gx, gy, nl), ng, counter, ns))
                    counter += 1

    if goal is None:
        return None
    path = [goal]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    return path


def compress(points):
    """去掉共线中间点，只保留折点。"""
    if len(points) <= 2:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        ax, ay = out[-1]
        bx, by = points[i]
        cx, cy = points[i + 1]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) > 1e-9:
            out.append(points[i])
    out.append(points[-1])
    return out


def path_to_routes(path, net, p0, p1):
    """把 A* 网格路径转成同层 route 列表，换层处插入过孔。"""
    routes = []
    seg = [(path[0][0] * GRID, path[0][1] * GRID)]
    seg_layer = path[0][2]
    for prev, cur in zip(path, path[1:]):
        if cur[2] != prev[2]:
            pts = compress(seg)
            routes.append({
                "net": net, "layer": LAYER_NAME[seg_layer],
                "points": pts,
                "vias": [[prev[0] * GRID, prev[1] * GRID]],
            })
            seg = [(cur[0] * GRID, cur[1] * GRID)]
            seg_layer = cur[2]
        else:
            seg.append((cur[0] * GRID, cur[1] * GRID))
    pts = compress(seg)
    if pts:
        routes.append({"net": net, "layer": LAYER_NAME[seg_layer],
                       "points": pts, "vias": []})
    # 首尾吸附到焊盘精确中心，保证 DRC 判定为连通（偏移 <= 0.25mm）
    if routes:
        routes[0]["points"][0] = [round(p0[0], 4), round(p0[1], 4)]
        routes[-1]["points"][-1] = [round(p1[0], 4), round(p1[1], 4)]
    return routes


def mark_route(occ, routes, net):
    """把刚生成的走线登记进占用表，避免同一轮后续走线撞上。"""
    for r in routes:
        L = 0 if r["layer"] == "F.Cu" else 1
        for (ax, ay), (bx, by) in zip(r["points"], r["points"][1:]):
            gx0 = int(math.floor((min(ax, bx) - CLEAR_MM) / GRID))
            gx1 = int(math.ceil((max(ax, bx) + CLEAR_MM) / GRID))
            gy0 = int(math.floor((min(ay, by) - CLEAR_MM) / GRID))
            gy1 = int(math.ceil((max(ay, by) + CLEAR_MM) / GRID))
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    occ[(gx, gy, L)].add(net)
        for vx, vy in r.get("vias", []):
            cx, cy = int(round(vx / GRID)), int(round(vy / GRID))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for layer in (0, 1):
                        occ[(cx + dx, cy + dy, layer)].add(net)


# ---------------------------------------------------------------- 闭环编排

NET_RE = re.compile(r"\[([^\]]+)\]")


def parse_unconnected(report):
    edges = []
    for item in report.get("unconnected_items", []):
        its = item.get("items", [])
        if len(its) < 2:
            continue
        a, b = its[0], its[1]
        m = NET_RE.search(a.get("description", ""))
        net = m.group(1) if m else ""
        edges.append({
            "net": net,
            "p0": [a["pos"]["x"], a["pos"]["y"]],
            "p1": [b["pos"]["x"], b["pos"]["y"]],
            "d0": a.get("description", ""),
            "d1": b.get("description", ""),
        })
    return edges


def infer_layers(desc, board, x, y):
    """从 DRC 描述判断端点在哪一层。

    端点不一定是焊盘：闭环布线后，DRC 会把「已布走线的自由端」也列为待连接项，
    描述形如 '走线 [GND] (B.Cu), 长度: 17.5 mm'。层判断错了 A* 会找不到终点。
    """
    if "(B.Cu)" in desc:
        return (1,)
    if "金属化焊盘" in desc:
        return (0, 1)
    if "(F.Cu)" in desc or desc.startswith("F.Cu"):
        return (0,)
    return pad_layers(board, x, y)


def run_drc(pcb_path, report_path):
    cmd = [KICAD_CLI, "pcb", "drc", "--format", "json",
           "--output", report_path, pcb_path]
    subprocess.run(cmd, capture_output=True, text=True)
    with open(report_path, encoding="utf-8") as f:
        return json.load(f)


def loop(design, pcb, layout_file, routes_file, report_file, max_iter):
    routes = []
    history = []
    seen_edges = set()
    fail_counts = {}
    for it in range(1, max_iter + 1):
        t0 = time.time()
        # 中间轮不铺铜：避免同一进程反复 zone fill 触发 pcbnew 段错误，
        # 也让 DRC 报告只反映走线效果，下降曲线干净。
        kp.build(design, pcb, layout_file, layout_file, routes, planes=False)
        board = pcbnew.LoadBoard(pcb)
        report = run_drc(pcb, report_file)
        violations = report.get("violations", [])
        # GND 由全板铺铜负责，不需要（也不应该）再为它生成走线
        edges = [e for e in parse_unconnected(report) if e["net"] != "GND"]

        # 布线顺序决定成败：先布两点信号（短线优先），电源大网络放最后。
        # 否则 GND/+5V 的一堆长走线会先把板面切成孤岛，后面的信号线过不去。
        def edge_priority(e):
            dist = abs(e["p0"][0] - e["p1"][0]) + abs(e["p0"][1] - e["p1"][1])
            # 长线最受限，优先给它留通道；电源网络永远排最后
            return (e["net"] in POWER_NETS, -dist)

        edges.sort(key=edge_priority)
        n_before = len(edges)

        history.append({
            "iteration": it,
            "violations": len(violations),
            "unconnected_before": n_before,
            "routes_total": len(routes),
            "seconds": 0.0,
            "new_routes": 0,
            "failed_edges": 0,
        })
        print("[轮 %d] 违规 %d / 未连接 %d / 已有走线 %d 条"
              % (it, len(violations), n_before, len(routes)), flush=True)

        if n_before == 0:
            break

        occ = build_occupancy(board, routes)
        new_routes, failed = 0, 0
        for edge in edges:
            net, p0, p1 = edge["net"], edge["p0"], edge["p1"]
            key = (net, round(p0[0], 2), round(p0[1], 2),
                   round(p1[0], 2), round(p1[1], 2))
            if key in seen_edges or fail_counts.get(key, 0) >= 3:
                continue
            sl = infer_layers(edge["d0"], board, p0[0], p0[1])
            el = infer_layers(edge["d1"], board, p1[0], p1[1])
            path = astar(occ, p0, p1, net, sl, el, margin=40)
            if path is None:
                path = astar(occ, p0, p1, net, sl, el, margin=max(GX, GY))
            if path is None:
                failed += 1
                fail_counts[key] = fail_counts.get(key, 0) + 1
                continue
            seen_edges.add(key)
            made = path_to_routes(path, net, p0, p1)
            mark_route(occ, made, net)
            routes.extend(made)
            new_routes += len(made)

        history[-1]["new_routes"] = new_routes
        history[-1]["failed_edges"] = failed
        history[-1]["seconds"] = round(time.time() - t0, 2)
        if new_routes == 0:
            print("  本轮无法再补线，停止迭代", flush=True)
            break

    # 最终一轮：铺上 GND 铜皮，用独立进程填充（避免段错误），再出最终 DRC
    kp.build(design, pcb, layout_file, layout_file, routes, planes=True)
    subprocess.run(
        [sys.executable, os.path.abspath(kp.__file__), "--fill-only",
         "--out", pcb], check=True
    )
    final = run_drc(pcb, report_file)
    with open(routes_file, "w", encoding="utf-8") as f:
        json.dump(routes, f, ensure_ascii=False, indent=2)

    vtypes = collections.Counter(v["type"] for v in final.get("violations", []))
    result = {
        "iterations": history,
        "final": {
            "violations": len(final.get("violations", [])),
            "violation_types": dict(vtypes),
            "unconnected_items": len(final.get("unconnected_items", [])),
            "schematic_parity": len(final.get("schematic_parity", [])),
            "routes": len(routes),
            "track_segments": sum(max(0, len(r["points"]) - 1) for r in routes),
            "vias": sum(len(r.get("vias", [])) for r in routes),
        },
        "board_mm": [kp.BOARD_W, kp.BOARD_H],
        "grid_mm": GRID,
        "note": "violations 为设计规则违规（须为 0）；unconnected_items 为尚未布线的连接。",
    }
    with open(report_file.replace(".json", "_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="outputs/design.json")
    ap.add_argument("--pcb", default="outputs/design.kicad_pcb")
    ap.add_argument("--layout", default="outputs/board_layout.json")
    ap.add_argument("--routes", default="outputs/board_routes.json")
    ap.add_argument("--drc", default="outputs/drc_report.json")
    ap.add_argument("--max-iter", type=int, default=MAX_ITER)
    a = ap.parse_args()

    result = loop(a.design, a.pcb, a.layout, a.routes, a.drc, a.max_iter)
    f = result["final"]
    print("\n=== 闭环结束 ===")
    for h in result["iterations"]:
        print("  轮 %d: 违规 %d / 未连接 %d -> 补 %d 条走线 / 失败 %d / %.1fs"
              % (h["iteration"], h["violations"], h["unconnected_before"],
                 h["new_routes"], h["failed_edges"], h["seconds"]))
    print("最终：违规 %d / 未连接 %d / 走线 %d 段 / 过孔 %d 个"
          % (f["violations"], f["unconnected_items"],
             f["track_segments"], f["vias"]))
    if f["violation_types"]:
        print("违规类型：", f["violation_types"])
    print("报告：", a.drc.replace(".json", "_summary.json"))


if __name__ == "__main__":
    main()
