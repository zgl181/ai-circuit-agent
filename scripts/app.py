"""W3 本地网页界面（MongoDB 后端版）：自然语言需求 -> 电路板 BOM + 网表 + KiCad 原理图。

后端以 MongoDB 为存储：
  - 用户/鉴权：register / login，token 存 sessions 集合
  - 设计历史：每次生成落库 designs 集合（owner 隔离）
  - LLM 缓存：相同需求直接命中 cache 集合，省一次模型调用
  - 多 Agent 共享状态：agent_state 集合（键值），供 W6 多 Agent 协作

零前端框架（stdlib http.server）。运行：.venv/Scripts/python.exe scripts/app.py
打开：浏览器访问 http://localhost:8765
"""
import json
import os
import sys
import subprocess
import re
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)          # 项目根
OUT = os.path.join(PROJECT, "outputs")  # 产物目录
WEB_INDEX = os.path.join(HERE, "web", "index.html")
KICAD_CLI = os.environ.get("KICAD_CLI", r"C:/Users/24013/Desktop/工具/bin/kicad-cli")

sys.path.insert(0, HERE)
import design_agent      # noqa: E402
import kicad_net         # noqa: E402
import kicad_sch         # noqa: E402
import db                # noqa: E402


def _write_outputs(design, prompt):
    """把设计落盘为 json/net/sch/svg，返回 (files_dict, svg_ok)。"""
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "design.json"), "w", encoding="utf-8") as f:
        json.dump(design, f, ensure_ascii=False, indent=2)

    net = kicad_net.build_netlist(design)
    with open(os.path.join(OUT, "design.net"), "w", encoding="utf-8") as f:
        f.write(net)

    sch = kicad_sch.build_sch(design)
    sch_path = os.path.join(OUT, "design.kicad_sch")
    with open(sch_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(sch)

    svg_path = os.path.join(OUT, "design.svg")
    if os.path.exists(svg_path):
        os.remove(svg_path)
    proc = subprocess.run(
        [KICAD_CLI, "sch", "export", "svg", "--output", OUT, sch_path],
        capture_output=True, text=True,
    )
    svg_ok = os.path.exists(svg_path)
    svg = open(svg_path, encoding="utf-8", errors="ignore").read() if svg_ok else None
    return {
        "json": "design.json", "net": "design.net",
        "sch": "design.kicad_sch", "svg": "design.svg",
    }, svg_ok, svg, proc.stderr


def generate(prompt: str, owner: str) -> dict:
    """跑完整管线；先做 LLM 缓存命中，再落库。"""
    cached = db.cache_get(prompt)
    if cached:
        design, from_cache = cached, True
    else:
        design = design_agent.design(prompt)
        db.cache_set(prompt, design)
        from_cache = False

    files, svg_ok, svg, kerr = _write_outputs(design, prompt)
    did = db.save_design(owner, prompt, design, files)

    comps = design.get("components", [])
    nets = design.get("nets", [])
    return {
        "ok": True,
        "board": design.get("board", ""),
        "prompt": prompt,
        "owner": owner,
        "from_cache": from_cache,
        "design_id": did,
        "components": comps,
        "nets": nets,
        "svg": svg,
        "svg_ok": svg_ok,
        "kicad_error": (kerr or "")[:500],
        "files": files,
        "summary": f"{len(comps)} 个元件 / {len(nets)} 条网络",
    }


def get_token(req_headers, body):
    auth = req_headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (body or {}).get("token")


def send(self, code, body, ctype="application/json; charset=utf-8"):
    if not isinstance(body, (bytes, bytearray)):
        body = body.encode("utf-8")
    self.send_response(code)
    self.send_header("Content-Type", ctype)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            try:
                html = open(WEB_INDEX, encoding="utf-8").read()
            except FileNotFoundError:
                send(self, 500, "index.html 缺失"); return
            send(self, 200, html, "text/html; charset=utf-8")
            return
        if u.path.startswith("/files/"):
            fp = os.path.join(OUT, os.path.basename(u.path))
            if os.path.isfile(fp):
                send(self, 200, open(fp, "rb").read(), "application/octet-stream")
            else:
                send(self, 404, "文件不存在")
            return
        # 设计历史列表
        if u.path == "/api/designs":
            token = get_token(self.headers, {})
            owner = db.user_by_token(token) or "guest"
            send(self, 200, json.dumps({"ok": True, "owner": owner,
                 "items": db.list_designs(owner)}, ensure_ascii=False))
            return
        # Agent 共享状态
        if u.path == "/api/agent/state":
            send(self, 200, json.dumps({"ok": True, "state": db.agent_state_all()},
                 ensure_ascii=False))
            return
        send(self, 404, "未找到该路径")

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = {}

        # 注册
        if u.path == "/api/register":
            try:
                db.create_user(body.get("username", ""), body.get("password", ""))
                send(self, 200, json.dumps({"ok": True}))
            except ValueError as e:
                send(self, 400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return

        # 登录
        if u.path == "/api/login":
            uname = db.verify_user(body.get("username", ""), body.get("password", ""))
            if uname:
                send(self, 200, json.dumps({"ok": True, "token": db.create_session(uname),
                     "username": uname}, ensure_ascii=False))
            else:
                send(self, 401, json.dumps({"ok": False, "error": "用户名或密码错误"}, ensure_ascii=False))
            return

        # 生成原理图
        if u.path == "/api/design":
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                send(self, 400, json.dumps({"ok": False, "error": "请输入电路需求"}))
                return
            owner = db.user_by_token(get_token(self.headers, body)) or "guest"
            try:
                result = generate(prompt, owner)
                send(self, 200, json.dumps(result, ensure_ascii=False))
            except Exception as e:
                send(self, 200, json.dumps({"ok": False,
                     "error": f"{type(e).__name__}: {str(e)[:300]}"}, ensure_ascii=False))
            return

        # Agent 共享状态写入
        if u.path == "/api/agent/state":
            key = body.get("key")
            if not key:
                send(self, 400, json.dumps({"ok": False, "error": "缺少 key"}))
                return
            db.agent_state_set(key, body.get("value"))
            send(self, 200, json.dumps({"ok": True}))
            return

        send(self, 404, "未找到该路径")

    def log_message(self, *args):
        pass


# GET /api/designs/<id> 通过统一路由处理更方便，这里补一个带 id 的处理
def _handle_design_by_id(self, design_id):
    token = get_token(self.headers, {})
    owner = db.user_by_token(token) or "guest"
    d = db.get_design(design_id, owner)
    if not d:
        send(self, 404, json.dumps({"ok": False, "error": "未找到该设计或无权访问"}, ensure_ascii=False))
        return
    # 同时把该设计重新落盘，便于前端直接预览/下载
    _write_outputs({
        "board": d.get("board", ""),
        "components": d.get("components", []),
        "nets": d.get("nets", []),
    }, d.get("prompt", ""))
    send(self, 200, json.dumps({
        "ok": True, "board": d.get("board", ""), "prompt": d.get("prompt", ""),
        "components": d.get("components", []), "nets": d.get("nets", []),
        "svg": (open(os.path.join(OUT, "design.svg"), encoding="utf-8", errors="ignore").read()
                if os.path.exists(os.path.join(OUT, "design.svg")) else None),
        "svg_ok": os.path.exists(os.path.join(OUT, "design.svg")),
        "files": {"json": "design.json", "net": "design.net",
                  "sch": "design.kicad_sch", "svg": "design.svg"},
        "summary": f"{len(d.get('components', []))} 元件 / {len(d.get('nets', []))} 网络",
    }, ensure_ascii=False))


# 给 Handler 注入带 id 的 GET 处理
def _do_GET(self):
    u = urlparse(self.path)
    m = re.match(r"^/api/designs/([0-9a-fA-F]{24})$", u.path)
    if m:
        _handle_design_by_id(self, m.group(1))
        return
    # 否则走原逻辑
    Handler.do_GET_original(self)


Handler.do_GET_original = Handler.do_GET
Handler.do_GET = _do_GET


if __name__ == "__main__":
    PORT = 8765
    print("=" * 50)
    store = db.mode()
    print(f" MongoDB 模式: {store}  (real=本地 mongod, mock=兜底)")
    print(" 电路板设计 AI 界面已启动")
    print(f" 在浏览器打开:  http://localhost:{PORT}")
    print(" 按 Ctrl+C 停止")
    print("=" * 50)
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
