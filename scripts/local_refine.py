"""本地增量修改器：学校 LLM 不可用时，按中文追加指令修改设计 JSON。

支持三类交互修正：
  增：再加一个 10k 电阻 / 增加 2 个 LED
  删：去掉按钮 / 删除 R2
  改：把 R1 改成 1k / 把 LED 换成绿色

改完的 design 直接交给 kicad_net / kicad_sch 重新生成并排版。
"""
import re

# (规范 type, 默认 value, ref 前缀, 默认 package, 识别关键词)
TYPE_RULES = [
    ("发光二极管", "红色 LED", "D", "LED_0805",
     ("led", "发光二极管", "发光", "指示灯", "灯")),
    ("电阻", "10k", "R", "0805",
     ("电阻", "限流", "上拉", "下拉")),
    ("电容", "100nF", "C", "0805", ("电容", "去耦")),
    ("按钮", "轻触开关", "SW", "SW_PUSH",
     ("按钮", "按键", "轻触", "开关")),
    ("晶振", "16MHz", "Y", "Crystal", ("晶振", "晶体")),
    ("蜂鸣器", "有源蜂鸣器", "BZ", "Buzzer", ("蜂鸣器", "buzzer")),
    ("电池", "2×AA 3V", "BT", "BatteryHolder_2AA", ("电池", "电池座")),
]
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5}


def _match_type(text):
    t = (text or "").lower()
    for spec in TYPE_RULES:
        if any(k in t for k in spec[4]):
            return spec
    return None


def _next_ref(comps, prefix):
    used = []
    for c in comps:
        m = re.match(rf"^{prefix}(\d+)$", c.get("ref", ""), re.I)
        if m:
            used.append(int(m.group(1)))
    return f"{prefix}{max(used) + 1 if used else 1}"


def _parse_count(text):
    m = re.search(r"(\d+|[一两二三四五])\s*个", text)
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else CN_NUM.get(v, 1)
    if re.search(r"[两二]", text):
        return 2
    return 1


def _net(nets, name):
    for n in nets:
        if n["name"] == name:
            return n
    n = {"name": name, "nodes": []}
    nets.append(n)
    return n


def _power_names(nets):
    """电源网络名可能叫 VCC / +5V / +3V3，复用设计里已有的名字避免单端。"""
    have = {n["name"] for n in nets}
    pos = next((x for x in ("+5V", "VCC", "+3V3", "VDD") if x in have), "+5V")
    gnd = next((x for x in ("GND", "VSS") if x in have), "GND")
    return pos, gnd


# 同义词：用户口语 -> 设计里可能出现的字段文本
SYNONYMS = {
    "按钮": ("按钮", "按键", "开关", "轻触"),
    "开关": ("开关", "按钮", "按键", "轻触"),
    "按键": ("按键", "按钮", "开关", "轻触"),
    "led": ("led", "发光二极管", "指示灯", "灯"),
    "发光二极管": ("发光二极管", "led", "灯"),
    "电阻": ("电阻", "限流", "上拉", "下拉"),
}


def _target_refs(comps, target):
    """把 'R1' / '按钮' / 'LED' 解析为实际元件位号列表。"""
    t = (target or "").strip()
    direct = [c["ref"] for c in comps if c["ref"].lower() == t.lower()]
    if direct:
        return direct
    tl = t.lower()
    words = [tl] + list(SYNONYMS.get(tl, ()))
    return [c["ref"] for c in comps
            if any(w in (c.get("type", "") + " " + c.get("value", "") + " " + c.get("note", "")).lower()
                   for w in words)]


def _delete(design, instruction):
    comps, nets = design["components"], design.get("nets", [])
    m = re.search(r"(?:去掉|删除|移除|不要|取消)\s*([A-Za-z]{1,3}\d+|[\u4e00-\u9fa5A-Za-z]+)", instruction)
    if not m:
        return "未识别要删除的元件，可以说“去掉按钮”或“删除 R2”"
    refs = _target_refs(comps, m.group(1))
    if not refs:
        return f"没找到要删除的元件：{m.group(1)}"
    design["components"] = [c for c in comps if c["ref"] not in refs]
    for net in nets:
        net["nodes"] = [nd for nd in net["nodes"] if nd["ref"] not in refs]
    design["nets"] = [n for n in nets if n["nodes"]]
    return f"已删除 {', '.join(refs)}，并清理了相关网络连接"


def _change(design, instruction):
    comps = design["components"]
    m = re.search(r"(?:把|将)\s*(\S+?)\s*(?:改成|改为|换成|设置为)\s*([\w.ΩkKM]+)", instruction)
    if not m:
        return "未识别修改指令，可以说“把 R1 改成 1k”或“把 LED 换成绿色”"
    refs = _target_refs(comps, m.group(1))
    if not refs:
        return f"没找到要修改的元件：{m.group(1)}"
    value = m.group(2)
    changed = [c for c in comps if c["ref"] in refs]
    # 「把 LED 换成绿色」这类颜色词，补成「绿色 LED」更规范
    for color in ("绿色", "红色", "蓝色", "黄色", "白色"):
        if value == color and any("led" in (c.get("type", "") + c.get("value", "")).lower() for c in changed):
            value = color + " LED"
            break
    for c in changed:
        c["value"] = value
    return f"已将 {', '.join(refs)} 的参数改为 {value}"


def _insert_series(comps, nets, spec, target, side, value):
    """把新元件真正串到目标元件的一侧。

    side='L'：插在目标 pin1 之前（电源/上游侧）；side='R'：插在目标 pin2 之后
    （下游侧）。做法是把目标该脚从原网络摘下，新元件两脚分别接原网络和新网络，
    新网络另一端接回目标脚——这样才是电气串联，而不是各自悬空接电源。
    返回 (新元件位号, 说明)；失败返回 (None, 原因)。
    """
    typ, _default, prefix, package, _ = spec
    hit = next((c for c in comps if c["ref"].lower() == target.lower()), None)
    if not hit:
        return None, f"没找到要串联的元件：{target}"
    pin = "1" if side == "L" else "2"
    old = next((n for n in nets if any(nd["ref"].lower() == target.lower()
                                       and str(nd["pin"]) == pin for nd in n["nodes"])), None)
    if not old:
        return None, f"{target} 的 pin{pin} 没接任何网络，无法在其{'左' if side == 'L' else '右'}侧串联"

    ref = _next_ref(comps, prefix)
    # 新网络名：沿用原网络前缀 + 目标位号，便于在网表里看出串联关系
    base = re.sub(r"[+.-]", "", old["name"])
    new_name = f"{base}_{target}_SER" if base else f"{target}_SER"
    k = 1
    while any(n["name"] == new_name for n in nets):
        k += 1
        new_name = f"{base}_{target}_SER{k}" if base else f"{target}_SER{k}"

    comps.append({"ref": ref, "type": typ, "value": value, "package": package,
                  "quantity": 1,
                  "note": f"按追加要求与 {target} 串联，[SERIES:{target}:{side}]"})
    # 目标脚从原网络摘下，改接新网络
    old["nodes"] = [nd for nd in old["nodes"]
                    if not (nd["ref"].lower() == target.lower() and str(nd["pin"]) == pin)]
    bridge = {"name": new_name, "nodes": []}
    nets.append(bridge)
    if side == "L":
        old["nodes"].append({"ref": ref, "pin": "1"})        # 上游侧：新电阻左边接原网络
        bridge["nodes"] = [{"ref": ref, "pin": "2"}, {"ref": target, "pin": pin}]
    else:
        old["nodes"].append({"ref": ref, "pin": "2"})        # 下游侧：新电阻右边接原网络
        bridge["nodes"] = [{"ref": target, "pin": pin}, {"ref": ref, "pin": "1"}]
    return ref, f"已将 {ref} 串联到 {target} 的{'左' if side == 'L' else '右'}侧（{old['name']} → {ref} → {target}）"


def _add(design, instruction):
    comps, nets = design["components"], design.setdefault("nets", [])
    m = re.search(r"(?:再加|添加|增加|加)\s*(.*)", instruction)
    if not m:
        return "未识别添加指令，可以说“再加一个 10k 电阻”或“增加 2 个 LED”"
    rest = m.group(1)
    spec = _match_type(rest)
    if not spec:
        return "没认出要添加的元件类型，支持：LED / 电阻 / 电容 / 按钮 / 晶振 / 蜂鸣器 / 电池"
    typ, default_val, prefix, package, _ = spec
    count = min(_parse_count(rest), 6)

    # 拓扑指令：跟某元件串联 + 放在其左/右侧。优先处理，避免被当成普通加元件。
    sm = re.search(r"(?:跟|与|和|同)\s*([A-Za-z]{1,3}\d+)\s*串联|串联\s*(?:到|在|于)?\s*([A-Za-z]{1,3}\d+)", rest)
    if sm:
        target = sm.group(1) or sm.group(2)
        side = "R" if re.search(r"(?:右边|右侧|后面|后方|下游)", rest) else "L"
        vm2 = re.search(r"(\d+(?:\.\d+)?\s*(?:k|K|M|nF|uF|pF|MHz|Ω))\b", rest)
        value = vm2.group(1) if vm2 else default_val
        results = []
        for _ in range(count):
            ref, note = _insert_series(comps, nets, spec, target, side, value)
            if ref is None:
                return note
            results.append(note)
        return "；".join(results)

    vm = re.search(r"(\d+(?:\.\d+)?\s*(?:k|K|M|nF|uF|pF|MHz|Ω)?)(?!\w)", rest)
    value = vm.group(1) if vm else default_val
    for color in ("绿色", "红色", "蓝色", "黄色", "白色"):
        if color in rest:
            value = color + " LED" if prefix == "D" else color
            break

    added, is_led = [], (prefix == "D")
    vcc_name, gnd_name = _power_names(nets)
    existing_series = sum(1 for n in nets if n["name"].startswith("LED_") and n["name"] != "LED_PWR")
    for i in range(count):
        ref = _next_ref(comps, prefix)
        comps.append({"ref": ref, "type": typ, "value": value,
                      "package": package, "quantity": 1,
                      "note": "按追加要求新增，请在 KiCad 中确认连接"})
        if is_led:
            # LED 自动配限流电阻，复用论文式"电阻→LED"串联导线
            rref = _next_ref(comps, "R")
            comps.append({"ref": rref, "type": "电阻", "value": "330Ω",
                          "package": "0805", "quantity": 1, "note": "新增 LED 的限流电阻"})
            idx = existing_series + i + 1
            name = "LED_PWR" if idx == 1 else ("LED_D13" if idx == 2 else f"LED_N{idx}")
            _net(nets, name)["nodes"].extend([
                {"ref": rref, "pin": "2"}, {"ref": ref, "pin": "1"}])
            _net(nets, vcc_name)["nodes"].append({"ref": rref, "pin": "1"})
            _net(nets, gnd_name)["nodes"].append({"ref": ref, "pin": "2"})
            added.extend([ref, rref])
        else:
            _net(nets, vcc_name)["nodes"].append({"ref": ref, "pin": "1"})
            _net(nets, gnd_name)["nodes"].append({"ref": ref, "pin": "2"})
            added.append(ref)
    return f"已添加 {', '.join(added)}（接到 {vcc_name} / {gnd_name}，请在 KiCad 中确认连接）"


def local_refine(design: dict, instruction: str):
    """就地修改 design，返回 (design, 变更说明)。"""
    text = (instruction or "").strip()
    if any(k in text for k in ("去掉", "删除", "移除", "不要", "取消")):
        note = _delete(design, text)
    elif any(k in text for k in ("改成", "改为", "换成", "设置为")):
        note = _change(design, text)
    elif any(k in text for k in ("加", "增加", "添加")):
        note = _add(design, text)
    else:
        note = "未识别指令，可以说：再加一个 10k 电阻 / 把 LED 换成绿色 / 去掉按钮"
    return design, note
