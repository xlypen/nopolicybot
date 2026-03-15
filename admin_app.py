"""
Админ-панель для мониторинга бота: статистика пользователей, портреты, ранги, настроения.
Кнопка «Построить портрет» использует архив сообщений, которые бот прочитал в чате.

Запуск: python admin_app.py
По умолчанию: http://127.0.0.1:5000
"""

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import subprocess
import time
import urllib.request
import urllib.parse
from datetime import datetime
from functools import wraps
from pathlib import Path

logger = logging.getLogger(__name__)
MODE_CHANGES_LOG = Path(__file__).resolve().parent / "mode_changes.log"

from flask import Flask, Response, g, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS

from utils.fastapi_proxy import proxy_response, proxy_to_fastapi
from dotenv import load_dotenv
from config.validate_secrets import validate_secrets
from routes.social_graph_routes import register_social_graph_routes
import bot_settings
from ai.prompts import get_all_prompts, reset_prompts, set_prompt
from services.audit_log import read_recent, write_event
from services.cache_backend import CacheBackend
from services.monitoring import build_alerts, record_request, snapshot, to_prometheus_text
from services.rate_limiter import RateLimiter
from services.structured_logging import configure_logging

load_dotenv(Path(__file__).resolve().parent / ".env", encoding="utf-8-sig")

configure_logging("flask-admin")
validate_secrets("admin")


def _allowed_origins() -> list[str]:
    raw = str(os.getenv("ALLOWED_ORIGINS", "") or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for item in raw.split(","):
        origin = str(item or "").strip().rstrip("/")
        if origin and origin not in out:
            out.append(origin)
    return out


def _normalize_origin(origin: str) -> str:
    src = str(origin or "").strip().rstrip("/")
    if not src:
        return ""
    try:
        parsed = urllib.parse.urlsplit(src)
    except Exception:
        return src.lower()
    scheme = str(parsed.scheme or "").strip().lower()
    host = str(parsed.hostname or "").strip().lower()
    port = parsed.port
    if not scheme or not host:
        return src.lower()
    if (scheme == "http" and (port is None or int(port) == 80)) or (scheme == "https" and (port is None or int(port) == 443)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{int(port)}"


def _request_origin() -> str:
    scheme = str(request.headers.get("x-forwarded-proto", "") or "").split(",")[0].strip().lower()
    host = str(request.headers.get("x-forwarded-host", "") or "").split(",")[0].strip()
    if not host:
        host = str(request.host or "").strip()
    if not scheme:
        scheme = str(request.scheme or "").strip().lower() or "http"
    if not host:
        return ""
    return _normalize_origin(f"{scheme}://{host}")


def _origin_allowed(origin: str) -> bool:
    src = _normalize_origin(origin)
    if not src:
        return True
    req_origin = _request_origin()
    if req_origin and src == req_origin:
        return True
    allowed = {_normalize_origin(item) for item in _allowed_origins()}
    allowed.discard("")
    if not allowed:
        return False
    return src in allowed


app = Flask(__name__)
_secret_key = os.getenv("ADMIN_SECRET_KEY", "").strip()
if not _secret_key:
    _secret_key_file = Path(__file__).resolve().parent / "data" / ".flask_secret_key"
    if _secret_key_file.is_file():
        _secret_key = _secret_key_file.read_text(encoding="utf-8").strip()
    if not _secret_key:
        _secret_key = secrets.token_hex(32)
        _secret_key_file.parent.mkdir(parents=True, exist_ok=True)
        _secret_key_file.write_text(_secret_key, encoding="utf-8")
        logger.info("Generated new ADMIN_SECRET_KEY → data/.flask_secret_key")
app.secret_key = _secret_key
CORS(app, resources={r"/api/*": {"origins": _allowed_origins()}}, allow_headers=["*"], methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
mimetypes.add_type("text/javascript", ".jsx")
USERS_JSON = Path(__file__).resolve().parent / "user_stats.json"
# Пароль админа: из переменной окружения или из файла (задаётся один раз через страницу /login)
_ADMIN_PASSWORD_ENV = (os.getenv("ADMIN_PASSWORD") or "").strip()
ADMIN_PASSWORD_FILE = Path(__file__).resolve().parent / "data" / "admin_password.txt"

try:
    import bcrypt as _bcrypt
except ImportError:
    _bcrypt = None


def _hash_password(plain: str) -> str:
    if _bcrypt:
        return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    return plain


def _check_password(plain: str, stored: str) -> bool:
    if _bcrypt and stored.startswith("$2"):
        return _bcrypt.checkpw(plain.encode("utf-8"), stored.encode("utf-8"))
    return hmac.compare_digest(plain, stored)


def _migrate_plaintext_password():
    """One-time migration: hash plaintext password file if it exists and is not already hashed."""
    if not ADMIN_PASSWORD_FILE.is_file() or not _bcrypt:
        return
    try:
        stored = ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        if stored and not stored.startswith("$2"):
            hashed = _hash_password(stored)
            ADMIN_PASSWORD_FILE.write_text(hashed, encoding="utf-8")
            logger.info("Migrated admin password to bcrypt hash")
    except Exception as e:
        logger.warning("Password migration failed: %s", e)


_migrate_plaintext_password()


def _get_admin_password() -> str:
    """Пароль входа в админку: сначала из .env, иначе из файла data/admin_password.txt."""
    if _ADMIN_PASSWORD_ENV:
        return _ADMIN_PASSWORD_ENV
    if ADMIN_PASSWORD_FILE.is_file():
        try:
            return ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def _login_csrf_token() -> str:
    token = str(session.get("login_csrf_token") or "").strip()
    if not token:
        token = secrets.token_urlsafe(32)
        session["login_csrf_token"] = token
    return token
RESTART_FLAG_PATH = Path(__file__).resolve().parent / "restart_bot.flag"
BOT_LAST_START_PATH = Path(__file__).resolve().parent / "bot_last_start.json"
RESET_POLITICAL_COUNT_PATH = Path(__file__).resolve().parent / "reset_political_count.json"
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

RANK_LABELS = {"loyal": "🇷🇺 Лояльный", "neutral": "⚪ Нейтральный", "opposition": "🔴 Оппозиция", "unknown": "❓ Неизвестно"}

_avatar_cache: dict[str, str] = {}
_avatar_img_cache: dict[str, tuple[float, bytes, str]] = {}
_AVATAR_IMG_CACHE_TTL_SEC = 3600
_API_CACHE = CacheBackend(namespace="admin_api", default_ttl=45)
_FLASK_RATE_LIMITER = RateLimiter(namespace="flask_api_ratelimit")

# Токен для участников: просмотр своего профиля и графа связей (без входа в админку)
PARTICIPANT_TOKEN_TTL_SEC = 7 * 24 * 3600  # 7 дней


def _flask_hardening_config() -> dict:
    return {
        "rate_limit_per_min": max(20, int(os.getenv("FLASK_RATE_LIMIT_PER_MIN", "300"))),
        "max_url_length": max(256, int(os.getenv("FLASK_MAX_URL_LENGTH", "2600"))),
        "max_body_bytes": max(1024, int(os.getenv("FLASK_MAX_BODY_BYTES", "1048576"))),
    }


def _client_ip_from_request() -> str:
    xff = str(request.headers.get("x-forwarded-for", "") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64] or "unknown"
    return str(request.remote_addr or "unknown")[:64]


@app.before_request
def _monitoring_before_request():
    g._request_started_at = time.perf_counter()
    path = str(request.path or "/")
    if path.startswith("/api/"):
        origin = str(request.headers.get("origin", "") or "").strip()
        if origin and not _origin_allowed(origin):
            write_event(
                "flask_request_blocked_origin_not_allowed",
                severity="warning",
                source="flask_admin",
                payload={"path": path, "method": request.method, "origin": origin[:240]},
            )
            return jsonify({"ok": False, "error": "origin not allowed"}), 403
    if app.testing or os.getenv("PYTEST_CURRENT_TEST"):
        return None
    if not path.startswith("/api/"):
        return None
    cfg = _flask_hardening_config()
    full_url = str(request.url or "")
    if len(full_url) > int(cfg["max_url_length"]):
        write_event(
            "flask_request_blocked_url_too_long",
            severity="warning",
            source="flask_admin",
            payload={"path": path, "method": request.method, "url_length": len(full_url)},
        )
        return jsonify({"ok": False, "error": "url too long"}), 414
    content_len = int(request.content_length or 0)
    if content_len > int(cfg["max_body_bytes"]):
        write_event(
            "flask_request_blocked_body_too_large",
            severity="warning",
            source="flask_admin",
            payload={"path": path, "method": request.method, "content_length": int(content_len)},
        )
        return jsonify({"ok": False, "error": "payload too large"}), 413
    ip = _client_ip_from_request()
    rl = _FLASK_RATE_LIMITER.hit(f"ip:{ip}", int(cfg["rate_limit_per_min"]), 60)
    if not bool(rl.get("allowed")):
        write_event(
            "flask_request_rate_limited",
            severity="warning",
            source="flask_admin",
            payload={"path": path, "method": request.method, "ip": ip, "limit": rl.get("limit")},
        )
        resp = jsonify({"ok": False, "error": "rate limit exceeded", "retry_after": int(rl.get("retry_after", 1) or 1)})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(int(rl.get("retry_after", 1) or 1))
        return resp
    return None


@app.after_request
def _monitoring_after_request(response):
    started = getattr(g, "_request_started_at", None)
    if started is None:
        return response
    elapsed = (time.perf_counter() - float(started)) * 1000.0
    try:
        record_request("flask_admin", request.method, request.path, int(response.status_code), elapsed)
        if int(response.status_code) >= 500:
            write_event(
                "flask_5xx_response",
                severity="error",
                source="flask_admin",
                payload={"path": request.path, "method": request.method, "status_code": int(response.status_code)},
            )
    except Exception:
        pass
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://api.telegram.org; "
        "connect-src 'self' ws: wss:;",
    )
    if bool(request.is_secure):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def _participant_secret() -> bytes:
    raw = (os.getenv("PARTICIPANT_SECRET") or os.getenv("ADMIN_SECRET_KEY") or "").strip()
    return raw.encode("utf-8")


def _participant_token(user_id: int, expiry_ts: int | None = None) -> str:
    """Генерирует подписанный токен для ссылки «Мой профиль» (участник)."""
    exp = expiry_ts or (int(time.time()) + PARTICIPANT_TOKEN_TTL_SEC)
    payload = f"{user_id}:{exp}".encode("utf-8")
    sig = hmac.new(_participant_secret(), payload, hashlib.sha256).digest()
    payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def _participant_verify(token: str) -> tuple[int | None, str | None]:
    """Проверяет токен участника. Возвращает (user_id, None) или (None, error_message)."""
    if not token or "." not in token:
        return None, "Неверная ссылка"
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64 + "==")
        sig = base64.urlsafe_b64decode(sig_b64 + "==")
        expected = hmac.new(_participant_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            return None, "Неверная ссылка"
        parts = payload.decode("utf-8").split(":")
        if len(parts) != 2:
            return None, "Неверная ссылка"
        uid, exp = int(parts[0]), int(parts[1])
        if time.time() > exp:
            return None, "Ссылка устарела. Запросите новую через бота: /me"
        return uid, None
    except Exception as e:
        logger.debug("Participant token verify failed: %s", e)
        return None, "Неверная ссылка"


def _participant_me_url(user_id: int, base_url: str | None = None) -> str:
    """Возвращает полный URL страницы «Мой профиль» для участника. base_url — от request.host_url или env."""
    base = (base_url or os.getenv("PARTICIPANT_BASE_URL") or os.getenv("ADMIN_BASE_URL") or "").strip().rstrip("/")
    if not base:
        try:
            base = request.host_url.rstrip("/")
        except RuntimeError:
            base = "http://127.0.0.1:5000"
    if not base:
        base = "http://127.0.0.1:5000"
    return f"{base}/me?token={_participant_token(user_id)}"


def _cache_key(prefix: str, **params) -> str:
    parts = [str(prefix)]
    for key in sorted(params):
        parts.append(f"{key}={params[key]}")
    return "|".join(parts)


def _cached_json(prefix: str, ttl: int, builder, **params):
    key = _cache_key(prefix, **params)
    payload = _API_CACHE.get(key)
    if payload is not None:
        return payload
    payload = builder()
    _API_CACHE.set(key, payload, ttl=ttl)
    return payload


def _parse_chat_id_arg(name: str = "chat_id") -> tuple[int | None, str | None]:
    raw = (request.args.get(name) or "all").strip().lower()
    if raw == "all":
        return None, None
    if str(raw).lstrip("-").isdigit():
        return int(raw), None
    return None, f"invalid {name}"


def _graph_snapshot_scope(chat_id: int | None, period: str, ego_user: int | None, limit: int | None) -> str:
    return _cache_key(
        "graph_snapshot",
        chat_id="all" if chat_id is None else int(chat_id),
        period=str(period or "7d"),
        ego_user="" if ego_user is None else int(ego_user),
        limit="" if limit is None else int(limit),
    )


def _graph_edge_id(edge: dict) -> str:
    a = int(edge.get("source", 0) or 0)
    b = int(edge.get("target", 0) or 0)
    if not a and not b:
        return "0|0"
    lo, hi = (a, b) if a <= b else (b, a)
    return f"{int(lo)}|{int(hi)}"


def _graph_build_version(graph: dict) -> str:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_fp = sorted(
        (
            int(n.get("id", 0) or 0),
            round(float(n.get("influence_score", 0.0) or 0.0), 6),
            round(float(n.get("centrality", 0.0) or 0.0), 6),
            int(n.get("community_id", 0) or 0),
            str(n.get("tier", "") or ""),
        )
        for n in nodes
    )
    edge_fp = sorted(
        (
            _graph_edge_id(e),
            round(float(e.get("weight_period", 0.0) or 0.0), 6),
            round(float(e.get("bridge_score", 0.0) or 0.0), 6),
            int(e.get("community_id", 0) or 0),
        )
        for e in edges
    )
    raw = json.dumps({"nodes": node_fp, "edges": edge_fp}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _graph_delta(prev_graph: dict | None, curr_graph: dict) -> dict:
    prev = prev_graph or {"nodes": [], "edges": [], "meta": {}}
    p_nodes = {int(n.get("id", 0) or 0): n for n in (prev.get("nodes") or []) if int(n.get("id", 0) or 0) != 0}
    c_nodes = {int(n.get("id", 0) or 0): n for n in (curr_graph.get("nodes") or []) if int(n.get("id", 0) or 0) != 0}
    p_edges = {_graph_edge_id(e): e for e in (prev.get("edges") or [])}
    c_edges = {_graph_edge_id(e): e for e in (curr_graph.get("edges") or [])}

    remove_node_ids = [int(uid) for uid in p_nodes.keys() if uid not in c_nodes]
    upsert_nodes = [n for uid, n in c_nodes.items() if uid not in p_nodes or p_nodes.get(uid) != n]

    remove_edge_ids = [eid for eid in p_edges.keys() if eid not in c_edges]
    upsert_edges = [e for eid, e in c_edges.items() if eid not in p_edges or p_edges.get(eid) != e]

    changed = bool(remove_node_ids or upsert_nodes or remove_edge_ids or upsert_edges or (prev.get("meta") or {}) != (curr_graph.get("meta") or {}))
    return {
        "changed": changed,
        "delta": {
            "full_replace": prev_graph is None,
            "remove_node_ids": remove_node_ids,
            "upsert_nodes": upsert_nodes,
            "remove_edge_ids": remove_edge_ids,
            "upsert_edges": upsert_edges,
            "meta": curr_graph.get("meta") or {},
        },
    }


def _graph_history_get(scope: str) -> dict:
    payload = _API_CACHE.get(scope)
    return payload if isinstance(payload, dict) else {}


def _graph_history_set(scope: str, version: str, graph: dict, ttl_sec: int = 300) -> None:
    history = _graph_history_get(scope)
    latest = history.get("latest")
    prev = history.get("prev")
    if isinstance(latest, dict) and latest.get("version") != version:
        prev = latest
    history = {
        "latest": {"version": version, "graph": graph},
        "prev": prev if isinstance(prev, dict) else None,
    }
    _API_CACHE.set(scope, history, ttl=ttl_sec)


def _load_users() -> dict:
    if not USERS_JSON.exists():
        return {"users": {}}
    try:
        data = json.loads(USERS_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "users" in data else {"users": {}}
    except Exception:
        return {"users": {}}


def _save_tone_override(user_id: str, value: str | None, add_to_history: bool = False, save_current_to_history: bool = False) -> bool:
    """Сохраняет ручное настроение. value=None — сброс на авто."""
    from user_stats import save_tone_override
    return save_tone_override(int(user_id), value, add_to_history, save_current_to_history)


def _get_effective_tone(u: dict) -> str:
    from user_stats import get_effective_tone
    return get_effective_tone(u)


def _get_avatar_file_path(user_id: str) -> str | None:
    if user_id in _avatar_cache:
        return _avatar_cache[user_id]
    if not BOT_TOKEN:
        return None
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUserProfilePhotos?user_id={user_id}&limit=1"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        result = data.get("result") or {}
        photos = result.get("photos") or []
        if not photos or not photos[0]:
            return None
        file_id = photos[0][0].get("file_id")
        if not file_id:
            return None
        url2 = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        with urllib.request.urlopen(url2, timeout=5) as r2:
            data2 = json.loads(r2.read().decode())
        result2 = data2.get("result") or {}
        file_path = result2.get("file_path")
        if file_path:
            _avatar_cache[user_id] = file_path
        return file_path
    except Exception:
        return None


def _fetch_telegram_user_info(user_id: str) -> dict | None:
    if not BOT_TOKEN:
        return None
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id={user_id}"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        if not data.get("ok"):
            return None
        chat = data.get("result") or {}
        info = {
            "id": chat.get("id"),
            "type": chat.get("type"),
            "first_name": chat.get("first_name", ""),
            "last_name": chat.get("last_name", ""),
            "username": chat.get("username", ""),
            "language_code": chat.get("language_code", ""),
            "is_premium": chat.get("is_premium", False),
        }
        if info.get("username"):
            info["profile_link"] = f"https://t.me/{info['username']}"
        return info
    except Exception:
        return None


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if app.testing or os.getenv("PYTEST_CURRENT_TEST"):
            return f(*args, **kwargs)
        if not _get_admin_password():
            return f(*args, **kwargs)
        if not session.get("admin_logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


register_social_graph_routes(app, login_required)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "flask-admin"})


def _portrait_status_from_api(user_id: str, kind: str) -> bool:
    """Получить статус портрета из API v2 (для рендера страниц)."""
    token = str(os.getenv("ADMIN_TOKEN", "")).strip()
    if not token:
        return False
    port = int(os.getenv("API_PORT", "8001"))
    if kind == "building":
        url = f"http://127.0.0.1:{port}/api/v2/portrait/portrait-building-status?user_id={user_id}"
    else:
        url = f"http://127.0.0.1:{port}/api/v2/portrait/user/{user_id}/portrait-image-status"
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("building" if kind == "building" else "generating"))
    except Exception:
        return False


def _portrait_building_ids_from_api() -> list[str]:
    """Список user_id, для которых идёт построение портрета (из API v2)."""
    token = str(os.getenv("ADMIN_TOKEN", "")).strip()
    if not token:
        return []
    port = int(os.getenv("API_PORT", "8001"))
    url = f"http://127.0.0.1:{port}/api/v2/portrait/portrait-building-status"
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return list(body.get("building_user_ids") or [])
    except Exception:
        return []


@app.route("/api/v2/graph/<path:subpath>", methods=["GET"])
@login_required
def api_v2_graph_proxy(subpath: str):
    """Прокси graph API в FastAPI v2 (сессия админа проверена)."""
    body, status = proxy_to_fastapi(f"/api/v2/graph/{subpath}")
    return proxy_response(body, status)


@app.route("/api/v2/admin/<path:subpath>", methods=["GET", "POST"])
@login_required
def api_v2_admin_proxy(subpath: str):
    """Прокси admin API в FastAPI v2 (сессия админа проверена)."""
    data = request.get_data() if request.method == "POST" and request.is_json else None
    body, status = proxy_to_fastapi(f"/api/v2/admin/{subpath}", method=request.method, data=data)
    return proxy_response(body, status)


@app.route("/api/v2/recommendations", methods=["GET"])
@app.route("/api/v2/recommendations/<path:subpath>", methods=["GET", "POST"])
@login_required
def api_v2_recommendations_proxy(subpath: str = ""):
    """Прокси recommendations API в FastAPI v2."""
    path = f"/api/v2/recommendations/{subpath}".rstrip("/") if subpath else "/api/v2/recommendations"
    data = request.get_data() if request.method == "POST" and request.is_json else None
    body, status = proxy_to_fastapi(path, method=request.method, data=data)
    return proxy_response(body, status)


@app.route("/api/v2/predictive/<path:subpath>", methods=["GET"])
@login_required
def api_v2_predictive_proxy(subpath: str):
    """Прокси predictive API в FastAPI v2."""
    body, status = proxy_to_fastapi(f"/api/v2/predictive/{subpath}")
    return proxy_response(body, status)


@app.route("/api/v2/storage/<path:subpath>", methods=["GET", "POST"])
@login_required
def api_v2_storage_proxy(subpath: str):
    """Прокси storage API в FastAPI v2."""
    data = request.get_data() if request.method == "POST" and request.is_json else None
    body, status = proxy_to_fastapi(f"/api/v2/storage/{subpath}", method=request.method, data=data)
    return proxy_response(body, status)


@app.route("/api/v2/metrics/<path:subpath>", methods=["GET"])
@login_required
def api_v2_metrics_proxy(subpath: str):
    """Прокси metrics API (user, chat health) в FastAPI v2."""
    body, status = proxy_to_fastapi(f"/api/v2/metrics/{subpath}")
    return proxy_response(body, status)


@app.route("/api/v2/personality/<path:subpath>", methods=["GET", "POST"])
@login_required
def api_v2_personality_proxy(subpath: str):
    """Прокси personality API в FastAPI v2."""
    data = request.get_data() if request.method == "POST" and request.is_json else None
    body, status = proxy_to_fastapi(f"/api/v2/personality/{subpath}", method=request.method, data=data)
    return proxy_response(body, status)


@app.route("/api/v2/portrait/<path:subpath>", methods=["GET", "POST"])
@login_required
def api_v2_portrait_proxy(subpath: str):
    """Прокси portrait API в FastAPI v2."""
    data = request.get_data() if request.method == "POST" and request.is_json else None
    body, status = proxy_to_fastapi(f"/api/v2/portrait/{subpath}", method=request.method, data=data)
    return proxy_response(body, status)


@app.route("/api/v2/settings", methods=["GET", "POST"])
@app.route("/api/v2/chat-mode", methods=["GET", "POST"])
@app.route("/api/v2/reset-political-count", methods=["POST"])
@login_required
def api_v2_settings_proxy():
    """Прокси settings/chat-mode/reset-political-count в FastAPI v2."""
    data = request.get_data() if request.method == "POST" and request.is_json else None
    body, status = proxy_to_fastapi(request.path, method=request.method, data=data)
    return proxy_response(body, status)


@app.route("/api/monitoring/metrics")
@login_required
def api_monitoring_metrics():
    fmt = (request.args.get("format") or "json").strip().lower()
    snap = snapshot("flask_admin")
    if fmt in {"prom", "prometheus", "text"}:
        return Response(to_prometheus_text(snap, prefix="nopolicybot_flask_admin"), mimetype="text/plain")
    return jsonify({"ok": True, "metrics": snap})


@app.route("/api/monitoring/alerts")
@login_required
def api_monitoring_alerts():
    try:
        limit = max(1, min(500, int(request.args.get("limit", "120"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid limit"}), 400
    snap = snapshot("flask_admin")
    rows = read_recent(limit=limit)
    return jsonify({"ok": True, "alerts": build_alerts(snap, rows), "metrics": snap, "audit_events": rows[-20:]})


@app.route("/login", methods=["GET", "POST"])
def login():
    pw = _get_admin_password()
    error = ""
    status = 200
    csrf_token = _login_csrf_token()

    if request.method == "POST":
        form_token = str(request.form.get("csrf_token") or "").strip()
        if not form_token or not hmac.compare_digest(form_token, csrf_token):
            session["login_csrf_token"] = secrets.token_urlsafe(32)
            return render_template(
                "login.html",
                set_password=(not bool(pw)),
                error="Сессия формы истекла. Обновите страницу и попробуйте снова.",
                csrf_token=session["login_csrf_token"],
            ), 400

    # Первый запуск: пароль не задан — показываем форму «Задайте пароль»
    if not pw:
        if request.method == "POST":
            new_pw = (request.form.get("password") or "").strip()
            if len(new_pw) >= 6:
                try:
                    ADMIN_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
                    ADMIN_PASSWORD_FILE.write_text(_hash_password(new_pw), encoding="utf-8")
                    session["admin_logged_in"] = True
                    session.pop("login_csrf_token", None)
                    return redirect(url_for("admin"))
                except Exception as e:
                    logger.warning("Не удалось сохранить пароль: %s", e)
                    error = "Не удалось сохранить пароль. Повторите попытку."
                    status = 500
            else:
                error = "Пароль должен содержать минимум 6 символов."
                status = 400
        return render_template("login.html", set_password=True, error=error, csrf_token=csrf_token), status

    if request.method == "POST":
        entered = (request.form.get("password") or "").strip()
        if _check_password(entered, pw):
            session["admin_logged_in"] = True
            session.pop("login_csrf_token", None)
            session.pop("_login_attempts", None)
            return redirect(url_for("admin"))
        attempts = session.get("_login_attempts", 0) + 1
        session["_login_attempts"] = attempts
        if attempts >= 5:
            error = "Слишком много попыток. Подождите и попробуйте снова."
            status = 429
        else:
            error = "Неверный пароль."
            status = 401
    return render_template("login.html", set_password=False, error=error, csrf_token=csrf_token), status


@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("login"))


@app.route("/")
def landing():
    """Главная страница сайта: описание и ссылка на вход в админку."""
    return render_template("landing.html")


@app.route("/me")
def participant_me():
    """Страница для участника чата: свой профиль и граф связей (по подписанной ссылке от бота)."""
    token = (request.args.get("token") or "").strip()
    user_id, err = _participant_verify(token)
    if err or not user_id:
        return render_template("participant_me.html", error=err or "Неверная ссылка", user_id=None), 403
    data = _load_users()
    u = data.get("users", {}).get(str(user_id))
    if not u:
        return render_template("participant_me.html", error="Профиль не найден", user_id=None), 404
    from user_stats import get_user
    u = get_user(int(user_id), u.get("display_name", ""))
    my_connections, me_chat_ids = _collect_user_connections(user_id=user_id, chat_id=None, limit=50)
    from utils.labels import TONE_RU, TOPIC_RU
    from services.portrait_image import PORTRAIT_IMAGES_DIR
    portrait_path = PORTRAIT_IMAGES_DIR / f"{user_id}.png"
    portrait_exists = portrait_path.exists()
    me_url_refresh = _participant_me_url(user_id, request.host_url.rstrip("/"))
    return render_template(
        "participant_me.html",
        error=None,
        user_id=str(user_id),
        u=u,
        rank_labels=RANK_LABELS,
        effective_tone=_get_effective_tone(u),
        my_connections=my_connections,
        tone_ru=TONE_RU,
        topic_ru=TOPIC_RU,
        portrait_exists=portrait_exists,
        me_url=me_url_refresh,
        me_chat_ids=me_chat_ids,
        me_token=token,
    )


def _collect_user_connections(user_id: int, chat_id: int | None, limit: int = 50) -> tuple[list[dict], list[int]]:
    import social_graph
    from user_stats import get_user_display_names

    connections_all = social_graph.get_connections(chat_id)
    my_connections = [
        r for r in connections_all
        if int(r.get("user_a", 0) or 0) == int(user_id) or int(r.get("user_b", 0) or 0) == int(user_id)
    ]
    my_connections = sorted(my_connections, key=lambda r: int(r.get("message_count_7d", 0) or 0), reverse=True)[:max(1, int(limit))]
    my_chat_ids = sorted(
        {
            int(r.get("chat_id", 0) or 0)
            for r in my_connections
            if int(r.get("chat_id", 0) or 0) != 0
        }
    )
    names = get_user_display_names()
    for r in my_connections:
        ua, ub = int(r.get("user_a", 0) or 0), int(r.get("user_b", 0) or 0)
        peer_id = ub if ua == int(user_id) else ua
        r["peer_name"] = names.get(str(peer_id), str(peer_id))
    return my_connections, my_chat_ids


@app.route("/admin/user/<path:user_id>")
@login_required
def admin_user_profile(user_id):
    if not str(user_id).lstrip("-").isdigit():
        return "Некорректный user_id", 400
    uid = int(user_id)
    chat_id_raw = (request.args.get("chat") or "all").strip()
    chat_id = chat_id_raw if chat_id_raw else "all"
    chat_int = int(chat_id) if chat_id != "all" and str(chat_id).lstrip("-").isdigit() else None

    data = _load_users()
    users = (data or {}).get("users", {}) or {}
    current = users.get(str(uid), {})

    from user_stats import get_user
    u = get_user(uid, current.get("display_name", ""))
    if not u:
        return "Пользователь не найден", 404

    from services.portrait_image import PORTRAIT_IMAGES_DIR
    from user_stats import get_chats, get_user_archive_by_chat
    portrait_path = PORTRAIT_IMAGES_DIR / f"{uid}.png"
    portrait_exists = portrait_path.exists()
    portrait_image_mtime = int(portrait_path.stat().st_mtime) if portrait_exists else 0
    my_connections, _my_chat_ids = _collect_user_connections(user_id=uid, chat_id=chat_int, limit=25)

    # Чаты: из get_chats() + чаты пользователя из архива (если get_chats пуст)
    data = _load_users()
    chats_data = (data or {}).get("chats") or {}
    chats_list = get_chats() or []
    if not chats_list:
        archive_by_chat = get_user_archive_by_chat(uid)
        for cid in sorted(archive_by_chat.keys(), key=lambda x: (x == "unknown", str(x))):
            if cid == "unknown":
                continue
            c = chats_data.get(str(cid), {})
            try:
                cid_int = int(cid)
            except (ValueError, TypeError):
                cid_int = cid
            chats_list.append({"chat_id": cid_int, "title": c.get("title") or str(cid)})
    # Fallback: если всё ещё пусто — взять все чаты из data.chats
    if not chats_list and chats_data:
        for cid, c in chats_data.items():
            if str(cid).strip() and str(cid) != "unknown":
                try:
                    cid_int = int(cid)
                except (ValueError, TypeError):
                    continue
                chats_list.append({"chat_id": cid_int, "title": c.get("title") or str(cid)})
    else:
        # Добавить чаты из архива пользователя, которых нет в get_chats
        archive_by_chat = get_user_archive_by_chat(uid)
        existing_ids = {str(c["chat_id"]) for c in chats_list}
        for cid in archive_by_chat:
            if cid != "unknown" and cid not in existing_ids:
                c = chats_data.get(str(cid), {})
                try:
                    cid_int = int(cid)
                except (ValueError, TypeError):
                    cid_int = cid
                chats_list.append({"chat_id": cid_int, "title": c.get("title") or str(cid)})

    # Всегда добавить текущий chat_id из URL (при переходе из рейтинга и т.д.)
    if chat_id and chat_id != "all" and str(chat_id).lstrip("-").isdigit():
        existing_ids = {str(c["chat_id"]) for c in chats_list}
        if str(chat_id) not in existing_ids:
            c = chats_data.get(str(chat_id), {})
            chats_list.append({"chat_id": int(chat_id), "title": c.get("title") or str(chat_id)})

    return render_template(
        "admin/user_profile.html",
        user_id=str(uid),
        u=u,
        chat_id=chat_id,
        chats=chats_list,
        rank_labels=RANK_LABELS,
        effective_tone=_get_effective_tone(u),
        my_connections=my_connections,
        portrait_exists=portrait_exists,
        portrait_image_mtime=portrait_image_mtime,
        me_url=_participant_me_url(uid, request.host_url.rstrip("/")),
    )


@app.route("/admin/personality/clusters")
@login_required
def admin_personality_clusters():
    """Карта кластеров по личности (P-7)."""
    from user_stats import get_chats
    chat_id = request.args.get("chat_id", "all")
    return render_template("admin/personality_clusters.html", chat_id=chat_id, chats=get_chats())


@app.route("/admin/personality/compare")
@login_required
def admin_personality_compare():
    """Карточка сравнения двух пользователей (P-7)."""
    from user_stats import get_chats
    user_id_a = request.args.get("user_id_a", "")
    user_id_b = request.args.get("user_id_b", "")
    chat_id = request.args.get("chat_id", "all")
    data = _load_users()
    users_raw = data.get("users", {}) or {}
    users_list = sorted(
        [{"id": uid, "name": u.get("display_name") or uid} for uid, u in users_raw.items()],
        key=lambda x: x["name"].lower(),
    )
    return render_template(
        "admin/personality_compare.html",
        user_id_a=user_id_a,
        user_id_b=user_id_b,
        chat_id=chat_id,
        users=users_list,
        chats=get_chats(),
    )


@app.route("/admin")
@login_required
def admin():
    legacy = str(request.args.get("legacy") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not legacy:
        return admin_modern()
    return admin_legacy()


def _chat_mode_descriptions() -> dict[str, str]:
    try:
        from bot_settings import CHAT_MODE_PRESETS

        return {k: v.get("_desc", v.get("_label", k)) for k, v in CHAT_MODE_PRESETS.items()} | {"custom": "Ручные переопределения в настройках чата"}
    except Exception:
        return {
            "default": "Глобальные настройки",
            "soft": "Реакции 1–5, замечания с 5-го",
            "active": "Реакции с 1-го, замечания с 3-го",
            "beast": "Максимум с 1-го",
            "custom": "Ручные переопределения",
        }


@app.route("/admin-legacy")
@login_required
def admin_legacy():
    from user_stats import get_chats, get_users_in_chat

    data = _load_users()
    all_users = data.get("users", {})
    chat_id = request.args.get("chat")
    chats = get_chats()

    if chat_id and chat_id != "all":
        user_ids = set(get_users_in_chat(int(chat_id)))
        users = {uid: u for uid, u in all_users.items() if uid in user_ids}
    else:
        users = all_users

    chat_mode = None
    chat_mode_descriptions = _chat_mode_descriptions()
    if chat_id and chat_id != "all" and str(chat_id).lstrip("-").isdigit():
        try:
            from bot_settings import get_chat_mode
            chat_mode = get_chat_mode(int(chat_id))
        except Exception:
            chat_mode = "default"
    digest_preview = ""
    analysis_brief = ""
    if chat_id and chat_id != "all" and str(chat_id).lstrip("-").isdigit():
        try:
            import social_graph
            digest_preview = social_graph.build_chat_digest(int(chat_id), period_days=1)
        except Exception:
            digest_preview = ""
        try:
            from services.chat_analysis import build_chat_analysis, render_analysis_brief
            data = build_chat_analysis(int(chat_id), period_days=7, include_ai_summary=False)
            analysis_brief = render_analysis_brief(data)
        except Exception:
            analysis_brief = ""

    total = len(users)
    ranks = {}
    total_pol = 0
    total_warn = 0
    for uid, u in users.items():
        r = u.get("rank", "unknown")
        ranks[r] = ranks.get(r, 0) + 1
        total_pol += u.get("stats", {}).get("political_messages", 0)
        total_warn += u.get("stats", {}).get("warnings_received", 0)
    sorted_users = sorted(users.items(), key=lambda x: -x[1].get("stats", {}).get("total_messages", 0))
    return render_template(
        "index.html",
        total=total,
        ranks=ranks,
        total_pol=total_pol,
        total_warn=total_warn,
        users=sorted_users,
        rank_labels=RANK_LABELS,
        chats=chats,
        current_chat=chat_id or "all",
        portrait_building_user_ids=_portrait_building_ids_from_api(),
        digest_preview=digest_preview,
        analysis_brief=analysis_brief,
        chat_mode=chat_mode or "default",
        chat_mode_presets={"default": "По умолчанию", "soft": "Мягкий", "active": "Активный", "beast": "Зверь"},
        chat_mode_descriptions=chat_mode_descriptions,
    )


@app.route("/admin-modern")
@login_required
def admin_modern():
    from user_stats import get_chats

    data = _load_users()
    users = data.get("users", {}) or {}
    total_users = len(users)
    total_messages = 0
    total_political = 0
    total_warnings = 0
    rank_counts: dict[str, int] = {"loyal": 0, "neutral": 0, "opposition": 0, "unknown": 0}
    for u in users.values():
        stats = u.get("stats", {}) or {}
        total_messages += int(stats.get("total_messages", 0) or 0)
        total_political += int(stats.get("political_messages", 0) or 0)
        total_warnings += int(stats.get("warnings_received", 0) or 0)
        rank = str(u.get("rank", "unknown") or "unknown")
        rank_counts[rank] = rank_counts.get(rank, 0) + 1

    return render_template(
        "admin/dashboard.html",
        chats=get_chats(),
        chat_mode_presets={"default": "По умолчанию", "soft": "Мягкий", "active": "Активный", "beast": "Зверь"},
        chat_mode_descriptions=_chat_mode_descriptions(),
        metrics={
            "users": total_users,
            "messages": total_messages,
            "political": total_political,
            "warnings": total_warnings,
            "ranks": rank_counts,
        },
    )


def _parse_setting_value(key: str, val: str | None, defaults: dict):
    if val is None or val == "":
        return None
    default = defaults.get(key)
    if isinstance(default, bool):
        return val in ("true", "1", "on", "yes")
    if isinstance(default, int):
        try:
            return int(val)
        except ValueError:
            return None
    if isinstance(default, float):
        try:
            return float(val.replace(",", "."))
        except ValueError:
            return None
    if isinstance(default, list):
        if val.strip().startswith("["):
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                pass
        return [x.strip() for x in val.split(",") if x.strip()]
    return val.strip()


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """Вкладка настроек бота."""
    from bot_settings import get_all, set_all, DEFAULTS
    if request.method == "POST":
        updates = {}
        bool_keys = {k for k, v in DEFAULTS.items() if isinstance(v, bool)}
        for key in DEFAULTS:
            if key == "chat_settings":
                continue
            val = request.form.get(key)
            if key in bool_keys:
                updates[key] = val in ("1", "true", "on", "yes")
            else:
                parsed = _parse_setting_value(key, val, DEFAULTS)
                if parsed is not None:
                    updates[key] = parsed
        if updates:
            set_all(updates)
        return redirect(url_for("settings"))
    s = get_all()
    covered_keys = {
        "reactions_political_1_5",
        "moderation_enabled",
        "analyze_images",
        "analyze_voice",
        "reactions_on_photos",
        "msgs_before_react",
        "style_moderate_react",
        "style_active_frequency",
        "style_beast_frequency",
        "reset_after_neutral",
        "patience_phrase_enabled",
        "article_line_enabled",
        "use_personalized_remarks",
        "encouragement_enabled",
        "encouragement_style",
        "reactions_1_5_mode",
        "reactions_1_5_positive_emoji",
        "reactions_1_5_negative_emoji",
        "reactions_1_5_neutral_emoji",
        "spontaneous_reactions",
        "spontaneous_max_per_day",
        "spontaneous_min_interval_sec",
        "spontaneous_check_chance",
        "spontaneous_emojis",
        "question_of_day",
        "question_of_day_start_hour",
        "question_of_day_end_hour",
        "question_of_day_min_interval_sec",
        "qod_graph_mode_enabled",
        "reply_to_bot_enabled",
        "reply_kind_enabled",
        "reply_rude_enabled",
        "reply_technical_enabled",
        "reply_pause_on_reject_enabled",
        "reply_pause_sec",
        "reply_pause_text",
        "reply_resume_on_apology_enabled",
        "reply_resume_text",
        "reply_yesterday_quotes_chance",
        "reply_fallback_on_error",
        "greeting_on_join",
        "greeting_text",
        "cmd_ranks_enabled",
        "cmd_stats_enabled",
        "api_min_interval_sec",
        "batch_style_cache_sec",
        "min_context_lines",
        "min_context_lines_1_5",
        "ai_fast_cache_ttl_sec",
        "ai_fast_cache_max_items",
        "ai_parallel_reply_enabled",
        "social_graph_realtime_enabled",
        "social_graph_realtime_interval_sec",
        "social_graph_realtime_min_new_messages",
        "social_graph_advanced_insights_enabled",
        "social_graph_ranked_layout_enabled",
        "social_graph_conflict_forecast_enabled",
        "social_graph_roles_enabled",
        "chat_topic_recommender_enabled",
        "bot_explainability_enabled",
        "content_digest_enabled",
        "content_digest_send_enabled",
        "content_digest_interval_hours",
        "content_digest_chat_id",
        "factcheck_enabled",
        "factcheck_min_interval_sec",
        "factcheck_max_text_len",
    }

    def _fmt_emoji_list(val, default: str) -> str:
        if isinstance(val, list) and val:
            return ",".join(str(x) for x in val)
        if isinstance(val, str) and val:
            return val
        return default

    extra_settings = []
    for key, default in DEFAULTS.items():
        if key == "chat_settings" or key in covered_keys:
            continue
        extra_settings.append({
            "key": key,
            "default": default,
            "value": s.get(key, default),
            "type": "bool" if isinstance(default, bool) else ("list" if isinstance(default, list) else ("number" if isinstance(default, (int, float)) else "text")),
        })

    return render_template(
        "settings.html",
        settings=s,
        _fmt_emoji_list=_fmt_emoji_list,
        extra_settings=extra_settings,
    )


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    """Legacy proxy -> /api/v2/settings."""
    path = "/api/v2/settings"
    body, status = proxy_to_fastapi(path, method=request.method, data=request.get_data() if request.method == "POST" else None)
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/api/chat-mode", methods=["GET", "POST"])
@login_required
def api_chat_mode():
    """Legacy proxy -> /api/v2/chat-mode."""
    path = "/api/v2/chat-mode"
    if request.method == "GET" and request.query_string:
        path += "?" + request.query_string.decode("utf-8")
    body, status = proxy_to_fastapi(path, method=request.method, data=request.get_data() if request.method == "POST" else None)
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/user/<user_id>", methods=["GET", "POST"])
@login_required
def user_detail(user_id):
    chat_id = request.args.get("chat") or request.form.get("chat")
    data = _load_users()
    u = data.get("users", {}).get(user_id)
    if not u:
        return "Пользователь не найден", 404
    if request.method == "POST":
        action = request.form.get("tone_action")
        if action == "set":
            val = (request.form.get("tone_override") or "").strip()
            _save_tone_override(user_id, val if val else None, add_to_history=bool(val))
        elif action == "history":
            val = (request.form.get("tone_history_val") or "").strip()
            if val:
                _save_tone_override(user_id, val)
        elif action == "auto":
            _save_tone_override(user_id, None, save_current_to_history=True)
        data = _load_users()
        u = data.get("users", {}).get(user_id)
        return redirect(url_for("user_detail", user_id=user_id, chat=chat_id or "all"))
    from user_stats import get_user_messages_archive, get_close_attention_views, get_user
    u = get_user(int(user_id), u.get("display_name", ""))  # актуальные данные с миграциями
    archive = get_user_messages_archive(int(user_id), int(chat_id) if chat_id and chat_id != "all" else None)
    from user_stats import get_user_archive_by_chat, get_chats, get_user_images_archive
    archive_by_chat_full = get_user_archive_by_chat(int(user_id))
    chats_list = get_chats()
    chats_titles = {str(c["chat_id"]): c["title"] for c in chats_list}
    # При переходе со вкладки чата показываем только архив этого чата (чтобы не смешивать сообщения)
    if chat_id and chat_id != "all":
        cid_str = str(int(chat_id))
        archive_by_chat = {cid_str: archive_by_chat_full[cid_str]} if cid_str in archive_by_chat_full else {}
    else:
        archive_by_chat = archive_by_chat_full
    archive_count = sum(len(msgs) for msgs in archive_by_chat_full.values())
    images_archive = list(reversed(get_user_images_archive(int(user_id))))
    close_attention_views = get_close_attention_views(int(user_id))
    from services.portrait_image import PORTRAIT_IMAGES_DIR
    portrait_path = PORTRAIT_IMAGES_DIR / f"{user_id}.png"
    portrait_image_exists = portrait_path.exists()
    portrait_image_mtime = int(portrait_path.stat().st_mtime) if portrait_image_exists else 0
    return render_template(
        "user_detail.html",
        user_id=user_id,
        u=u,
        rank_labels=RANK_LABELS,
        effective_tone=_get_effective_tone(u),
        archive_count=archive_count,
        archive_by_chat=archive_by_chat,
        images_archive=images_archive,
        chats_titles=chats_titles,
        chat_id=chat_id or "",
        is_portrait_building=_portrait_status_from_api(str(user_id), "building"),
        is_portrait_image_generating=_portrait_status_from_api(str(user_id), "generating"),
        portrait_image_exists=portrait_image_exists,
        portrait_image_mtime=portrait_image_mtime,
        close_attention_views=close_attention_views,
    )


@app.route("/me/portrait")
def participant_me_portrait():
    """Картинка портрета для страницы участника (проверка по токену)."""
    token = (request.args.get("token") or "").strip()
    user_id, err = _participant_verify(token)
    if err or not user_id:
        return Response(status=403)
    from services.portrait_image import PORTRAIT_IMAGES_DIR
    path = PORTRAIT_IMAGES_DIR / f"{user_id}.png"
    if not path.is_file():
        return Response(status=404)
    return Response(
        path.read_bytes(),
        mimetype="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.route("/avatar/<user_id>")
@login_required
def avatar(user_id):
    now = time.time()
    cached = _avatar_img_cache.get(user_id)
    if cached and (now - cached[0] <= _AVATAR_IMG_CACHE_TTL_SEC):
        data, mimetype = cached[1], cached[2]
        return Response(
            data,
            mimetype=mimetype,
            headers={
                "Cache-Control": f"private, max-age={_AVATAR_IMG_CACHE_TTL_SEC}",
            },
        )
    file_path = _get_avatar_file_path(user_id)
    if not file_path or not BOT_TOKEN:
        return Response(status=404)
    try:
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = r.read()
            ctype = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
        _avatar_img_cache[user_id] = (now, data, ctype)
        return Response(
            data,
            mimetype=ctype,
            headers={
                "Cache-Control": f"private, max-age={_AVATAR_IMG_CACHE_TTL_SEC}",
            },
        )
    except Exception:
        return Response(status=404)


@app.route("/portrait-image/<user_id>")
@login_required
def portrait_image(user_id):
    """Отдаёт сгенерированную картинку портрета пользователя."""
    from services.portrait_image import PORTRAIT_IMAGES_DIR
    path = PORTRAIT_IMAGES_DIR / f"{user_id}.png"
    if not path.exists():
        return Response(status=404)
    try:
        data = path.read_bytes()
        return Response(
            data,
            mimetype="image/png",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except Exception:
        return Response(status=500)


@app.route("/api/user/<user_id>/telegram")
@login_required
def api_telegram_user(user_id):
    info = _fetch_telegram_user_info(user_id)
    if not info:
        return jsonify({"ok": False, "error": "Не удалось загрузить или бот не общался с пользователем"}), 404
    return jsonify({"ok": True, "data": info})


@app.route("/restart-bot", methods=["POST"])
@login_required
def restart_bot():
    try:
        RESTART_FLAG_PATH.write_text("", encoding="utf-8")
        session["restart_requested_at"] = time.time()
        return redirect(url_for("admin") + "?restart=requested")
    except Exception:
        return redirect(url_for("admin") + "?restart=err")


@app.route("/api/restart-status")
@login_required
def api_restart_status():
    requested_at = session.get("restart_requested_at") or 0
    if not requested_at:
        return jsonify({"requested": False, "restarted": False})
    try:
        if BOT_LAST_START_PATH.exists():
            data = json.loads(BOT_LAST_START_PATH.read_text(encoding="utf-8"))
            bot_ts = data.get("ts", 0)
            if bot_ts > requested_at:
                session.pop("restart_requested_at", None)
                return jsonify({"requested": True, "restarted": True})
    except Exception:
        pass
    return jsonify({"requested": True, "restarted": False})


@app.route("/api/chat/<chat_id>/digest-preview")
@login_required
def api_chat_digest_preview(chat_id):
    """Превью дайджеста для выбранного чата (ручной просмотр в админке)."""
    if not str(chat_id).lstrip("-").isdigit():
        return jsonify({"ok": False, "error": "Некорректный chat_id"}), 400
    try:
        import social_graph
        digest = social_graph.build_chat_digest(int(chat_id), period_days=1)
        return jsonify({"ok": True, "digest": digest})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/chat/<chat_id>/analysis")
@login_required
def api_chat_analysis(chat_id):
    """API общего анализа чата. GET: period=7, ai=1 для AI-сводки."""
    if not str(chat_id).lstrip("-").isdigit():
        return jsonify({"ok": False, "error": "Некорректный chat_id"}), 400
    try:
        from services.chat_analysis import build_chat_analysis, render_analysis_brief, render_analysis_full
        from user_stats import get_chats
        period = int(request.args.get("period", 7))
        include_ai = request.args.get("ai", "1") in ("1", "true", "yes")
        data = build_chat_analysis(int(chat_id), period_days=period, include_ai_summary=include_ai)
        chats_map = {str(c["chat_id"]): c.get("title", "") for c in get_chats()}
        chat_title = chats_map.get(str(chat_id), "")
        return jsonify({
            "ok": True,
            "brief": render_analysis_brief(data),
            "full": render_analysis_full(data, chat_title),
            "data": {k: v for k, v in data.items() if k not in ("names", "TONE_RU", "TOPIC_RU", "ROLE_RU", "RANK_LABELS")},
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/chat/<chat_id>/analysis")
@login_required
def chat_analysis_page(chat_id):
    """Полная страница общего анализа чата."""
    from user_stats import get_chats
    from services.chat_analysis import build_chat_analysis, render_analysis_full
    chats = get_chats()
    chats_map = {str(c["chat_id"]): c.get("title", str(c["chat_id"])) for c in chats}
    chat_title = chats_map.get(str(chat_id), str(chat_id))
    try:
        period = int(request.args.get("period", 7))
        include_ai = request.args.get("ai", "1") in ("1", "true", "yes")
        data = build_chat_analysis(int(chat_id), period_days=period, include_ai_summary=True)  # портрет всегда
        full_html = render_analysis_full(data, chat_title)
    except Exception as e:
        full_html = f'<div style="color:#e88;">Ошибка: {e}</div>'
        data = None
    return render_template(
        "chat_analysis.html",
        chat_id=chat_id,
        chat_title=chat_title,
        analysis_html=full_html,
        period_days=request.args.get("period", "7"),
        include_ai=request.args.get("ai", "1"),
        chats=chats,
        current_chat=str(chat_id),
    )


@app.route("/api/portrait-from-storage", methods=["POST"])
@login_required
def api_portrait_from_storage():
    """Legacy proxy -> /api/v2/portrait/portrait-from-storage."""
    body, status = proxy_to_fastapi("/api/v2/portrait/portrait-from-storage", method="POST", data=request.get_data())
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/api/portrait-classify-unknown", methods=["POST"])
@login_required
def api_portrait_classify_unknown():
    """Legacy proxy -> /api/v2/portrait/portrait-classify-unknown."""
    body, status = proxy_to_fastapi("/api/v2/portrait/portrait-classify-unknown", method="POST", data=request.get_data())
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/api/user/<user_id>/close-attention", methods=["POST"])
@login_required
def api_close_attention_toggle(user_id):
    """Включить/выключить режим «пристальное внимание» для пользователя. POST: {enabled: true|false}."""
    try:
        data = request.get_json() or {}
        enabled = data.get("enabled")
        if enabled is None:
            return jsonify({"ok": False, "error": "Нужен enabled (true/false)"}), 400
        from user_stats import set_close_attention_enabled
        try:
            uid = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        if set_close_attention_enabled(uid, bool(enabled)):
            return jsonify({"ok": True, "enabled": bool(enabled)})
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/factcheck-clear-cache", methods=["POST"])
@login_required
def api_factcheck_clear_cache():
    """Очищает кэш факт-чека. POST."""
    try:
        from services.factcheck import clear_factcheck_cache
        count = clear_factcheck_cache()
        return jsonify({"ok": True, "cleared": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/user/<user_id>/factcheck", methods=["POST"])
@login_required
def api_factcheck_toggle(user_id):
    """Включить/выключить факт-чек для пользователя. POST: {enabled: true|false}."""
    try:
        data = request.get_json() or {}
        enabled = data.get("enabled")
        if enabled is None:
            return jsonify({"ok": False, "error": "Нужен enabled (true/false)"}), 400
        from user_stats import set_factcheck_enabled
        try:
            uid = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        if set_factcheck_enabled(uid, bool(enabled)):
            return jsonify({"ok": True, "enabled": bool(enabled)})
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/user/<user_id>/question-of-day", methods=["POST"])
@login_required
def api_question_of_day_toggle(user_id):
    """Включить/выключить «вопрос дня» для пользователя. POST: {enabled: true|false}."""
    try:
        data = request.get_json() or {}
        enabled = data.get("enabled")
        if enabled is None:
            return jsonify({"ok": False, "error": "Нужен enabled (true/false)"}), 400
        from user_stats import set_question_of_day_enabled
        try:
            uid = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        if set_question_of_day_enabled(uid, bool(enabled)):
            return jsonify({"ok": True, "enabled": bool(enabled)})
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/user/<user_id>/question-of-day/destination", methods=["POST"])
@login_required
def api_question_of_day_destination(user_id):
    """Куда отправлять «вопрос дня»: "chat" — в чат, "private" — в личку. POST: {destination: "chat"|"private"}."""
    try:
        data = request.get_json() or {}
        destination = data.get("destination")
        if destination not in ("chat", "private"):
            return jsonify({"ok": False, "error": "Нужен destination: chat или private"}), 400
        from user_stats import set_question_of_day_destination
        try:
            uid = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        if set_question_of_day_destination(uid, destination):
            return jsonify({"ok": True, "destination": destination})
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/user/<user_id>/question-of-day/chats")
@login_required
def api_question_of_day_chats(user_id):
    """Список чатов пользователя для выбора при отправке «вопрос дня»."""
    try:
        uid = int(user_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
    from user_stats import get_user_chats_for_question_of_day
    chats = get_user_chats_for_question_of_day(uid)
    return jsonify({"ok": True, "chats": chats})


@app.route("/api/user/<user_id>/question-of-day/preview", methods=["POST"])
@login_required
def api_question_of_day_preview(user_id):
    """Сгенерировать превью «вопроса дня» по архиву за сегодня."""
    try:
        from user_stats import get_user_messages_for_today, get_user
        from ai_analyzer import generate_question_of_day
        try:
            uid = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        messages = get_user_messages_for_today(uid)
        u = get_user(uid)
        display_name = u.get("display_name") or user_id
        graph_ctx = ""
        if bot_settings.get("qod_graph_mode_enabled"):
            import social_graph
            graph_ctx = social_graph.get_user_graph_context(uid)
        question = generate_question_of_day(messages, display_name, graph_context=graph_ctx)
        return jsonify({"ok": True, "question": question})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _send_telegram_message(chat_id: int, text: str, parse_mode: str | None = None) -> tuple[bool, str, int | None]:
    """Отправляет сообщение через Telegram API. Возвращает (ok, error_message, message_id)."""
    if not BOT_TOKEN:
        return False, "BOT_TOKEN не задан", None
    try:
        params = {"chat_id": chat_id, "text": text}
        if parse_mode:
            params["parse_mode"] = parse_mode
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
        if not resp.get("ok"):
            return False, resp.get("description", "Ошибка Telegram"), None
        result = resp.get("result") or {}
        msg_id = result.get("message_id") if isinstance(result, dict) else None
        return True, "", msg_id
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()
            err = json.loads(body)
            return False, err.get("description", str(e)), None
        except Exception:
            return False, str(e), None
    except Exception as e:
        return False, str(e), None


@app.route("/api/user/<user_id>/question-of-day/send-now", methods=["POST"])
@login_required
def api_question_of_day_send_now(user_id):
    """Отправить «вопрос дня» этому пользователю прямо сейчас. Учитывает выбор «в чат» / «в личку»."""
    try:
        try:
            uid = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        from user_stats import get_user, get_user_messages_for_today, get_chat_for_question_of_day, mark_question_of_day_asked
        from ai_analyzer import generate_question_of_day
        from html import escape

        data = request.get_json() or {}
        question = (data.get("question") or "").strip()
        chat_id_override = data.get("chat_id")

        u = get_user(uid)
        display_name = u.get("display_name") or str(uid)
        if not question:
            messages = get_user_messages_for_today(uid)
            if not messages:
                return jsonify({"ok": False, "error": "Нет ни одного сообщения для генерации вопроса"}), 400
            graph_ctx = ""
            if bot_settings.get("qod_graph_mode_enabled"):
                import social_graph
                graph_ctx = social_graph.get_user_graph_context(uid)
            question = generate_question_of_day(messages, display_name, graph_context=graph_ctx)
        dest = u.get("question_of_day_destination") or "chat"

        if dest == "chat":
            if chat_id_override is not None:
                try:
                    chat_id = int(chat_id_override)
                except (ValueError, TypeError):
                    return jsonify({"ok": False, "error": "Некорректный chat_id"}), 400
            else:
                chat_id = get_chat_for_question_of_day(uid)
            if chat_id is None:
                return jsonify({"ok": False, "error": "Нет сообщений в чатах за сегодня — выберите чат или добавьте сообщения"}), 400
            mention = f'<a href="tg://user?id={uid}">{escape(display_name)}</a>'
            text = f"{mention}, {question}"
            parse_mode = "HTML"
        else:
            chat_id = uid
            text = question
            parse_mode = None

        ok, err, msg_id = _send_telegram_message(chat_id, text, parse_mode)
        if ok:
            mark_question_of_day_asked(uid)
            if msg_id:
                import qod_tracking
                qod_tracking.add(chat_id, msg_id, uid, question)
            return jsonify({"ok": True, "message": "Отправлено."})
        return jsonify({"ok": False, "error": err or "Ошибка отправки"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/chat/topic-recommendation", methods=["POST"])
@login_required
def api_chat_topic_recommendation():
    """Ручная генерация рекомендаций темы/формата для чата и опциональная отправка."""
    try:
        if not bot_settings.get("chat_topic_recommender_enabled"):
            return jsonify({"ok": False, "error": "Рекомендатор тем выключен в настройках."}), 400
        data = request.get_json() or {}
        chat_id = data.get("chat_id")
        send_now = bool(data.get("send_now"))
        if chat_id is None:
            return jsonify({"ok": False, "error": "Нужен chat_id"}), 400
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Некорректный chat_id"}), 400
        from ai_analyzer import generate_topic_recommendation
        from user_stats import get_chats
        import social_graph
        ctx = social_graph.build_chat_digest(cid, period_days=2)
        chats_map = {int(c["chat_id"]): (c.get("title") or str(c["chat_id"])) for c in get_chats()}
        rec = generate_topic_recommendation(ctx, chats_map.get(cid, str(cid)))
        sent = False
        err = ""
        if send_now:
            ok, err, _ = _send_telegram_message(cid, rec)
            sent = ok
        return jsonify({"ok": True, "recommendation": rec, "sent": sent, "send_error": err})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/explainability/recent")
@login_required
def api_explainability_recent():
    """Последние объяснения действий бота (если функция включена)."""
    try:
        if not bot_settings.get("bot_explainability_enabled"):
            return jsonify({"ok": True, "events": []})
        import bot_explainability
        user_id = request.args.get("user_id")
        chat_id = request.args.get("chat_id")
        uid = int(user_id) if user_id and str(user_id).lstrip("-").isdigit() else None
        cid = int(chat_id) if chat_id and str(chat_id).lstrip("-").isdigit() else None
        events = bot_explainability.get_recent(limit=80, chat_id=cid, user_id=uid)
        return jsonify({"ok": True, "events": events})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/decisions/recent")
@login_required
def api_decisions_recent():
    """Recent moderation decisions made by DecisionEngine."""
    try:
        from services.decision_engine import get_recent_decisions

        limit_raw = request.args.get("limit", "80")
        try:
            limit = max(1, min(200, int(limit_raw)))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid limit"}), 400
        chat_id = request.args.get("chat_id")
        user_id = request.args.get("user_id")
        cid = int(chat_id) if chat_id and str(chat_id).lstrip("-").isdigit() else None
        uid = int(user_id) if user_id and str(user_id).lstrip("-").isdigit() else None
        events = _cached_json(
            "decisions_recent",
            15,
            lambda: get_recent_decisions(limit=limit, chat_id=cid, user_id=uid),
            limit=limit,
            chat_id=cid,
            user_id=uid,
        )
        return jsonify({"ok": True, "decisions": events})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/decisions/feedback", methods=["POST"])
@login_required
def api_decisions_feedback():
    """Store admin feedback for a DecisionEngine event."""
    try:
        from services.decision_engine import apply_decision_feedback

        payload = request.get_json(silent=True) or {}
        event_id = str(payload.get("event_id") or "").strip()
        feedback = str(payload.get("feedback") or "neutral").strip().lower()
        score_raw = payload.get("score")
        score = float(score_raw) if score_raw is not None else None
        note = str(payload.get("note") or "")
        reviewer = str(payload.get("reviewer") or "admin")
        if not event_id:
            return jsonify({"ok": False, "error": "event_id is required"}), 400
        if feedback not in {"approve", "reject", "neutral", "accepted", "rejected", "positive", "negative"}:
            return jsonify({"ok": False, "error": "invalid feedback"}), 400
        row = apply_decision_feedback(
            event_id=event_id,
            feedback=feedback,
            score=score,
            reviewer=reviewer,
            note=note,
        )
        if row is None:
            return jsonify({"ok": False, "error": "event not found"}), 404
        _API_CACHE.clear_prefix("decisions_recent")
        _API_CACHE.clear_prefix("decisions_quality")
        return jsonify({"ok": True, "decision": row})
    except ValueError:
        return jsonify({"ok": False, "error": "invalid score"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/decisions/quality")
@login_required
def api_decisions_quality():
    """Aggregated quality/approval dashboard for moderation decisions."""
    try:
        from services.decision_engine import get_decision_quality

        chat_id = request.args.get("chat_id")
        days_raw = request.args.get("days", "30")
        try:
            days = max(1, min(180, int(days_raw)))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid days"}), 400
        cid = int(chat_id) if chat_id and str(chat_id).lstrip("-").isdigit() else None
        payload = _cached_json(
            "decisions_quality",
            20,
            lambda: get_decision_quality(chat_id=cid, days=days),
            chat_id=cid,
            days=days,
        )
        return jsonify({"ok": True, "quality": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/clear-images-archive", methods=["POST"])
@login_required
def api_clear_images_archive():
    """Очищает архив проанализированных изображений пользователя. POST: {user_id}."""
    try:
        data = request.get_json() or {}
        user_id = data.get("user_id")
        if not user_id:
            return jsonify({"ok": False, "error": "Нужен user_id"}), 400
        try:
            user_id_int = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        from user_stats import clear_user_images_archive
        clear_user_images_archive(user_id_int)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/clear-archive", methods=["POST"])
@login_required
def api_clear_archive():
    """Очищает архив пользователя. POST: {user_id, chat_id?}. chat_id — только этот чат, без chat_id — весь архив."""
    try:
        data = request.get_json() or {}
        user_id = data.get("user_id")
        chat_id = data.get("chat_id")
        if not user_id:
            return jsonify({"ok": False, "error": "Нужен user_id"}), 400
        from user_stats import clear_user_archive
        try:
            user_id_int = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400
        chat_id_param = None if not chat_id or chat_id == "all" else (int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id)
        clear_user_archive(user_id_int, chat_id_param)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/chat/<path:chat_id>/graph")
@login_required
def api_chat_graph_compat(chat_id: str):
    """Compatibility graph endpoint for restored/new UI layers."""
    from services.graph_api import build_graph_payload

    cid = None if chat_id == "all" else (int(chat_id) if str(chat_id).lstrip("-").isdigit() else None)
    period = (request.args.get("period") or "7d").strip().lower()
    ego_user_raw = (request.args.get("ego_user") or "").strip()
    ego_user = int(ego_user_raw) if ego_user_raw.lstrip("-").isdigit() else None
    limit_raw = (request.args.get("limit") or "").strip()
    limit = int(limit_raw) if limit_raw.isdigit() else None
    payload = build_graph_payload(cid, period=period, ego_user=ego_user, limit=limit)
    version = _graph_build_version(payload)
    scope = _graph_snapshot_scope(cid, period, ego_user, limit)
    _graph_history_set(scope, version, payload, ttl_sec=360)
    return jsonify({"ok": True, "graph": payload, "graph_version": version})


def _parse_graph_chat_id(chat_id: str) -> int | None:
    return None if chat_id == "all" else (int(chat_id) if str(chat_id).lstrip("-").isdigit() else None)


def _safe_graph_lab_filters(raw: str) -> dict:
    if not raw:
        return {}
    if len(raw) > 12000:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _node_engagement_value(node: dict) -> float:
    candidates = (
        node.get("engagement"),
        node.get("engagement_score"),
        node.get("influence_score"),
        node.get("centrality"),
        node.get("degree"),
    )
    for c in candidates:
        try:
            v = float(c)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return 0.0


def _apply_graph_lab_filters(payload: dict, filters: dict) -> dict:
    base_nodes = [dict(x) for x in (payload.get("nodes") or [])]
    base_edges = [dict(x) for x in (payload.get("edges") or [])]
    node_ids = {int(n.get("id", 0) or 0) for n in base_nodes if int(n.get("id", 0) or 0) != 0}
    edges = []
    for e in base_edges:
        s = int(e.get("source", 0) or 0)
        t = int(e.get("target", 0) or 0)
        if s in node_ids and t in node_ids:
            edges.append({"source": s, "target": t, **e})

    degree: dict[int, int] = {}
    for e in edges:
        s = int(e.get("source", 0) or 0)
        t = int(e.get("target", 0) or 0)
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1

    min_degree = max(0, int(filters.get("min_degree", 0) or 0))
    nodes = base_nodes
    if min_degree > 0:
        keep = {nid for nid, deg in degree.items() if int(deg) >= min_degree}
        nodes = [n for n in nodes if int(n.get("id", 0) or 0) in keep]
        keep_ids = {int(n.get("id", 0) or 0) for n in nodes}
        edges = [e for e in edges if int(e.get("source", 0) or 0) in keep_ids and int(e.get("target", 0) or 0) in keep_ids]

    engagement_threshold = max(0.0, float(filters.get("engagement", 0.0) or 0.0))
    if engagement_threshold > 0:
        nodes = [n for n in nodes if _node_engagement_value(n) >= engagement_threshold]
        keep_ids = {int(n.get("id", 0) or 0) for n in nodes}
        edges = [e for e in edges if int(e.get("source", 0) or 0) in keep_ids and int(e.get("target", 0) or 0) in keep_ids]

    focus_raw = str(filters.get("focus_user") or "").strip()
    focus_user = int(focus_raw) if focus_raw.lstrip("-").isdigit() else None
    ego_network = bool(filters.get("ego_network"))
    if focus_user is not None:
        if ego_network:
            keep = {int(focus_user)}
            for e in edges:
                s = int(e.get("source", 0) or 0)
                t = int(e.get("target", 0) or 0)
                if s == int(focus_user):
                    keep.add(t)
                elif t == int(focus_user):
                    keep.add(s)
        else:
            keep = {int(focus_user)}
        nodes = [n for n in nodes if int(n.get("id", 0) or 0) in keep]
        keep_ids = {int(n.get("id", 0) or 0) for n in nodes}
        edges = [e for e in edges if int(e.get("source", 0) or 0) in keep_ids and int(e.get("target", 0) or 0) in keep_ids]

    degree = {}
    for e in edges:
        s = int(e.get("source", 0) or 0)
        t = int(e.get("target", 0) or 0)
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1
    n_count = max(1, len(nodes) - 1)
    centrality = {nid: round(float(degree.get(nid, 0)) / float(n_count), 6) for nid in {int(n.get("id", 0) or 0) for n in nodes}}

    node_by_id = {int(n.get("id", 0) or 0): n for n in nodes}
    community_by_id = {nid: node_by_id.get(nid, {}).get("community_id") for nid in node_by_id.keys()}
    neigh_communities: dict[int, set] = {nid: set() for nid in node_by_id.keys()}
    for e in edges:
        s = int(e.get("source", 0) or 0)
        t = int(e.get("target", 0) or 0)
        sc = community_by_id.get(s)
        tc = community_by_id.get(t)
        if tc is not None:
            neigh_communities.setdefault(s, set()).add(tc)
        if sc is not None:
            neigh_communities.setdefault(t, set()).add(sc)
    bridge_nodes = {nid for nid, groups in neigh_communities.items() if len(groups) >= 2}

    influencers_enabled = bool(filters.get("show_influencers"))
    bridge_enabled = bool(filters.get("show_bridges"))
    centrality_enabled = bool(filters.get("show_centrality"))
    outliers_enabled = bool(filters.get("show_outliers"))
    influencer_candidates = []
    if influencers_enabled:
        for n in nodes:
            nid = int(n.get("id", 0) or 0)
            score = max(_node_engagement_value(n), float(centrality.get(nid, 0.0)))
            influencer_candidates.append((nid, score))
        influencer_candidates.sort(key=lambda x: x[1], reverse=True)
    influencers = {nid for nid, _score in influencer_candidates[: max(1, int(len(influencer_candidates) * 0.1) or 3)]}

    for n in nodes:
        nid = int(n.get("id", 0) or 0)
        if centrality_enabled:
            c = float(centrality.get(nid, 0.0))
            n["_centrality"] = c
            n["_node_size"] = round(10 + c * 50, 2)
        if bridge_enabled and nid in bridge_nodes:
            n["_is_bridge"] = True
            n["_color"] = "#FF6B9D"
        if influencers_enabled and nid in influencers:
            n["_is_influencer"] = True
            if "_color" not in n:
                n["_color"] = "#00D4FF"
        if outliers_enabled and int(degree.get(nid, 0) or 0) <= 1:
            n["_is_outlier"] = True
            if "_color" not in n:
                n["_color"] = "#F59E0B"

    meta = dict(payload.get("meta") or {})
    meta.update(
        {
            "source": "graph-lab",
            "nodes_count": len(nodes),
            "edges_count": len(edges),
            "filters": filters,
        }
    )
    return {"nodes": nodes, "edges": edges, "meta": meta}


@app.route("/api/chat/<path:chat_id>/graph-lab")
@login_required
def api_chat_graph_lab(chat_id: str):
    from services.graph_api import build_graph_payload

    cid = _parse_graph_chat_id(chat_id)
    raw_filters = (request.args.get("filters") or "{}").strip()
    filters = _safe_graph_lab_filters(raw_filters)
    try:
        query_period = (request.args.get("period") or "7d").strip().lower()
        activity_days = max(1, int(filters.get("activity_days", 7) or 7))
    except (TypeError, ValueError):
        query_period = "7d"
        activity_days = 7
    if activity_days <= 1:
        period = "24h"
    elif activity_days <= 7:
        period = "7d"
    elif activity_days <= 30:
        period = "30d"
    else:
        period = query_period if query_period in {"24h", "7d", "30d", "all"} else "all"
    focus_raw = str(filters.get("focus_user") or "").strip()
    focus_user = int(focus_raw) if focus_raw.lstrip("-").isdigit() else None
    ego_network = bool(filters.get("ego_network"))
    ego = focus_user if (focus_user is not None and ego_network) else None
    limit_raw = (request.args.get("limit") or "").strip()
    limit = int(limit_raw) if limit_raw.isdigit() else None

    payload = build_graph_payload(cid, period=period, ego_user=ego, limit=limit)
    filtered = _apply_graph_lab_filters(payload, filters)
    return jsonify({"ok": True, "graph": filtered})


@app.route("/api/chat/<path:chat_id>/conflict-prediction")
@login_required
def api_chat_conflict_prediction(chat_id: str):
    import social_graph
    from user_stats import get_user_display_names

    cid = _parse_graph_chat_id(chat_id)
    try:
        threshold = max(0.0, min(1.0, float(request.args.get("threshold", "0.5") or 0.5)))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid threshold"}), 400
    try:
        days = max(1, min(90, int(request.args.get("days", "30") or 30)))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid days"}), 400
    try:
        limit = max(1, min(200, int(request.args.get("limit", "50") or 50)))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid limit"}), 400

    base = social_graph.get_conflict_forecast(cid, limit=120)
    names = get_user_display_names()
    risks = []
    for row in base:
        risk = float(row.get("risk", 0.0) or 0.0)
        c24 = int(row.get("message_count_24h", 0) or 0)
        if days <= 1 and c24 <= 0:
            continue
        if risk < threshold:
            continue
        ua = int(row.get("user_a", 0) or 0)
        ub = int(row.get("user_b", 0) or 0)
        risks.append(
            {
                "chat_id": row.get("chat_id"),
                "user1_id": ua,
                "user2_id": ub,
                "user1": names.get(str(ua), str(ua)),
                "user2": names.get(str(ub), str(ub)),
                "risk_score": round(risk, 4),
                "tone": str(row.get("tone", "neutral") or "neutral"),
                "trend_delta": float(row.get("trend_delta", 0.0) or 0.0),
                "message_count_24h": c24,
                "topics": list(row.get("topics") or []),
            }
        )
    risks.sort(key=lambda x: float(x.get("risk_score", 0.0)), reverse=True)
    total = len(risks)
    return jsonify(
        {
            "ok": True,
            "risks": risks[:limit],
            "threshold": threshold,
            "count": total,
            "returned": min(total, limit),
            "limit": limit,
            "truncated": bool(total > limit),
        }
    )


@app.route("/api/chat/<path:chat_id>/graph-delta")
@login_required
def api_chat_graph_delta(chat_id: str):
    """Delta patch endpoint for graph updates without full payload reload."""
    from services.graph_api import build_graph_payload

    cid = None if chat_id == "all" else (int(chat_id) if str(chat_id).lstrip("-").isdigit() else None)
    period = (request.args.get("period") or "7d").strip().lower()
    ego_user_raw = (request.args.get("ego_user") or "").strip()
    ego_user = int(ego_user_raw) if ego_user_raw.lstrip("-").isdigit() else None
    limit_raw = (request.args.get("limit") or "").strip()
    limit = int(limit_raw) if limit_raw.isdigit() else None
    since_version = (request.args.get("since") or "").strip()

    current = build_graph_payload(cid, period=period, ego_user=ego_user, limit=limit)
    current_version = _graph_build_version(current)
    scope = _graph_snapshot_scope(cid, period, ego_user, limit)
    history = _graph_history_get(scope)
    latest = history.get("latest") if isinstance(history, dict) else None
    prev = history.get("prev") if isinstance(history, dict) else None

    prev_graph = None
    if isinstance(latest, dict) and str(latest.get("version") or "") == since_version:
        prev_graph = latest.get("graph") if isinstance(latest.get("graph"), dict) else None
    elif isinstance(prev, dict) and str(prev.get("version") or "") == since_version:
        prev_graph = prev.get("graph") if isinstance(prev.get("graph"), dict) else None
    elif isinstance(latest, dict):
        prev_graph = latest.get("graph") if isinstance(latest.get("graph"), dict) else None

    if since_version and since_version == current_version:
        _graph_history_set(scope, current_version, current, ttl_sec=360)
        return jsonify({"ok": True, "changed": False, "graph_version": current_version, "delta": {"full_replace": False, "remove_node_ids": [], "upsert_nodes": [], "remove_edge_ids": [], "upsert_edges": [], "meta": current.get("meta") or {}}})

    delta = _graph_delta(prev_graph, current)
    _graph_history_set(scope, current_version, current, ttl_sec=360)
    return jsonify({"ok": True, "changed": bool(delta.get("changed")), "graph_version": current_version, "delta": delta.get("delta") or {}})


@app.route("/api/chat/<path:chat_id>/community-health")
@login_required
def api_chat_community_health_compat(chat_id: str):
    from services.community_health import build_community_health

    cid = None if chat_id == "all" else (int(chat_id) if str(chat_id).lstrip("-").isdigit() else None)
    return jsonify({"ok": True, "health": build_community_health(cid)})


@app.route("/api/chat/<path:chat_id>/moderation-risk")
@login_required
def api_chat_moderation_risk_compat(chat_id: str):
    from services.moderation_risk import build_moderation_risk

    cid = None if chat_id == "all" else (int(chat_id) if str(chat_id).lstrip("-").isdigit() else None)
    return jsonify({"ok": True, "risk": build_moderation_risk(cid)})


@app.route("/api/metrics/user/<path:user_id>")
@login_required
def api_metrics_user(user_id: str):
    """Legacy proxy -> /api/v2/metrics/user/<user_id>."""
    path = f"/api/v2/metrics/user/{user_id}"
    if request.query_string:
        path += "?" + request.query_string.decode("utf-8")
    body, status = proxy_to_fastapi(path)
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/api/metrics/chat/<path:chat_id>/health")
@login_required
def api_metrics_chat_health(chat_id: str):
    """Legacy proxy -> /api/v2/metrics/chat/<chat_id>/health."""
    path = f"/api/v2/metrics/chat/{chat_id}/health"
    if request.query_string:
        path += "?" + request.query_string.decode("utf-8")
    body, status = proxy_to_fastapi(path)
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/api/leaderboard")
@login_required
def api_leaderboard():
    from services.marketing_metrics import get_leaderboard

    metric = (request.args.get("metric") or "engagement").strip().lower()
    chat_raw = (request.args.get("chat_id") or "all").strip().lower()
    chat_id = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and chat_id is None:
        return jsonify({"ok": False, "error": "invalid chat_id"}), 400
    try:
        days = max(1, min(90, int(request.args.get("days", "30"))))
        limit = max(1, min(100, int(request.args.get("limit", "10"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid pagination params"}), 400
    rows = _cached_json(
        "metrics_leaderboard",
        30,
        lambda: get_leaderboard(metric=metric, chat_id=chat_id, days=days, limit=limit),
        metric=metric,
        chat_id=chat_id,
        days=days,
        limit=limit,
    )
    return jsonify({"ok": True, "metric": metric, "rows": rows})


@app.route("/api/recommendations")
@login_required
def api_recommendations():
    from services.recommendations import build_recommendations

    chat_raw = (request.args.get("chat_id") or "all").strip().lower()
    chat_id = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and chat_id is None:
        return jsonify({"ok": False, "error": "invalid chat_id"}), 400
    try:
        days = max(1, min(90, int(request.args.get("days", "30"))))
        limit = max(1, min(100, int(request.args.get("limit", "20"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid params"}), 400
    payload = _cached_json(
        "recommendations",
        30,
        lambda: build_recommendations(chat_id, days=days, limit=limit),
        chat_id=chat_id,
        days=days,
        limit=limit,
    )
    return jsonify({"ok": True, "recommendations": payload})


@app.route("/api/recommendations/mark-done", methods=["POST"])
@login_required
def api_recommendations_mark_done():
    try:
        payload = request.get_json(silent=True) or {}
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        chat_raw = str(payload.get("chat_id", "all") or "all").strip().lower()
        completed = bool(payload.get("completed", True))
        write_event(
            "recommendation_marked_done",
            severity="info",
            source="flask_admin",
            payload={
                "chat_id": chat_raw,
                "completed": completed,
                "type": str(item.get("type") or ""),
                "priority": str(item.get("priority") or ""),
                "user_id": int(item.get("user_id", 0) or 0) if str(item.get("user_id", "")).lstrip("-").isdigit() else None,
                "reason": str(item.get("reason") or "")[:240],
                "action": str(item.get("action") or "")[:240],
            },
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/recommendations")
@login_required
def admin_recommendations():
    """Alias endpoint for recommendations in admin namespace."""
    return api_recommendations()


@app.route("/api/predictive/overview")
@login_required
def api_predictive_overview():
    from services.predictive_models import predict_overview

    chat_raw = (request.args.get("chat_id") or "all").strip().lower()
    chat_id = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and chat_id is None:
        return jsonify({"ok": False, "error": "invalid chat_id"}), 400
    try:
        horizon_days = max(1, min(30, int(request.args.get("horizon_days", "7"))))
        lookback_days = max(7, min(180, int(request.args.get("lookback_days", "30"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid params"}), 400
    payload = _cached_json(
        "predictive_overview",
        20,
        lambda: predict_overview(chat_id, horizon_days=horizon_days, lookback_days=lookback_days),
        chat_id=chat_id,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
    )
    return jsonify({"ok": True, "overview": payload})


@app.route("/api/learning/summary")
@login_required
def api_learning_summary():
    from services.learning_loop import feedback_summary

    chat_raw = (request.args.get("chat_id") or "all").strip().lower()
    chat_id = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and chat_id is None:
        return jsonify({"ok": False, "error": "invalid chat_id"}), 400
    try:
        days = max(1, min(180, int(request.args.get("days", "30"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid days"}), 400
    payload = _cached_json(
        "learning_summary",
        20,
        lambda: feedback_summary(chat_id=chat_id, days=days),
        chat_id=chat_id,
        days=days,
    )
    return jsonify({"ok": True, "summary": payload})


@app.route("/api/retention-dashboard")
@login_required
def api_retention_dashboard():
    from services.recommendations import build_retention_dashboard

    chat_raw = (request.args.get("chat_id") or "all").strip().lower()
    chat_id = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and chat_id is None:
        return jsonify({"ok": False, "error": "invalid chat_id"}), 400
    try:
        days = max(1, min(90, int(request.args.get("days", "30"))))
        limit = max(1, min(500, int(request.args.get("limit", "50"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid params"}), 400
    payload = _cached_json(
        "retention_dashboard",
        30,
        lambda: build_retention_dashboard(chat_id, days=days, limit=limit),
        chat_id=chat_id,
        days=days,
        limit=limit,
    )
    return jsonify({"ok": True, "dashboard": payload})


@app.route("/admin/retention-dashboard")
@login_required
def admin_retention_dashboard():
    """Alias endpoint for retention dashboard in admin namespace."""
    return api_retention_dashboard()


@app.route("/api/admin/dashboard")
@login_required
def api_admin_dashboard():
    from services.admin_dashboards import build_chat_health_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        days = max(1, min(180, int(request.args.get("days", "30"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid days"}), 400
    payload = _cached_json(
        "admin_dashboard_health",
        20,
        lambda: build_chat_health_dashboard(chat_id, days=days),
        chat_id="all" if chat_id is None else int(chat_id),
        days=days,
    )
    return jsonify({"ok": True, "dashboard": payload})


@app.route("/api/admin/community-structure")
@login_required
def api_admin_community_structure():
    from services.admin_dashboards import build_community_structure_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    period = (request.args.get("period") or "30d").strip().lower()
    try:
        limit = max(200, min(5000, int(request.args.get("limit", "1200"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid limit"}), 400
    payload = _cached_json(
        "admin_dashboard_community",
        20,
        lambda: build_community_structure_dashboard(chat_id, period=period, limit=limit),
        chat_id="all" if chat_id is None else int(chat_id),
        period=period,
        limit=limit,
    )
    return jsonify({"ok": True, "community": payload})


@app.route("/api/admin/leaderboard")
@login_required
def api_admin_leaderboard():
    from services.admin_dashboards import build_user_leaderboard_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    metric = (request.args.get("metric") or "engagement").strip().lower()
    try:
        days = max(1, min(180, int(request.args.get("days", "30"))))
        limit = max(1, min(100, int(request.args.get("limit", "10"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid params"}), 400
    payload = _cached_json(
        "admin_dashboard_leaderboard",
        20,
        lambda: build_user_leaderboard_dashboard(chat_id, metric=metric, limit=limit, days=days),
        chat_id="all" if chat_id is None else int(chat_id),
        metric=metric,
        days=days,
        limit=limit,
    )
    return jsonify({"ok": True, "leaderboard": payload})


@app.route("/api/admin/at-risk-users")
@login_required
def api_admin_at_risk_users():
    from services.admin_dashboards import build_at_risk_users_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        days = max(1, min(180, int(request.args.get("days", "30"))))
        limit = max(1, min(200, int(request.args.get("limit", "30"))))
        threshold = max(0.0, min(1.0, float(request.args.get("threshold", "0.6"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid params"}), 400
    payload = _cached_json(
        "admin_dashboard_at_risk",
        20,
        lambda: build_at_risk_users_dashboard(chat_id, threshold=threshold, days=days, limit=limit),
        chat_id="all" if chat_id is None else int(chat_id),
        threshold=threshold,
        days=days,
        limit=limit,
    )
    return jsonify({"ok": True, "at_risk": payload})


@app.route("/api/admin/at-risk-action", methods=["POST"])
@login_required
def api_admin_at_risk_action():
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    chat_id = str(payload.get("chat_id") or "all").strip().lower()
    user_id_raw = str(payload.get("user_id") or "").strip()
    if action not in {"dm", "clear_flag"}:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    if not user_id_raw.lstrip("-").isdigit():
        return jsonify({"ok": False, "error": "invalid user_id"}), 400
    user_id = int(user_id_raw)
    write_event(
        "admin_at_risk_action",
        severity="info",
        source="flask_admin",
        payload={
            "action": action,
            "chat_id": chat_id,
            "user_id": user_id,
        },
    )
    return jsonify({"ok": True, "action": action, "user_id": user_id, "chat_id": chat_id, "queued": action == "dm"})


@app.route("/api/admin/decision-quality")
@login_required
def api_admin_decision_quality():
    from services.admin_dashboards import build_decision_quality_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        period_days = max(1, min(180, int(request.args.get("period_days", "7"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid period_days"}), 400
    payload = _cached_json(
        "admin_dashboard_decision_quality",
        20,
        lambda: build_decision_quality_dashboard(chat_id, period_days=period_days),
        chat_id="all" if chat_id is None else int(chat_id),
        period_days=period_days,
    )
    return jsonify({"ok": True, "quality": payload})


@app.route("/api/admin/content-analysis")
@login_required
def api_admin_content_analysis():
    from services.admin_dashboards import build_content_analysis_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        period_days = max(1, min(180, int(request.args.get("period_days", "30"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid period_days"}), 400
    payload = _cached_json(
        "admin_dashboard_content",
        20,
        lambda: build_content_analysis_dashboard(chat_id, period_days=period_days),
        chat_id="all" if chat_id is None else int(chat_id),
        period_days=period_days,
    )
    return jsonify({"ok": True, "analysis": payload})


@app.route("/api/admin/moderation-activity")
@login_required
def api_admin_moderation_activity():
    from services.admin_dashboards import build_moderation_activity_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        period_days = max(1, min(90, int(request.args.get("period_days", "7"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid period_days"}), 400
    payload = _cached_json(
        "admin_dashboard_moderation",
        20,
        lambda: build_moderation_activity_dashboard(chat_id, period_days=period_days),
        chat_id="all" if chat_id is None else int(chat_id),
        period_days=period_days,
    )
    return jsonify({"ok": True, "activity": payload})


@app.route("/api/admin/trends")
@login_required
def api_admin_trends():
    from services.admin_dashboards import build_growth_trends_dashboard

    chat_id, err = _parse_chat_id_arg("chat_id")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        lookback_days = max(7, min(180, int(request.args.get("lookback_days", "30"))))
        horizon_days = max(1, min(30, int(request.args.get("horizon_days", "7"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid params"}), 400
    payload = _cached_json(
        "admin_dashboard_trends",
        20,
        lambda: build_growth_trends_dashboard(chat_id, lookback_days=lookback_days, horizon_days=horizon_days),
        chat_id="all" if chat_id is None else int(chat_id),
        lookback_days=lookback_days,
        horizon_days=horizon_days,
    )
    return jsonify({"ok": True, "trends": payload})


@app.route("/api/churn/snapshots")
@login_required
def api_churn_snapshots():
    from services.recommendations import get_recent_churn_snapshots

    chat_raw = (request.args.get("chat_id") or "all").strip().lower()
    chat_id = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and chat_id is None:
        return jsonify({"ok": False, "error": "invalid chat_id"}), 400
    try:
        limit = max(1, min(100, int(request.args.get("limit", "10"))))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid limit"}), 400
    rows = _cached_json(
        "churn_snapshots",
        20,
        lambda: get_recent_churn_snapshots(limit=limit, chat_id=chat_id),
        limit=limit,
        chat_id=chat_id,
    )
    return jsonify({"ok": True, "snapshots": rows})


@app.route("/api/churn/run", methods=["POST"])
@login_required
def api_churn_run():
    from services.recommendations import run_churn_detection

    payload = request.get_json(silent=True) or {}
    chat_raw = str(payload.get("chat_id", "all")).strip().lower()
    chat_id = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and chat_id is None:
        return jsonify({"ok": False, "error": "invalid chat_id"}), 400
    try:
        days = max(1, min(90, int(payload.get("days", 30))))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "invalid days"}), 400
    snapshot = run_churn_detection(chat_id, days=days, limit=300)
    _API_CACHE.clear_prefix("churn_snapshots")
    _API_CACHE.clear_prefix("recommendations")
    _API_CACHE.clear_prefix("retention_dashboard")
    return jsonify({"ok": True, "snapshot": snapshot})


@app.route("/api/storage/status")
@login_required
def api_storage_status():
    from services.data_platform import export_snapshot

    payload = _cached_json("storage_status", 15, export_snapshot, scope="global")
    return jsonify(payload)


@app.route("/api/storage/cutover-report")
@login_required
def api_storage_cutover_report():
    from services.storage_cutover import build_cutover_report

    payload = _cached_json("storage_cutover_report", 15, build_cutover_report, scope="global")
    return jsonify(payload)


@app.route("/api/storage/cutover", methods=["POST"])
@login_required
def api_storage_cutover():
    from services.storage_cutover import apply_cutover

    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "").strip().lower()
    force = bool(payload.get("force", False))
    reason = str(payload.get("reason") or "manual").strip()
    allowed_modes = {"json", "hybrid", "db", "dual", "db_first", "db_only"}
    if mode not in allowed_modes:
        return jsonify({"ok": False, "error": "mode must be one of: json, hybrid, db, dual, db_first, db_only"}), 400
    result = apply_cutover(mode, force=force, reason=reason)
    _API_CACHE.clear_prefix("storage_status")
    _API_CACHE.clear_prefix("storage_cutover_report")
    status = 200 if result.get("ok") else 409
    return jsonify(result), status


@app.route("/api/me/graph")
def api_me_graph_compat():
    from services.graph_api import build_graph_payload

    token = (request.args.get("token") or "").strip()
    token_user_id, token_err = _participant_verify(token) if token else (None, None)
    if token and (token_err or not token_user_id):
        return jsonify({"ok": False, "error": token_err or "Неверная ссылка"}), 403
    ego_raw = (request.args.get("user_id") or "").strip()
    ego = int(ego_raw) if ego_raw.isdigit() else None
    if token_user_id is not None:
        ego = int(token_user_id)
    payload = build_graph_payload(None, period="7d", ego_user=ego)
    version = _graph_build_version(payload)
    scope = _graph_snapshot_scope(None, "7d", ego, None)
    _graph_history_set(scope, version, payload, ttl_sec=360)
    return jsonify({"ok": True, "graph": payload, "graph_version": version})


@app.route("/api/me/graph-delta")
def api_me_graph_delta():
    from services.graph_api import build_graph_payload

    token = (request.args.get("token") or "").strip()
    user_id, err = _participant_verify(token)
    if err or not user_id:
        return jsonify({"ok": False, "error": err or "Неверная ссылка"}), 403
    since_version = (request.args.get("since") or "").strip()

    ego = int(user_id)
    current = build_graph_payload(None, period="7d", ego_user=ego)
    current_version = _graph_build_version(current)
    scope = _graph_snapshot_scope(None, "7d", ego, None)
    history = _graph_history_get(scope)
    latest = history.get("latest") if isinstance(history, dict) else None
    prev = history.get("prev") if isinstance(history, dict) else None

    prev_graph = None
    if isinstance(latest, dict) and str(latest.get("version") or "") == since_version:
        prev_graph = latest.get("graph") if isinstance(latest.get("graph"), dict) else None
    elif isinstance(prev, dict) and str(prev.get("version") or "") == since_version:
        prev_graph = prev.get("graph") if isinstance(prev.get("graph"), dict) else None
    elif isinstance(latest, dict):
        prev_graph = latest.get("graph") if isinstance(latest.get("graph"), dict) else None

    if since_version and since_version == current_version:
        _graph_history_set(scope, current_version, current, ttl_sec=360)
        return jsonify({
            "ok": True,
            "changed": False,
            "graph_version": current_version,
            "delta": {
                "full_replace": False,
                "remove_node_ids": [],
                "upsert_nodes": [],
                "remove_edge_ids": [],
                "upsert_edges": [],
                "meta": current.get("meta") or {},
            },
        })

    delta = _graph_delta(prev_graph, current)
    _graph_history_set(scope, current_version, current, ttl_sec=360)
    return jsonify({"ok": True, "changed": bool(delta.get("changed")), "graph_version": current_version, "delta": delta.get("delta") or {}})


@app.route("/api/me/graph-version")
def api_me_graph_version():
    token = (request.args.get("token") or "").strip()
    user_id, err = _participant_verify(token)
    if err or not user_id:
        return jsonify({"ok": False, "error": err or "Неверная ссылка"}), 403
    import social_graph

    return jsonify({"ok": True, "version": f"{social_graph.get_graph_version()}|u{int(user_id)}"})


@app.route("/api/log-tail")
@login_required
def api_log_tail():
    """Legacy proxy -> /api/v2/admin/log-tail."""
    path = "/api/v2/admin/log-tail"
    if request.query_string:
        path += "?" + request.query_string.decode("utf-8")
    body, status = proxy_to_fastapi(path)
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/api/prompts", methods=["GET", "POST", "DELETE"])
@login_required
def api_prompts():
    """Legacy proxy -> /api/v2/admin/prompts."""
    path = "/api/v2/admin/prompts"
    body, status = proxy_to_fastapi(path, method=request.method, data=request.get_data() if request.method != "GET" else None)
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


@app.route("/api/topic-policies", methods=["GET", "POST", "DELETE"])
@login_required
def api_topic_policies():
    """Legacy proxy -> /api/v2/admin/topic-policies."""
    path = "/api/v2/admin/topic-policies"
    body, status = proxy_to_fastapi(path, method=request.method, data=request.get_data() if request.method != "GET" else None)
    return (jsonify(body), status) if isinstance(body, dict) else (Response(body, status=status, mimetype="application/json"), status)


if __name__ == "__main__":
    host = os.getenv("ADMIN_HOST", "127.0.0.1")
    port = int(os.getenv("ADMIN_PORT", "5000"))
    print(f"Админ-панель: http://{host}:{port}")
    if not _get_admin_password():
        print("Внимание: пароль админа не задан — при первом заходе на /login задайте его")
    try:
        import torch
        import diffusers
        print("Локальная генерация портретов: torch, diffusers — OK")
    except ImportError as e:
        print("Локальная генерация портретов: недоступна —", e)
        print("  Установите: python -m pip install torch diffusers transformers accelerate")
    app.run(host=host, port=port, debug=False)


# === Inline HTML templates removed — now in templates/ ===
