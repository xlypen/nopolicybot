"""
Единый прокси-хелпер для Flask → FastAPI.
Сессия должна быть проверена до вызова (через @login_required).
"""
import json
import os
import urllib.error
import urllib.request

from flask import Response, request


def _get_fastapi_base() -> str:
    port = int(os.getenv("API_PORT", "8001"))
    return f"http://127.0.0.1:{port}"


def proxy_to_fastapi(
    path: str,
    method: str | None = None,
    data: bytes | None = None,
) -> tuple[dict | bytes, int]:
    """
    Проксировать запрос на FastAPI с внутренним Bearer токеном.
    Сессия уже проверена до вызова этой функции (@login_required).

    Returns:
        (body, status_code) — body может быть dict или bytes
    """
    token = str(os.getenv("ADMIN_TOKEN", "")).strip()
    if not token:
        return {"ok": False, "error": "ADMIN_TOKEN not configured"}, 503

    base = _get_fastapi_base()
    url = f"{base}{path}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8")

    method = method or request.method
    data = data if data is not None else (request.get_data() if request.method in ("POST", "PUT", "PATCH") else None)

    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", request.content_type or "application/json")

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read()
            try:
                return json.loads(body.decode("utf-8")), resp.status
            except json.JSONDecodeError:
                return body, resp.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "error": str(e)}
        return body, e.code
    except OSError as e:
        return {"ok": False, "error": str(e)}, 502


def proxy_response(body: dict | bytes, status: int) -> Response:
    """Преобразовать (body, status) в Flask Response."""
    if isinstance(body, dict):
        from flask import jsonify

        return jsonify(body), status
    return Response(body, status=status, mimetype="application/json")
