"""
Админ-панель для мониторинга бота: статистика пользователей, портреты, ранги.

Запуск: python admin_app.py
По умолчанию: http://127.0.0.1:5000
Пароль: ADMIN_PASSWORD в .env (если не задан — без авторизации, только localhost)
"""

import json
import os
import urllib.request
import urllib.error
from functools import wraps
from pathlib import Path

from flask import Flask, redirect, Response, render_template_string, request, session, url_for
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", encoding="utf-8-sig")

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_SECRET_KEY", "change-me-in-production")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
USERS_JSON = Path(__file__).resolve().parent / "user_stats.json"
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

RANK_LABELS = {"loyal": "🇷🇺 Лояльный", "neutral": "⚪ Нейтральный", "opposition": "🔴 Оппозиция", "unknown": "❓ Неизвестно"}

# Кэш путей к аватаркам (user_id -> file_path)
_avatar_cache: dict[str, str] = {}


def _load_users() -> dict:
    if not USERS_JSON.exists():
        return {"users": {}}
    try:
        data = json.loads(USERS_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "users" in data else {"users": {}}
    except Exception:
        return {"users": {}}


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return f(*args, **kwargs)
        if not session.get("admin_logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if not ADMIN_PASSWORD:
        return redirect(url_for("index"))
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("index"))
    return render_template_string(LOGIN_HTML)


@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    data = _load_users()
    users = data.get("users", {})
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
    return render_template_string(
        INDEX_HTML,
        total=total,
        ranks=ranks,
        total_pol=total_pol,
        total_warn=total_warn,
        users=sorted_users,
        rank_labels=RANK_LABELS,
    )


@app.route("/user/<user_id>")
@login_required
def user_detail(user_id):
    data = _load_users()
    u = data.get("users", {}).get(user_id)
    if not u:
        return "Пользователь не найден", 404
    return render_template_string(USER_DETAIL_HTML, user_id=user_id, u=u, rank_labels=RANK_LABELS)


def _get_avatar_file_path(user_id: str) -> str | None:
    """Получает file_path аватарки пользователя через Telegram API. Кэширует результат."""
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
        file_id = photos[0][0].get("file_id")  # smallest size
        if not file_id:
            return None
        url2 = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        with urllib.request.urlopen(url2, timeout=5) as r2:
            data2 = json.loads(r2.read().decode())
        file_path = data2.get("result", {}).get("file_path")
        if file_path:
            _avatar_cache[user_id] = file_path
        return file_path
    except Exception:
        return None


@app.route("/avatar/<user_id>")
def avatar(user_id):
    """Проксирует аватарку пользователя из Telegram."""
    file_path = _get_avatar_file_path(user_id)
    if not file_path or not BOT_TOKEN:
        return Response(status=404)
    try:
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = r.read()
        return Response(data, mimetype="image/jpeg")
    except Exception:
        return Response(status=404)


LOGIN_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Вход — Админ-панель</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 2rem; background: #1a1a2e; color: #eee; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .card { background: #16213e; padding: 2rem; border-radius: 12px; max-width: 360px; width: 100%; }
        h1 { margin: 0 0 1.5rem; font-size: 1.25rem; }
        input[type="password"] { width: 100%; padding: 0.75rem; border: 1px solid #0f3460; border-radius: 8px; background: #0f3460; color: #eee; font-size: 1rem; margin-bottom: 1rem; }
        button { width: 100%; padding: 0.75rem; background: #e94560; border: none; border-radius: 8px; color: #fff; font-size: 1rem; cursor: pointer; }
        button:hover { background: #ff6b6b; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Вход в админ-панель</h1>
        <form method="post">
            <input type="password" name="password" placeholder="Пароль" required autofocus>
            <button type="submit">Войти</button>
        </form>
    </div>
</body>
</html>
"""

INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Админ-панель — Политмонитор</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 1.5rem; background: #1a1a2e; color: #eee; }
        a { color: #e94560; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem; }
        h1 { margin: 0; font-size: 1.5rem; }
        .logout { color: #888; font-size: 0.9rem; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
        .card { background: #16213e; padding: 1rem; border-radius: 10px; }
        .card h3 { margin: 0 0 0.5rem; font-size: 0.85rem; color: #888; font-weight: 500; }
        .card .val { font-size: 1.5rem; font-weight: 600; }
        table { width: 100%; border-collapse: collapse; background: #16213e; border-radius: 10px; overflow: hidden; }
        th, td { padding: 0.75rem 1rem; text-align: left; }
        th { background: #0f3460; font-weight: 500; font-size: 0.85rem; }
        tr:hover { background: #1a2a4a; }
        .rank { font-size: 1.1rem; }
        .portrait { font-size: 0.85rem; color: #aaa; max-width: 400px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .user-cell { display: flex; align-items: center; gap: 0.5rem; }
        .avatar { width: 32px; height: 32px; border-radius: 50%; object-fit: cover; background: #0f3460; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Админ-панель — Политмонитор</h1>
        {% if session.get('admin_logged_in') %}<a href="{{ url_for('logout') }}" class="logout">Выйти</a>{% endif %}
    </div>
    <div class="cards">
        <div class="card"><h3>Пользователей</h3><span class="val">{{ total }}</span></div>
        <div class="card"><h3>Полит. сообщений</h3><span class="val">{{ total_pol }}</span></div>
        <div class="card"><h3>Замечаний выдано</h3><span class="val">{{ total_warn }}</span></div>
        {% for r, cnt in ranks.items() %}<div class="card"><h3>{{ rank_labels.get(r, r) }}</h3><span class="val">{{ cnt }}</span></div>{% endfor %}
    </div>
    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>Имя</th>
                <th>Ранг</th>
                <th>Сообщ.</th>
                <th>Полит.</th>
                <th>+ / − / 0</th>
                <th>Замечаний</th>
                <th>Портрет</th>
            </tr>
        </thead>
        <tbody>
            {% for uid, u in users %}
            <tr>
                <td><a href="{{ url_for('user_detail', user_id=uid) }}">{{ uid }}</a></td>
                <td>
                    <div class="user-cell">
                        <img src="{{ url_for('avatar', user_id=uid) }}" alt="" class="avatar" onerror="this.style.display='none'">
                        <span>{{ u.get('display_name', uid) }}</span>
                    </div>
                </td>
                <td class="rank">{{ rank_labels.get(u.get('rank'), u.get('rank')) }}</td>
                <td>{{ u.get('stats', {}).get('total_messages', 0) }}</td>
                <td>{{ u.get('stats', {}).get('political_messages', 0) }}</td>
                <td>{{ u.get('stats', {}).get('positive_sentiment', 0) }} / {{ u.get('stats', {}).get('negative_sentiment', 0) }} / {{ u.get('stats', {}).get('neutral_sentiment', 0) }}</td>
                <td>{{ u.get('stats', {}).get('warnings_received', 0) }}</td>
                <td class="portrait" title="{{ u.get('portrait', '') }}">{{ (u.get('portrait') or '')[:80] }}{% if (u.get('portrait') or '')|length > 80 %}…{% endif %}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</body>
</html>
"""

USER_DETAIL_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Пользователь {{ u.get('display_name', user_id) }} — Админ</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 1.5rem; background: #1a1a2e; color: #eee; }
        a { color: #e94560; }
        .back { margin-bottom: 1rem; }
        .card { background: #16213e; padding: 1.5rem; border-radius: 10px; margin-bottom: 1rem; }
        .card h2 { margin: 0 0 1rem; font-size: 1rem; color: #888; }
        .portrait { white-space: pre-wrap; line-height: 1.5; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.5rem; }
        .stat { background: #0f3460; padding: 0.5rem; border-radius: 6px; }
        .user-header { display: flex; align-items: center; gap: 1rem; }
        .avatar-lg { width: 64px; height: 64px; border-radius: 50%; object-fit: cover; background: #0f3460; }
    </style>
</head>
<body>
    <div class="back"><a href="{{ url_for('index') }}">← Назад к списку</a></div>
    <div class="card">
        <h2>Пользователь</h2>
        <div class="user-header">
            <img src="{{ url_for('avatar', user_id=user_id) }}" alt="" class="avatar-lg" onerror="this.style.display='none'">
            <div>
                <p><strong>ID:</strong> {{ user_id }}</p>
                <p><strong>Имя:</strong> {{ u.get('display_name', '—') }}</p>
                <p><strong>Ранг:</strong> {{ rank_labels.get(u.get('rank'), u.get('rank')) }}</p>
            </div>
        </div>
    </div>
    <div class="card">
        <h2>Статистика</h2>
        <div class="stats">
            <div class="stat">Сообщений: {{ u.get('stats', {}).get('total_messages', 0) }}</div>
            <div class="stat">Полит.: {{ u.get('stats', {}).get('political_messages', 0) }}</div>
            <div class="stat">+ {{ u.get('stats', {}).get('positive_sentiment', 0) }}</div>
            <div class="stat">− {{ u.get('stats', {}).get('negative_sentiment', 0) }}</div>
            <div class="stat">0 {{ u.get('stats', {}).get('neutral_sentiment', 0) }}</div>
            <div class="stat">Замечаний: {{ u.get('stats', {}).get('warnings_received', 0) }}</div>
        </div>
    </div>
    <div class="card">
        <h2>Портрет</h2>
        <div class="portrait">{{ u.get('portrait', '—') or '—' }}</div>
    </div>
    {% if u.get('tone_to_bot') %}
    <div class="card">
        <h2>Настроение к боту</h2>
        <p>{{ u.get('tone_to_bot') }}</p>
    </div>
    {% endif %}
</body>
</html>
"""


if __name__ == "__main__":
    host = os.getenv("ADMIN_HOST", "127.0.0.1")
    port = int(os.getenv("ADMIN_PORT", "5000"))
    print(f"Админ-панель: http://{host}:{port}")
    if not ADMIN_PASSWORD:
        print("Внимание: ADMIN_PASSWORD не задан — вход без пароля (только localhost)")
    app.run(host=host, port=port, debug=False)
