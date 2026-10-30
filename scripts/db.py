"""MongoDB 数据层：本地 mongod 优先，连不上时退化为 mongomock（开发/演示用）。

集合规划（数据库名 circuit_agent）：
  - users       用户账号 {username, salt, pw_hash}
  - sessions    token 会话 {token, username, created_at}
  - designs     设计历史 {owner, prompt, board, components, nets, files, created_at}
  - cache       LLM 结果缓存 {prompt_hash, design, created_at}
  - agent_state 多 Agent 共享状态（键值）{key, value, updated_at}

真实部署：本机先起 mongod（见 README），MONGO_URI 默认 mongodb://localhost:27017。
"""
import os
import hashlib
import datetime
import uuid

try:
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure, DuplicateKeyError
    HAS_PYMONGO = True
except Exception:
    HAS_PYMONGO = False

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "circuit_agent"

_client = None
_mode = None  # 'real' | 'mock'


def _connect():
    """返回 MongoClient（带库选择），并缓存模式。"""
    global _client, _mode
    if _client is not None:
        return _client
    if HAS_PYMONGO:
        # 1) 优先连真实本地 mongod
        try:
            c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
            c.admin.command("ping")
            _client, _mode = c, "real"
            return c
        except Exception:
            pass
        # 2) 退化到 mongomock，保证不装 mongod 也能跑通流程
        try:
            import mongomock
            _client, _mode = mongomock.MongoClient(), "mock"
            return _client
        except Exception:
            pass
    raise RuntimeError("无法连接 MongoDB，且 mongomock 也不可用，请 pip install pymongo mongomock")


def db():
    """当前数据库对象。"""
    return _connect()[DB_NAME]


def mode():
    """'real' 或 'mock'，供状态提示。"""
    _connect()
    return _mode


# ----------------------------- 用户 / 鉴权 -----------------------------
def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def create_user(username: str, password: str):
    users = db().users
    if users.find_one({"username": username}):
        raise ValueError("用户名已存在")
    salt = uuid.uuid4().hex
    users.insert_one({
        "username": username,
        "salt": salt,
        "pw_hash": _hash_password(password, salt),
        "created_at": datetime.datetime.utcnow(),
    })
    return username


def verify_user(username: str, password: str):
    u = db().users.find_one({"username": username})
    if not u:
        return None
    if u["pw_hash"] != _hash_password(password, u["salt"]):
        return None
    return username


def create_session(username: str) -> str:
    token = uuid.uuid4().hex
    db().sessions.insert_one({
        "token": token,
        "username": username,
        "created_at": datetime.datetime.utcnow(),
    })
    return token


def user_by_token(token: str):
    if not token:
        return None
    s = db().sessions.find_one({"token": token})
    return s["username"] if s else None


# ----------------------------- 设计历史 -----------------------------
def save_design(owner: str, prompt: str, design: dict, files: dict) -> str:
    doc = {
        "owner": owner,
        "prompt": prompt,
        "board": design.get("board", ""),
        "components": design.get("components", []),
        "nets": design.get("nets", []),
        "files": files or {},
        "created_at": datetime.datetime.utcnow(),
    }
    res = db().designs.insert_one(doc)
    return str(res.inserted_id)


def list_designs(owner: str, limit: int = 50):
    cur = db().designs.find({"owner": owner}).sort("created_at", -1).limit(limit)
    out = []
    for d in cur:
        out.append({
            "id": str(d["_id"]),
            "board": d.get("board", ""),
            "prompt": d.get("prompt", ""),
            "summary": f"{len(d.get('components', []))} 元件 / {len(d.get('nets', []))} 网络",
            "created_at": d.get("created_at").isoformat() if d.get("created_at") else "",
        })
    return out


def get_design(design_id: str, owner: str):
    from bson import ObjectId
    try:
        d = db().designs.find_one({"_id": ObjectId(design_id), "owner": owner})
    except Exception:
        return None
    if not d:
        return None
    d["id"] = str(d["_id"])
    return d


# ----------------------------- LLM 结果缓存 -----------------------------
def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()


def cache_get(prompt: str):
    hit = db().cache.find_one({"prompt_hash": _prompt_hash(prompt)})
    return hit["design"] if hit else None


def cache_set(prompt: str, design: dict):
    db().cache.update_one(
        {"prompt_hash": _prompt_hash(prompt)},
        {"$set": {"design": design, "created_at": datetime.datetime.utcnow()}},
        upsert=True,
    )


# ----------------------------- 多 Agent 共享状态 -----------------------------
def agent_state_get(key: str):
    d = db().agent_state.find_one({"key": key})
    return d["value"] if d else None


def agent_state_set(key: str, value):
    db().agent_state.update_one(
        {"key": key},
        {"$set": {"value": value, "updated_at": datetime.datetime.utcnow()}},
        upsert=True,
    )


def agent_state_all():
    return [{"key": d["key"], "value": d["value"]}
            for d in db().agent_state.find()]
