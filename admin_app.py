"""
Админ-панель для мониторинга бота: статистика пользователей, портреты, ранги, настроения.
Кнопка «Построить портрет» использует архив сообщений, которые бот прочитал в чате.

Запуск: python admin_app.py
По умолчанию: http://127.0.0.1:5000
"""

import json
import os
import time
import urllib.request
import urllib.parse
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, Response, render_template_string, request, session, url_for
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", encoding="utf-8-sig")

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_SECRET_KEY", "change-me-in-production")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
USERS_JSON = Path(__file__).resolve().parent / "user_stats.json"
RESTART_FLAG_PATH = Path(__file__).resolve().parent / "restart_bot.flag"
BOT_LAST_START_PATH = Path(__file__).resolve().parent / "bot_last_start.json"
RESET_POLITICAL_COUNT_PATH = Path(__file__).resolve().parent / "reset_political_count.json"
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

RANK_LABELS = {"loyal": "🇷🇺 Лояльный", "neutral": "⚪ Нейтральный", "opposition": "🔴 Оппозиция", "unknown": "❓ Неизвестно"}

_avatar_cache: dict[str, str] = {}
# user_id (str) → идёт построение портрета (синхронизация главная ↔ профиль)
_portrait_building: set[str] = set()


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
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Вход</title></head>
<body style="font-family:sans-serif;padding:2rem;background:#1a1a2e;color:#eee;">
<div style="max-width:360px;margin:auto;background:#16213e;padding:2rem;border-radius:12px;">
<h1 style="margin:0 0 1.5rem;">Вход</h1>
<form method="post">
<input type="password" name="password" placeholder="Пароль" required style="width:100%;padding:0.75rem;margin-bottom:1rem;border-radius:8px;">
<button type="submit" style="width:100%;padding:0.75rem;background:#e94560;border:none;border-radius:8px;color:#fff;cursor:pointer;">Войти</button>
</form></div></body></html>
""")


@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
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
        chats=chats,
        current_chat=chat_id or "all",
        portrait_building_user_ids=list(_portrait_building),
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

    def _fmt_emoji_list(val, default: str) -> str:
        if isinstance(val, list) and val:
            return ",".join(str(x) for x in val)
        if isinstance(val, str) and val:
            return val
        return default

    return render_template_string(SETTINGS_HTML, settings=s, _fmt_emoji_list=_fmt_emoji_list)


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    """API настроек: GET — все, POST — обновить {key: value}."""
    from bot_settings import get_all, set_all, DEFAULTS
    if request.method == "POST":
        data = request.get_json() or {}
        updates = {}
        for k, v in data.items():
            if k in DEFAULTS and k != "chat_settings":
                updates[k] = v
        if updates:
            set_all(updates)
        return jsonify({"ok": True, "settings": get_all()})
    return jsonify({"ok": True, "settings": get_all()})


@app.route("/api/reset-political-count", methods=["POST"])
@login_required
def api_reset_political_count():
    """Сброс счётчика полит. сообщений для чата. POST: {chat_id: 123}."""
    try:
        data = request.get_json() or request.form or {}
        chat_id = data.get("chat_id")
        if chat_id is None:
            return jsonify({"ok": False, "error": "Нужен chat_id"}), 400
        try:
            cid = int(chat_id)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "chat_id должен быть числом"}), 400
        cid_str = str(cid)
        existing = []
        if RESET_POLITICAL_COUNT_PATH.exists():
            try:
                fdata = json.loads(RESET_POLITICAL_COUNT_PATH.read_text(encoding="utf-8"))
                existing = list(fdata.get("chat_ids") or [])
            except Exception:
                pass
        if cid_str not in existing:
            existing.append(cid_str)
        RESET_POLITICAL_COUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESET_POLITICAL_COUNT_PATH.write_text(json.dumps({"chat_ids": existing}, ensure_ascii=False), encoding="utf-8")
        return jsonify({"ok": True, "message": f"Сброс для чата {cid} запланирован. Применится при следующем сообщении в чате."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
    from user_stats import get_user_messages_archive
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
    return render_template_string(
        USER_DETAIL_HTML,
        user_id=user_id,
        u=u,
        rank_labels=RANK_LABELS,
        effective_tone=_get_effective_tone(u),
        archive_count=archive_count,
        archive_by_chat=archive_by_chat,
        images_archive=images_archive,
        chats_titles=chats_titles,
        chat_id=chat_id or "",
        is_portrait_building=user_id in _portrait_building,
    )


@app.route("/avatar/<user_id>")
@login_required
def avatar(user_id):
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
        return redirect(url_for("index") + "?restart=requested")
    except Exception:
        return redirect(url_for("index") + "?restart=err")


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


@app.route("/api/portrait-from-storage", methods=["POST"])
@login_required
def api_portrait_from_storage():
    try:
        data = request.get_json() or {}
        user_id = data.get("user_id")
        chat_id = data.get("chat_id")
        if not user_id:
            return jsonify({"ok": False, "error": "Нужен user_id"}), 400

        from user_stats import get_user_messages_archive, get_user, set_deep_portrait
        from ai_analyzer import build_deep_portrait_from_messages

        try:
            user_id_int = int(user_id)
        except ValueError:
            return jsonify({"ok": False, "error": "Некорректный user_id"}), 400

        user_id_str = str(user_id_int)
        if user_id_str in _portrait_building:
            return jsonify({"ok": False, "error": "Портрет уже создаётся для этого пользователя"}), 409

        _portrait_building.add(user_id_str)
        try:
            chat_id_int = int(chat_id) if chat_id and chat_id != "all" else None
            messages = get_user_messages_archive(user_id_int, chat_id_int)
            if not messages:
                return jsonify({
                    "ok": False,
                    "error": "Нет сообщений в архиве. Бот накапливает сообщения по мере чтения чата. Подождите, пока участник напишет больше.",
                }), 404

            u = get_user(user_id_int)
            display_name = u.get("display_name", user_id)
            portrait, rank = build_deep_portrait_from_messages(messages, display_name)
            set_deep_portrait(user_id_int, portrait, rank)

            return jsonify({
                "ok": True,
                "messages_count": len(messages),
                "portrait_preview": (portrait[:500] + "…") if len(portrait) > 500 else portrait,
            })
        finally:
            _portrait_building.discard(user_id_str)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/portrait-building-status")
@login_required
def api_portrait_building_status():
    """Возвращает, идёт ли построение портрета для user_id или список всех."""
    user_id = request.args.get("user_id")
    if user_id:
        return jsonify({"building": user_id in _portrait_building})
    return jsonify({"building_user_ids": list(_portrait_building)})


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
        question = generate_question_of_day(messages, display_name)
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
            question = generate_question_of_day(messages, display_name)
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


@app.route("/social-graph")
@login_required
def social_graph():
    """Дерево связей пользователей: кто с кем общается, о чём."""
    from social_graph import get_connections, get_chats_with_connections
    from user_stats import get_user_display_names

    chat_id = request.args.get("chat")
    chat_id_int = int(chat_id) if chat_id and str(chat_id).lstrip("-").isdigit() else None
    connections = get_connections(chat_id_int)
    chats = get_chats_with_connections()
    names = get_user_display_names()

    for conn in connections:
        conn["name_a"] = names.get(str(conn.get("user_a", "")), str(conn.get("user_a", "")))
        conn["name_b"] = names.get(str(conn.get("user_b", "")), str(conn.get("user_b", "")))

    return render_template_string(
        SOCIAL_GRAPH_HTML,
        connections=connections,
        chats=chats,
        current_chat=chat_id or "",
    )


@app.route("/api/process-social-graph", methods=["POST"])
@login_required
def api_process_social_graph():
    """Запустить обработку накопленных диалогов (саммари, обновление связей)."""
    try:
        from social_graph import process_pending_days
        n = process_pending_days()
        return jsonify({"ok": True, "processed": n})
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


INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Админ-панель — Политмонитор</title>
<style>
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:1.5rem;background:#1a1a2e;color:#eee}
a{color:#e94560;text-decoration:none}a:hover{text-decoration:underline}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;flex-wrap:wrap;gap:1rem}
h1{margin:0;font-size:1.5rem}
.logout{color:#888;font-size:0.9rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem;margin-bottom:2rem}
.card{background:#16213e;padding:1rem;border-radius:10px}
.card h3{margin:0 0 0.5rem;font-size:0.85rem;color:#888;font-weight:500}
.card .val{font-size:1.5rem;font-weight:600}
table{width:100%;border-collapse:collapse;background:#16213e;border-radius:10px;overflow:hidden}
th,td{padding:0.75rem 1rem;text-align:left}
th{background:#0f3460;font-weight:500;font-size:0.85rem}
tr:hover{background:#1a2a4a}tr.clickable-row{cursor:pointer}
.rank{font-size:1.1rem}
.portrait{font-size:0.85rem;color:#aaa;max-width:400px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.user-cell{display:flex;align-items:center;gap:0.5rem}
.avatar{width:32px;height:32px;border-radius:50%;object-fit:cover;background:#0f3460}
.btn{display:inline-block;padding:0.5rem 1rem;background:#e94560;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:0.9rem}
.btn:hover{background:#ff6b6b}
.btn-sm{padding:0.35rem 0.65rem;font-size:0.8rem}
</style>
</head>
<body>
<div class="header">
<h1>Админ-панель — Политмонитор</h1>
<div style="display:flex;gap:1rem;align-items:center;">
<a href="{{ url_for('social_graph') }}" class="btn" style="background:#0f3460;padding:0.5rem 1rem;font-size:0.9rem;text-decoration:none;">Дерево связей</a>
<a href="{{ url_for('settings') }}" class="btn" style="background:#0f3460;padding:0.5rem 1rem;font-size:0.9rem;text-decoration:none;">Настройки</a>
<form method="post" action="{{ url_for('restart_bot') }}" style="display:inline;">
<button type="submit" class="btn" style="background:#0f3460;padding:0.5rem 1rem;font-size:0.9rem;">Перезапустить бота</button>
</form>
{% if session.get('admin_logged_in') %}<a href="{{ url_for('logout') }}" class="logout">Выйти</a>{% endif %}
</div>
</div>
<p id="restart-msg" style="margin-bottom:1rem;"></p>
{% if request.args.get('restart') == 'requested' %}
<script>
(function(){
var el=document.getElementById('restart-msg');
el.style.color='#4ade80';
el.textContent='Запрос на перезапуск отправлен. Бот перезапустится в течение ~30 сек.';
var attempts=0,maxAttempts=45;
var poll=setInterval(function(){
attempts++;
if(attempts>maxAttempts){clearInterval(poll);return}
fetch('{{ url_for("api_restart_status") }}').then(function(r){return r.json()}).then(function(d){
if(d.restarted){clearInterval(poll);el.style.color='#4ade80';el.textContent='Бот успешно перезапущен';setTimeout(function(){el.style.display='none'},3000)}
});
},2000);
})();
</script>
{% elif request.args.get('restart') == 'err' %}
<script>
document.getElementById('restart-msg').style.color='#e94560';
document.getElementById('restart-msg').textContent='Ошибка при создании флага перезапуска.';
setTimeout(function(){document.getElementById('restart-msg').style.display='none'},5000);
</script>
{% endif %}
<div class="tabs" style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1rem;">
<a href="{{ url_for('index', chat='all') }}" class="tab {% if current_chat == 'all' %}tab-active{% endif %}" style="padding:0.5rem 1rem;background:#16213e;border-radius:8px;color:#eee;text-decoration:none;">Все</a>
{% for c in chats %}
<a href="{{ url_for('index', chat=c['chat_id']) }}" class="tab {% if current_chat == c['chat_id'] %}tab-active{% endif %}" style="padding:0.5rem 1rem;background:#16213e;border-radius:8px;color:#eee;text-decoration:none;">{{ (c['title'] or c['chat_id'])[:30] }}{% if (c['title'] or c['chat_id'])|length > 30 %}…{% endif %}</a>
{% endfor %}
</div>
{% if current_chat and current_chat != 'all' %}
<div style="margin-bottom:1rem;">
<button type="button" class="btn btn-sm" id="btn-reset-political" style="background:#0f3460;">Сбросить счётчик полит. сообщений</button>
<span id="reset-result" style="font-size:0.85rem;margin-left:0.5rem;"></span>
</div>
<script>
document.getElementById('btn-reset-political').onclick=function(){
var btn=this, span=document.getElementById('reset-result');
btn.disabled=true; span.textContent='…'; span.style.color='#aaa';
fetch('{{ url_for("api_reset_political_count") }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:'{{ current_chat }}'})})
.then(function(r){return r.json();})
.then(function(d){
if(d.ok){span.textContent='Готово. Сброс применится при следующем сообщении.';span.style.color='#4ade80';}
else{span.textContent=d.error||'Ошибка';span.style.color='#e94560';}
btn.disabled=false;
}).catch(function(){span.textContent='Ошибка сети';span.style.color='#e94560';btn.disabled=false;});
};
</script>
{% endif %}
<style>.tab-active{background:#e94560 !important;}</style>
<div class="cards">
<div class="card"><h3>Пользователей</h3><span class="val">{{ total }}</span></div>
<div class="card"><h3>Полит. сообщений</h3><span class="val">{{ total_pol }}</span></div>
<div class="card"><h3>Замечаний выдано</h3><span class="val">{{ total_warn }}</span></div>
{% for r,cnt in ranks.items() %}<div class="card"><h3>{{ rank_labels.get(r,r) }}</h3><span class="val">{{ cnt }}</span></div>{% endfor %}
</div>
<table>
<thead>
<tr><th>ID</th><th>Имя</th><th>Ранг</th><th>Сообщ.</th><th>Полит.</th><th>+ / − / 0</th><th>Замечаний</th><th>Портрет</th><th>Действия</th></tr>
</thead>
<tbody>
{% for uid,u in users %}
<tr class="clickable-row" onclick="location.href='{{ url_for('user_detail', user_id=uid, chat=current_chat) }}'">
<td>{{ uid }}</td>
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
<td onclick="event.stopPropagation()">
<button type="button" class="btn btn-sm" id="btn-portrait-{{ uid }}" onclick="buildPortrait('{{ uid }}', '{{ current_chat }}', this)" {% if uid in portrait_building_user_ids %}disabled{% endif %}>Составить портрет</button>
<span id="result-{{ uid }}" style="font-size:0.8rem;margin-left:0.25rem;">{% if uid in portrait_building_user_ids %}Анализирую…{% endif %}</span>
</td>
</tr>
{% endfor %}
</tbody>
</table>
<script>
function buildPortrait(uid, chatId, btn){
var result=document.getElementById('result-'+uid);
result.textContent='Анализирую…';
result.style.color='#aaa';
btn.disabled=true;
fetch('{{ url_for("api_portrait_from_storage") }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,chat_id:chatId||'all'})})
.then(function(r){return r.json().then(function(d){return {status:r.status,data:d};});})
.then(function(res){
var data=res.data;
if(data.ok){result.textContent='Готово!';result.style.color='#4ade80';setTimeout(function(){location.reload()},1500)}
else if(res.status===409){result.textContent='Уже создаётся…';result.style.color='#aaa'}
else{result.textContent=data.error||'Ошибка';result.style.color='#e94560';btn.disabled=false}
})
.catch(function(){result.textContent='Ошибка';result.style.color='#e94560';btn.disabled=false});
}
{% if portrait_building_user_ids %}
(function poll(){
fetch('{{ url_for("api_portrait_building_status") }}').then(r=>r.json()).then(function(d){
var building=d.building_user_ids||[];
{% for uid,u in users %}
if(!building.includes('{{ uid }}') && document.getElementById('result-{{ uid }}') && document.getElementById('result-{{ uid }}').textContent.indexOf('Анализирую')>=0){
document.getElementById('btn-portrait-{{ uid }}').disabled=false;
document.getElementById('result-{{ uid }}').textContent='';
}
{% endfor %}
if(building.length>0)setTimeout(poll,2000);
else location.reload();
});
})();
{% endif %}
</script>
</body>
</html>
"""

SOCIAL_GRAPH_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Дерево связей — Админ</title>
<style>
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:1.5rem;background:#1a1a2e;color:#eee}
a{color:#e94560;text-decoration:none}a:hover{text-decoration:underline}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;flex-wrap:wrap;gap:1rem}
h1{margin:0;font-size:1.5rem}
.btn{display:inline-block;padding:0.5rem 1rem;background:#e94560;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:0.9rem}
.btn:hover{background:#ff6b6b}
.btn-sm{padding:0.35rem 0.65rem;font-size:0.8rem}
.btn-secondary{background:#0f3460}.btn-secondary:hover{background:#1a2a4a}
.card{background:#16213e;padding:1rem;border-radius:10px;margin-bottom:1rem}
.card h2{margin:0 0 0.5rem;font-size:1rem;color:#888}
.connection{display:flex;align-items:flex-start;gap:1rem;padding:0.75rem;background:#0f3460;border-radius:8px;margin-bottom:0.5rem}
.connection-users{font-weight:600;white-space:nowrap;color:#4ade80}
.connection-summary{font-size:0.9rem;color:#ccc;max-width:600px;white-space:pre-wrap}
.connection-meta{font-size:0.8rem;color:#666}
.tabs{display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1rem}
.tab{padding:0.5rem 1rem;background:#16213e;border-radius:8px;color:#eee;text-decoration:none}
.tab.active{background:#e94560}
</style>
</head>
<body>
<div class="header">
<h1>Дерево связей</h1>
<div style="display:flex;gap:0.5rem;align-items:center;">
<a href="{{ url_for('index') }}" class="btn btn-secondary">← Назад</a>
<button type="button" class="btn btn-sm" id="btn-process" style="background:#0f3460;">Обработать накопленные диалоги</button>
<span id="process-result" style="font-size:0.85rem;margin-left:0.5rem;"></span>
</div>
</div>
<p style="color:#888;font-size:0.9rem;margin-bottom:1rem;">
Связи строятся по reply-to: кто кому отвечает. Раз в день (или по кнопке) диалоги суммируются через ИИ.</p>
<div class="tabs">
<a href="{{ url_for('social_graph') }}" class="tab {% if not current_chat %}active{% endif %}">Все чаты</a>
{% for c in chats %}
<a href="{{ url_for('social_graph', chat=c['chat_id']) }}" class="tab {% if current_chat == c['chat_id']|string %}active{% endif %}">{{ (c['title'] or c['chat_id'])[:25] }}{% if (c['title'] or c['chat_id'])|length > 25 %}…{% endif %}</a>
{% endfor %}
</div>
{% if connections %}
<div class="card">
<h2>Связи ({{ connections|length }})</h2>
{% for conn in connections %}
<div class="connection">
<div>
<div class="connection-users">{{ conn.name_a }} ↔ {{ conn.name_b }}</div>
<div class="connection-meta">ID: {{ conn.user_a }} / {{ conn.user_b }} · сообщений: {{ conn.message_count }} · обновлено: {{ conn.last_updated }}</div>
</div>
<div class="connection-summary">{{ conn.summary or '—' }}</div>
</div>
{% endfor %}
</div>
{% else %}
<div class="card">
<p style="color:#888;">Связей пока нет. Диалоги накапливаются по мере общения в чате (reply-to). Обработка — раз в 4 часа или по кнопке.</p>
</div>
{% endif %}
<script>
document.getElementById('btn-process').onclick=function(){
var btn=this, span=document.getElementById('process-result');
btn.disabled=true; span.textContent='…'; span.style.color='#aaa';
fetch('{{ url_for("api_process_social_graph") }}',{method:'POST',headers:{'Content-Type':'application/json'}})
.then(function(r){return r.json();})
.then(function(d){
if(d.ok){span.textContent='Обработано дней: '+d.processed;span.style.color='#4ade80';if(d.processed>0)setTimeout(function(){location.reload()},1500)}
else{span.textContent=d.error||'Ошибка';span.style.color='#e94560';}
btn.disabled=false;
}).catch(function(){span.textContent='Ошибка сети';span.style.color='#e94560';btn.disabled=false;});
};
</script>
</body>
</html>
"""

SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Настройки бота — Админ</title>
<style>
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:1.5rem;background:#1a1a2e;color:#eee}
a{color:#e94560;text-decoration:none}a:hover{text-decoration:underline}
.back{margin-bottom:1rem}
.tabs{display:flex;flex-wrap:wrap;gap:0.3rem;margin-bottom:1rem;border-bottom:1px solid #0f3460;padding-bottom:0.5rem}
.tab{padding:0.5rem 1rem;background:#0f3460;border:none;border-radius:8px;color:#aaa;cursor:pointer;font-size:0.9rem}
.tab:hover{color:#fff;background:#1a2a4a}
.tab.active{background:#e94560;color:#fff}
.tab-panel{display:none}
.tab-panel.active{display:block}
.card{background:#16213e;padding:1.5rem;border-radius:10px;margin-bottom:1rem}
.card h2{margin:0 0 0.5rem;font-size:1.1rem;color:#888}
.card .section-desc{font-size:0.85rem;color:#666;margin-bottom:1rem;line-height:1.4}
.setting-row{display:flex;align-items:center;justify-content:space-between;padding:0.6rem 0;border-bottom:1px solid #0f3460;flex-wrap:wrap;gap:0.5rem}
.setting-row:last-child{border-bottom:none}
.setting-desc{font-size:0.85rem;color:#888;margin-top:0.2rem}
label{display:flex;align-items:center;gap:0.5rem;cursor:pointer}
input[type="checkbox"]{width:18px;height:18px;cursor:pointer}
input[type="number"],input[type="text"],select{padding:0.4rem 0.6rem;background:#0f3460;border:1px solid #1a2a4a;border-radius:6px;color:#eee;min-width:80px}
.btn{display:inline-block;padding:0.5rem 1rem;background:#e94560;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:0.9rem}
.btn:hover{background:#ff6b6b}
</style>
</head>
<body>
<div class="back"><a href="{{ url_for('index') }}">← Назад к списку</a></div>
<form method="post" action="{{ url_for('settings') }}">
<div class="tabs">
<button type="button" class="tab active" data-tab="moderation">Модерация</button>
<button type="button" class="tab" data-tab="reactions">Реакции</button>
<button type="button" class="tab" data-tab="question">Вопрос дня</button>
<button type="button" class="tab" data-tab="dm">Личка</button>
<button type="button" class="tab" data-tab="greeting">Приветствие</button>
<button type="button" class="tab" data-tab="tech">Технические</button>
</div>

<div id="moderation" class="tab-panel active">
<div class="card"><h2>Модерация политики</h2>
<div class="section-desc">Когда бот начнёт писать замечания в чате.</div>
<div class="setting-row" style="background:#1a2a4a;border-radius:8px;padding:1rem;margin-bottom:0.5rem;border:2px solid #e94560;">
<div><strong style="font-size:1.1rem;">👍 Ставить лайки (эмодзи) на полит. сообщения 1–4</strong><div class="setting-desc" style="margin-top:0.5rem;">ВКЛЮЧИ ЭТО, чтобы бот ставил эмодзи на первые 4 сообщения. Текст замечания — с 5-го.</div></div>
<label><input type="checkbox" name="reactions_political_1_5" value="1" {% if settings.get('reactions_political_1_5') %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Модерация включена</strong><div class="setting-desc">Включить/выключить замечания бота за политические темы в чате</div></div>
<label><input type="checkbox" name="moderation_enabled" value="1" {% if settings.get('moderation_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Анализ картинок</strong><div class="setting-desc">Анализировать фото на политический контент (мемы, флаги и т.п.). Требует vision-модель (OPENAI_VISION_MODEL)</div></div>
<label><input type="checkbox" name="analyze_images" value="1" {% if settings.get('analyze_images', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Реакции на фото</strong><div class="setting-desc">Ставить подходящие по контексту эмодзи на фото (мем — 😂, пошлое — 😏, техническое — 🤓 и т.п.)</div></div>
<label><input type="checkbox" name="reactions_on_photos" value="1" {% if settings.get('reactions_on_photos', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Сообщений до первого замечания</strong><div class="setting-desc">С 1-го по (N−1)-е — только лайки. С N-го — текст замечания (1–20)</div></div>
<input type="number" name="msgs_before_react" value="{{ settings.get('msgs_before_react', 5) }}" min="1" max="20"></div>
<div class="setting-row"><div><strong>Умеренный: при отсутствии политики</strong><div class="setting-desc">Если пользователь «умеренный», но в сообщении нет политики — не реагировать или похвалить</div></div>
<select name="style_moderate_react"><option value="none" {% if settings.get('style_moderate_react')=='none' %}selected{% endif %}>Не реагировать</option><option value="praise" {% if settings.get('style_moderate_react','praise')=='praise' %}selected{% endif %}>Похвала</option></select></div>
<div class="setting-row"><div><strong>Активный стиль — частота</strong><div class="setting-desc">Как часто реагировать на «активных»: каждое сообщение или через раз</div></div>
<select name="style_active_frequency"><option value="every" {% if settings.get('style_active_frequency')=='every' %}selected{% endif %}>Каждое</option><option value="every_other" {% if settings.get('style_active_frequency','every_other')=='every_other' %}selected{% endif %}>Через раз</option></select></div>
<div class="setting-row"><div><strong>Режим «зверь» — частота</strong><div class="setting-desc">Как часто реагировать на самых активных в политике</div></div>
<select name="style_beast_frequency"><option value="every" {% if settings.get('style_beast_frequency','every')=='every' %}selected{% endif %}>Каждое</option><option value="every_other" {% if settings.get('style_beast_frequency')=='every_other' %}selected{% endif %}>Через раз</option></select></div>
<div class="setting-row"><div><strong>Сброс после нейтральных</strong><div class="setting-desc">Сколько нейтральных сообщений подряд сбрасывает счётчик политики (5–50)</div></div>
<input type="number" name="reset_after_neutral" value="{{ settings.get('reset_after_neutral', 25) }}" min="5" max="50"></div>
<div class="setting-row"><div><strong>Фраза «Я долго терпел…»</strong><div class="setting-desc">Добавлять ли в ответы классическую фразу про терпение</div></div>
<label><input type="checkbox" name="patience_phrase_enabled" value="1" {% if settings.get('patience_phrase_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Строка про статью УК</strong><div class="setting-desc">Упоминать ли статью Уголовного кодекса в ответах</div></div>
<label><input type="checkbox" name="article_line_enabled" value="1" {% if settings.get('article_line_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Персональные замечания (ИИ)</strong><div class="setting-desc">Использовать ли ИИ для индивидуальных замечаний под конкретного пользователя</div></div>
<label><input type="checkbox" name="use_personalized_remarks" value="1" {% if settings.get('use_personalized_remarks', True) %}checked{% endif %}><span>Вкл.</span></label></div>
</div>
</div>

<div id="reactions" class="tab-panel">
<div class="card"><h2>Поощрения</h2>
<div class="section-desc">Реакции на нейтральные/хорошие сообщения: похвала, поддержка.</div>
<div class="setting-row"><div><strong>Поощрения включены</strong><div class="setting-desc">Реагировать ли на сообщения без политики (похвала, поддержка)</div></div>
<label><input type="checkbox" name="encouragement_enabled" value="1" {% if settings.get('encouragement_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Стиль поощрений</strong><div class="setting-desc">Шаблоны — готовые фразы; ИИ — генерация под контекст; Оба — случайный выбор</div></div>
<select name="encouragement_style"><option value="template" {% if settings.get('encouragement_style')=='template' %}selected{% endif %}>Шаблоны</option><option value="personalized" {% if settings.get('encouragement_style')=='personalized' %}selected{% endif %}>ИИ</option><option value="both" {% if settings.get('encouragement_style','both')=='both' %}selected{% endif %}>Оба</option></select></div>
</div>
<div class="card"><h2>Реакции на политику</h2>
<div class="section-desc">Эмодзи на политические сообщения 1–4. Главный переключатель «Ставить лайки» — во вкладке «Модерация» (первый пункт).</div>
<div class="setting-row"><div><strong>Режим лайков</strong><div class="setting-desc">Только эмодзи — всегда; Случайно — 50% шанс; Только текст — без эмодзи</div></div>
<select name="reactions_1_5_mode"><option value="reaction_only" {% if settings.get('reactions_1_5_mode')=='reaction_only' %}selected{% endif %}>Только эмодзи</option><option value="text_only" {% if settings.get('reactions_1_5_mode')=='text_only' %}selected{% endif %}>Только текст</option><option value="random" {% if settings.get('reactions_1_5_mode','random')=='random' %}selected{% endif %}>Случайно</option></select></div>
<div class="section-desc" style="margin-bottom:0.5rem;">Эмодзи объединяются в один пул — бот ставит случайный из ~30.</div>
<div class="setting-row"><div><strong>Позитив</strong></div>
<input type="text" name="reactions_1_5_positive_emoji" value="{{ _fmt_emoji_list(settings.get('reactions_1_5_positive_emoji'), '👍,❤,😍,🥰,🤩,👏,😁,🔥,💯,🎉') }}" style="min-width:200px"></div>
<div class="setting-row"><div><strong>Негатив</strong></div>
<input type="text" name="reactions_1_5_negative_emoji" value="{{ _fmt_emoji_list(settings.get('reactions_1_5_negative_emoji'), '👎,🤮,😡,🤬,😤,😈,💩,🙄,😒,🤢') }}" style="min-width:200px"></div>
<div class="setting-row"><div><strong>Нейтрал</strong></div>
<input type="text" name="reactions_1_5_neutral_emoji" value="{{ _fmt_emoji_list(settings.get('reactions_1_5_neutral_emoji'), '🤔,🤷,😐,🤨,😬,😮,👀,🤓,🫡,💭') }}" style="min-width:200px"></div>
</div>
<div class="card"><h2>Прочие реакции (спонтанные)</h2>
<div class="section-desc">Случайные эмодзи на обычные сообщения (без политики) — для «живости» чата. Включается отдельно от реакций на политику.</div>
<div class="setting-row"><div><strong>Включить</strong><div class="setting-desc">Ставить ли случайные эмодзи на обычные сообщения</div></div>
<label><input type="checkbox" name="spontaneous_reactions" value="1" {% if settings.get('spontaneous_reactions') %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Макс. в день</strong><div class="setting-desc">Сколько спонтанных реакций максимум за сутки (1–20)</div></div>
<input type="number" name="spontaneous_max_per_day" value="{{ settings.get('spontaneous_max_per_day', 5) }}" min="1" max="20"></div>
<div class="setting-row"><div><strong>Мин. интервал (сек)</strong><div class="setting-desc">Минимальная пауза между спонтанными реакциями (300–7200)</div></div>
<input type="number" name="spontaneous_min_interval_sec" value="{{ settings.get('spontaneous_min_interval_sec', 3600) }}" min="300" max="7200"></div>
<div class="setting-row"><div><strong>Вероятность проверки</strong><div class="setting-desc">Шанс 0–1 при каждом сообщении — проверять ли, ставить ли реакцию</div></div>
<input type="number" name="spontaneous_check_chance" value="{{ settings.get('spontaneous_check_chance', 0.2) }}" min="0.01" max="1" step="0.01"></div>
<div class="setting-row"><div><strong>Эмодзи</strong><div class="setting-desc">Список через запятую для случайного выбора</div></div>
<input type="text" name="spontaneous_emojis" value="{{ _fmt_emoji_list(settings.get('spontaneous_emojis'), '👍,❤️,🇷🇺,🔥') }}" style="min-width:120px"></div>
</div>
</div>

<div id="question" class="tab-panel">
<div class="card"><h2>Вопрос дня</h2>
<div class="section-desc">Периодический вопрос в чат: «вопрос дня» — в заданное окно времени.</div>
<div class="setting-row"><div><strong>Включить</strong><div class="setting-desc">Задавать ли «вопрос дня» в чате</div></div>
<label><input type="checkbox" name="question_of_day" value="1" {% if settings.get('question_of_day', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Начало окна (час)</strong><div class="setting-desc">С какого часа (0–23) можно задавать вопрос</div></div>
<input type="number" name="question_of_day_start_hour" value="{{ settings.get('question_of_day_start_hour', 20) }}" min="0" max="23"></div>
<div class="setting-row"><div><strong>Конец окна (час)</strong><div class="setting-desc">До какого часа (0–23) можно задавать вопрос</div></div>
<input type="number" name="question_of_day_end_hour" value="{{ settings.get('question_of_day_end_hour', 22) }}" min="0" max="23"></div>
<div class="setting-row"><div><strong>Мин. интервал (сек)</strong><div class="setting-desc">Минимальная пауза между вопросами (60–600)</div></div>
<input type="number" name="question_of_day_min_interval_sec" value="{{ settings.get('question_of_day_min_interval_sec', 120) }}" min="60" max="600"></div>
</div>
</div>

<div id="dm" class="tab-panel">
<div class="card"><h2>Ответы в личку</h2>
<div class="section-desc">Как бот отвечает в личные сообщения: на обращения, добрые/грубые фразы, технические вопросы.</div>
<div class="setting-row"><div><strong>Отвечать на обращения</strong><div class="setting-desc">Реагировать ли на сообщения, адресованные боту</div></div>
<label><input type="checkbox" name="reply_to_bot_enabled" value="1" {% if settings.get('reply_to_bot_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Добрые ответы</strong><div class="setting-desc">Отвечать на добрые/ласковые обращения</div></div>
<label><input type="checkbox" name="reply_kind_enabled" value="1" {% if settings.get('reply_kind_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Грубые ответы</strong><div class="setting-desc">Отвечать на грубые/оскорбительные обращения</div></div>
<label><input type="checkbox" name="reply_rude_enabled" value="1" {% if settings.get('reply_rude_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Технические ответы</strong><div class="setting-desc">Отвечать на вопросы про бота, команды и т.п.</div></div>
<label><input type="checkbox" name="reply_technical_enabled" value="1" {% if settings.get('reply_technical_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Вероятность вчерашних цитат</strong><div class="setting-desc">Шанс 0–1 при ответе — вставить цитату из вчерашних сообщений чата</div></div>
<input type="number" name="reply_yesterday_quotes_chance" value="{{ settings.get('reply_yesterday_quotes_chance', 0.01) }}" min="0" max="1" step="0.01"></div>
<div class="setting-row"><div><strong>Текст при ошибке ИИ</strong><div class="setting-desc">Фраза, которую бот покажет, если ИИ не смог сгенерировать ответ</div></div>
<input type="text" name="reply_fallback_on_error" value="{{ settings.get('reply_fallback_on_error', 'сейчас не в настроении, напиши потом.') }}" style="min-width:220px"></div>
</div>
</div>

<div id="greeting" class="tab-panel">
<div class="card"><h2>Приветствие и команды</h2>
<div class="section-desc">Сообщение при добавлении бота в чат и доступность команд /ranks и /stats.</div>
<div class="setting-row"><div><strong>Приветствие при добавлении</strong><div class="setting-desc">Отправлять ли приветственное сообщение при добавлении бота в чат</div></div>
<label><input type="checkbox" name="greeting_on_join" value="1" {% if settings.get('greeting_on_join', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>Текст приветствия</strong><div class="setting-desc">Текст сообщения при добавлении бота</div></div>
<input type="text" name="greeting_text" value="{{ settings.get('greeting_text', 'Привет, котятки! Пришёл смотреть за вашим поведением.') }}" style="min-width:280px"></div>
<div class="setting-row"><div><strong>/ranks</strong><div class="setting-desc">Команда для просмотра рейтингов</div></div>
<label><input type="checkbox" name="cmd_ranks_enabled" value="1" {% if settings.get('cmd_ranks_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
<div class="setting-row"><div><strong>/stats</strong><div class="setting-desc">Команда для просмотра статистики</div></div>
<label><input type="checkbox" name="cmd_stats_enabled" value="1" {% if settings.get('cmd_stats_enabled', True) %}checked{% endif %}><span>Вкл.</span></label></div>
</div>
</div>

<div id="tech" class="tab-panel">
<div class="card"><h2>Технические</h2>
<div class="section-desc">Параметры API, кэширования и контекста — для тонкой настройки производительности.</div>
<div class="setting-row"><div><strong>Мин. интервал ИИ (сек)</strong><div class="setting-desc">Пауза между замечаниями (защита от rate limit). Первое замечание после порога — без задержки (5–60)</div></div>
<input type="number" name="api_min_interval_sec" value="{{ settings.get('api_min_interval_sec', 5) }}" min="5" max="60"></div>
<div class="setting-row"><div><strong>Кэш стиля (сек)</strong><div class="setting-desc">Как долго кэшировать стиль пользователя перед повторным запросом к ИИ (60–600)</div></div>
<input type="number" name="batch_style_cache_sec" value="{{ settings.get('batch_style_cache_sec', 300) }}" min="60" max="600"></div>
<div class="setting-row"><div><strong>Мин. строк контекста</strong><div class="setting-desc">Минимум строк истории для анализа модерации (3–30)</div></div>
<input type="number" name="min_context_lines" value="{{ settings.get('min_context_lines', 15) }}" min="3" max="30"></div>
<div class="setting-row"><div><strong>Мин. контекст для 1–5</strong><div class="setting-desc">Минимум строк для быстрых реакций 1–5 (3–15)</div></div>
<input type="number" name="min_context_lines_1_5" value="{{ settings.get('min_context_lines_1_5', 5) }}" min="3" max="15"></div>
</div>
</div>

<div style="margin-top:1rem;"><button type="submit" class="btn">Сохранить настройки</button></div>
</form>
<script>
document.querySelectorAll('.tab').forEach(function(btn){
btn.addEventListener('click',function(){
document.querySelectorAll('.tab').forEach(function(b){b.classList.remove('active')});
document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active')});
btn.classList.add('active');
var id=btn.getAttribute('data-tab');
document.getElementById(id).classList.add('active');
});
});
</script>
</body>
</html>
"""

USER_DETAIL_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Пользователь {{ u.get('display_name', user_id) }} — Админ</title>
<style>
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:1.5rem;background:#1a1a2e;color:#eee}
a{color:#e94560}
.back{margin-bottom:1rem}
.card{background:#16213e;padding:1.5rem;border-radius:10px;margin-bottom:1rem}
.card h2{margin:0 0 1rem;font-size:1rem;color:#888}
.portrait{white-space:pre-wrap;line-height:1.5}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:0.5rem}
.stat{background:#0f3460;padding:0.5rem;border-radius:6px}
.user-header{display:flex;align-items:center;gap:1rem}
.avatar-lg{width:64px;height:64px;border-radius:50%;object-fit:cover;background:#0f3460}
.btn{display:inline-block;padding:0.5rem 1rem;background:#e94560;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:0.9rem;margin-top:0.5rem}
.btn:hover{background:#ff6b6b}.btn:disabled{opacity:0.6;cursor:not-allowed}
.telegram-info{margin-top:1rem;padding:1rem;background:#0f3460;border-radius:8px}
.telegram-info p{margin:0.25rem 0}
.telegram-error{color:#e94560}
</style>
</head>
<body>
<div class="back"><a href="{{ url_for('index', chat=chat_id or 'all') }}">← Назад к списку</a> · <a href="{{ url_for('settings') }}">Настройки</a></div>
<div class="card">
<h2>Пользователь</h2>
<div class="user-header">
<img src="{{ url_for('avatar', user_id=user_id) }}" alt="" class="avatar-lg" onerror="this.style.display='none'">
<div>
<p><strong>ID:</strong> {{ user_id }}</p>
<p><strong>Имя:</strong> {{ u.get('display_name', '—') }}</p>
<p><strong>Ранг:</strong> {{ rank_labels.get(u.get('rank'), u.get('rank')) }}</p>
<button type="button" class="btn" id="btn-telegram" onclick="loadTelegramInfo()">Загрузить из Telegram</button>
<div id="telegram-result"></div>
</div>
</div>
</div>
<script>
function loadTelegramInfo(){
var btn=document.getElementById('btn-telegram');
var out=document.getElementById('telegram-result');
btn.disabled=true;
out.innerHTML='Загрузка…';
fetch('{{ url_for("api_telegram_user", user_id=user_id) }}')
.then(r=>r.json())
.then(function(data){
if(data.ok){
var d=data.data;
var html='<div class="telegram-info"><h3 style="margin:0 0 0.5rem">Данные Telegram</h3>';
html+='<p><strong>Имя:</strong> '+(d.first_name||'')+' '+(d.last_name||'')+'</p>';
if(d.username)html+='<p><strong>@username:</strong> <a href="https://t.me/'+d.username+'" target="_blank">@'+d.username+'</a></p>';
if(d.language_code)html+='<p><strong>Язык:</strong> '+d.language_code+'</p>';
if(d.is_premium)html+='<p>Premium ✓</p>';
html+='</div>';
out.innerHTML=html;
}else{out.innerHTML='<span class="telegram-error">'+(data.error||'Ошибка')+'</span>'}
})
.catch(function(){out.innerHTML='<span class="telegram-error">Ошибка запроса</span>'})
.finally(function(){btn.disabled=false});
}
</script>
<div class="card">
<h2>Статистика</h2>
<div class="stats">
<div class="stat">Сообщений: {{ u.get('stats', {}).get('total_messages', 0) }}</div>
<div class="stat">В архиве: {{ archive_count }}</div>
<div class="stat">Полит.: {{ u.get('stats', {}).get('political_messages', 0) }}</div>
<div class="stat">+ {{ u.get('stats', {}).get('positive_sentiment', 0) }}</div>
<div class="stat">− {{ u.get('stats', {}).get('negative_sentiment', 0) }}</div>
<div class="stat">0 {{ u.get('stats', {}).get('neutral_sentiment', 0) }}</div>
<div class="stat">Замечаний: {{ u.get('stats', {}).get('warnings_received', 0) }}</div>
</div>
</div>
<div class="card">
<h2>Архив сообщений (по чатам)</h2>
{% if chat_id and chat_id != 'all' %}
<p style="font-size:0.9rem;color:#888;margin-bottom:0.5rem;">Показан только чат «{{ chats_titles.get(chat_id, chat_id) }}». <a href="{{ url_for('user_detail', user_id=user_id, chat='all') }}">Показать все чаты</a></p>
{% endif %}
{% if archive_by_chat %}
{% for cid, msgs in archive_by_chat.items() %}
{% set chat_title = chats_titles.get(cid, 'Неизвестный чат (старые сообщения без привязки)' if cid == 'unknown' else cid) %}
<div style="margin-bottom:1rem;padding:1rem;background:#0f3460;border-radius:8px;">
<h4 style="margin:0 0 0.5rem;">{{ chat_title }} ({{ msgs|length }} сообщ.)</h4>
<button type="button" class="btn btn-sm" style="background:#e94560;margin-bottom:0.5rem;" onclick="clearArchive('{{ cid }}', this)">Очистить этот чат</button>
<div style="max-height:150px;overflow-y:auto;font-size:0.85rem;color:#aaa;">
{% for m in msgs[-20:] %}
<div style="margin:0.25rem 0;"><span style="color:#888;">{{ m.date[:10] }}</span> {{ m.text[:100] }}{% if m.text|length > 100 %}…{% endif %}</div>
{% endfor %}
{% if msgs|length > 20 %}<div style="color:#666;">… и ещё {{ msgs|length - 20 }}</div>{% endif %}
</div>
</div>
{% endfor %}
<button type="button" class="btn" style="background:#8b0000;" onclick="clearArchive('all', this)">Очистить весь архив</button>
{% else %}
<p style="color:#888;">Архив пуст</p>
{% endif %}
</div>
{% if images_archive %}
<div class="card">
<h2>Проанализированные изображения</h2>
<p style="font-size:0.85rem;color:#888;margin-bottom:0.75rem;">Категория и описание по содержанию (политика, пошлое, мем, техническое и т.п.)</p>
<button type="button" class="btn btn-sm" style="background:#e94560;margin-bottom:0.75rem;" onclick="clearImagesArchive(this)">Очистить архив изображений</button>
<div style="max-height:300px;overflow-y:auto;">
{% for img in images_archive %}
<div style="padding:0.5rem;margin-bottom:0.5rem;background:#0f3460;border-radius:6px;font-size:0.9rem;">
<span style="color:#4ade80;font-weight:bold;">{{ img.category }}</span> — {{ img.date }}{% if img.get('reaction_emoji') %} реакция: {{ img.reaction_emoji }}{% endif %}<br>
<span style="color:#ccc;">{{ img.description or '—' }}</span>
</div>
{% endfor %}
</div>
</div>
{% endif %}
<div class="card">
<h2>Портрет</h2>
<div class="portrait">{{ u.get('portrait', '—') or '—' }}</div>
<div style="margin-top:1rem;padding:1rem;background:#0f3460;border-radius:8px;">
<h3 style="margin:0 0 0.5rem;font-size:0.95rem;">Составить портрет из архива сообщений</h3>
<p style="font-size:0.85rem;color:#aaa;margin:0 0 0.5rem;">Использует до 1000 сообщений на чат. Психологический, профессиональный и политический портрет через ИИ.</p>
<p style="font-size:0.85rem;margin:0 0 0.5rem;">В архиве: {{ archive_count }} сообщений</p>
<button type="button" class="btn" id="btn-portrait" onclick="buildPortrait()" {% if is_portrait_building %}disabled{% endif %}>Составить портрет</button>
<div id="portrait-result" style="margin-top:0.5rem;font-size:0.9rem;">{% if is_portrait_building %}Анализирую… (1–2 мин){% endif %}</div>
</div>
</div>
<div class="card">
<h2>Настроение к боту</h2>
<p>Влияет на тон ответов бота этому участнику (портрет передаётся в ИИ).</p>
{% if effective_tone %}
<p><strong>Сейчас:</strong> {{ effective_tone }}{% if u.get('tone_override') %} <em>(ручное)</em>{% else %} <em>(авто)</em>{% endif %}</p>
{% else %}
<p><em>Не задано</em></p>
{% endif %}
<form method="post" style="margin-top:1rem;">
<input type="hidden" name="chat" value="{{ chat_id }}">
<input type="text" name="tone_override" placeholder="Напр.: грубит, агрессивен" value="{{ u.get('tone_override', '') }}" style="width:100%;max-width:400px;padding:0.5rem;background:#0f3460;border:1px solid #1a2a4a;border-radius:6px;color:#eee;margin-bottom:0.5rem;">
<div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
<button type="submit" name="tone_action" value="set" class="btn">Задать вручную</button>
<button type="submit" name="tone_action" value="auto" class="btn" style="background:#0f3460;">Использовать авто</button>
</div>
{% set hist = u.get('tone_history') or [] %}
{% if hist %}
<p style="font-size:0.85rem;color:#888;margin-top:1rem;">Быстрый выбор (последние 3):</p>
<div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.5rem;">
{% for h in hist %}
<form method="post" style="display:inline;">
<input type="hidden" name="chat" value="{{ chat_id }}">
<input type="hidden" name="tone_action" value="history">
<input type="hidden" name="tone_history_val" value="{{ h }}">
<button type="submit" class="btn" style="background:#0f3460;font-size:0.85rem;">{{ h[:40] }}{% if h|length > 40 %}…{% endif %}</button>
</form>
{% endfor %}
</div>
{% endif %}
</form>
<p style="font-size:0.85rem;color:#888;margin-top:0.5rem;">«Использовать авто» — сброс, настроение будет определяться ИИ по обращениям к боту.</p>
</div>
<div class="card">
<h2>Вопрос дня</h2>
<p style="font-size:0.9rem;color:#aaa;margin-bottom:1rem;">Бот вечером (20:00–22:00) может задать пользователю один добрый вопрос, основанный на его сообщениях за день. По умолчанию отключено.</p>
<label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;margin-bottom:0.75rem;">
<input type="checkbox" id="qod-checkbox" {% if u.get('question_of_day_enabled') %}checked{% endif %} onchange="toggleQuestionOfDay(this)">
<span>Задавать вопрос дня</span>
</label>
<p style="font-size:0.85rem;color:#888;margin-bottom:0.5rem;">Куда отправлять:</p>
<label style="display:inline-flex;align-items:center;gap:0.5rem;cursor:pointer;margin-right:1rem;">
<input type="radio" name="qod-dest" value="chat" {% if u.get('question_of_day_destination', 'chat') == 'chat' %}checked{% endif %} onchange="setQuestionDest('chat')">
<span>В чат</span>
</label>
<label style="display:inline-flex;align-items:center;gap:0.5rem;cursor:pointer;">
<input type="radio" name="qod-dest" value="private" {% if u.get('question_of_day_destination') == 'private' %}checked{% endif %} onchange="setQuestionDest('private')">
<span>В личку</span>
</label>
<div id="qod-chat-select-wrap" style="margin-top:0.75rem;{% if u.get('question_of_day_destination') == 'private' %}display:none{% endif %}">
<label style="font-size:0.85rem;color:#888;">Чат для отправки:</label>
<select id="qod-chat-select" style="margin-left:0.5rem;padding:0.35rem 0.5rem;background:#0f3460;border:1px solid #1a4a7a;border-radius:6px;color:#fff;min-width:200px;">
<option value="">— загрузка —</option>
</select>
</div>
<div style="margin-bottom:0.5rem;margin-top:1rem;">
<button type="button" class="btn" style="background:#0f3460;" id="btn-qod-preview" onclick="generateQuestionPreview()">Сгенерировать превью</button>
<button type="button" class="btn" id="btn-qod-send" onclick="sendQuestionNow()" style="margin-left:0.5rem;">Отправить сейчас</button>
<span id="qod-preview-status" style="font-size:0.85rem;margin-left:0.5rem;"></span>
</div>
<div id="qod-preview" style="padding:0.75rem;background:#0f3460;border-radius:8px;font-style:italic;display:none;"></div>
</div>
<script>
function toggleQuestionOfDay(cb){
cb.disabled=true;
fetch('{{ url_for("api_question_of_day_toggle", user_id=user_id) }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:cb.checked})})
.then(r=>r.json()).then(function(d){if(d.ok){cb.disabled=false}else{alert(d.error||'Ошибка');cb.checked=!cb.checked;cb.disabled=false}})
.catch(function(){alert('Ошибка');cb.checked=!cb.checked;cb.disabled=false});
}
function setQuestionDest(val){
document.getElementById('qod-chat-select-wrap').style.display=val==='chat'?'block':'none';
if(val==='chat')loadQodChats();
fetch('{{ url_for("api_question_of_day_destination", user_id=user_id) }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({destination:val})})
.then(r=>r.json()).then(function(d){if(!d.ok)alert(d.error||'Ошибка')})
.catch(function(){alert('Ошибка')});
}
function loadQodChats(){
var sel=document.getElementById('qod-chat-select');
if(!sel)return;
sel.innerHTML='<option value="">— загрузка —</option>';
fetch('{{ url_for("api_question_of_day_chats", user_id=user_id) }}')
.then(r=>r.json()).then(function(d){
if(!d.ok){sel.innerHTML='<option value="">Ошибка загрузки</option>';return;}
var opts=[{value:'',text:'Авто (самый активный сегодня)'}];
(d.chats||[]).forEach(function(c){opts.push({value:c.chat_id,text:(c.title||c.chat_id)+' ('+c.today_count+' сегодня)'});});
sel.innerHTML=opts.map(function(o){return'<option value="'+o.value+'">'+o.text+'</option>';}).join('');
})
.catch(function(){sel.innerHTML='<option value="">Ошибка</option>';});
}
if(document.querySelector('input[name="qod-dest"][value="chat"]')&&document.querySelector('input[name="qod-dest"][value="chat"]').checked)loadQodChats();
function sendQuestionNow(){
var btn=document.getElementById('btn-qod-send');
var status=document.getElementById('qod-preview-status');
var previewEl=document.getElementById('qod-preview');
var question=previewEl.style.display!=='none'?previewEl.textContent.trim():'';
var chatSel=document.getElementById('qod-chat-select');
var chatId=chatSel&&chatSel.value!==''?chatSel.value:undefined;
var destChat=document.querySelector('input[name="qod-dest"][value="chat"]');
var payload={question:question||undefined};
if(destChat&&destChat.checked&&chatId)payload.chat_id=parseInt(chatId,10);
btn.disabled=true;
status.textContent='Отправляю…';
fetch('{{ url_for("api_question_of_day_send_now", user_id=user_id) }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
.then(r=>r.json()).then(function(d){
btn.disabled=false;
if(d.ok){status.textContent=d.message||'Отправлено.';status.style.color='#4ade80';setTimeout(function(){status.textContent='';status.style.color='';},4000)}
else{status.textContent=d.error||'Ошибка';status.style.color='#e94560';}
})
.catch(function(){btn.disabled=false;status.textContent='Ошибка';status.style.color='#e94560';});
}
function generateQuestionPreview(){
var btn=document.getElementById('btn-qod-preview');
var status=document.getElementById('qod-preview-status');
var out=document.getElementById('qod-preview');
btn.disabled=true;
status.textContent='Генерирую…';
out.style.display='none';
fetch('{{ url_for("api_question_of_day_preview", user_id=user_id) }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})})
.then(r=>r.json()).then(function(d){
btn.disabled=false;
if(d.ok){out.textContent=d.question;out.style.display='block';status.textContent='';}
else{status.textContent=d.error||'Ошибка';status.style.color='#e94560';}
})
.catch(function(){btn.disabled=false;status.textContent='Ошибка';status.style.color='#e94560';});
}
function buildPortrait(){
var btn=document.getElementById('btn-portrait');
var out=document.getElementById('portrait-result');
btn.disabled=true;
out.innerHTML='Анализирую… (1–2 мин)';
fetch('{{ url_for("api_portrait_from_storage") }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:'{{ user_id }}',chat_id:'{{ chat_id or "all" }}'})})
.then(function(r){return r.json().then(function(d){return {status:r.status,data:d};});})
.then(function(res){
var data=res.data;
if(data.ok){out.innerHTML='<span style="color:#4ade80">Готово! Проанализировано сообщений: '+data.messages_count+'. <a href="">Обновить страницу</a></span>';setTimeout(function(){location.reload()},2000)}
else if(res.status===409){out.innerHTML='Портрет уже создаётся…';}
else{out.innerHTML='<span style="color:#e94560">'+(data.error||'Ошибка')+'</span>';btn.disabled=false}
})
.catch(function(){out.innerHTML='<span style="color:#e94560">Ошибка запроса</span>';btn.disabled=false});
}
{% if is_portrait_building %}
(function poll(){
fetch('{{ url_for("api_portrait_building_status") }}?user_id={{ user_id }}').then(r=>r.json()).then(function(d){
if(!d.building){location.reload();}
else setTimeout(poll,2000);
});
})();
{% endif %}
function clearArchive(chatId, btn){
if(!confirm(chatId==='all'?'Очистить весь архив?':'Очистить архив этого чата?'))return;
btn.disabled=true;
fetch('{{ url_for("api_clear_archive") }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:'{{ user_id }}',chat_id:chatId==='all'?null:chatId})})
.then(r=>r.json())
.then(function(data){
if(data.ok){location.reload()}
else{alert(data.error||'Ошибка');btn.disabled=false}
})
.catch(function(){alert('Ошибка');btn.disabled=false});
}
function clearImagesArchive(btn){
if(!confirm('Очистить архив проанализированных изображений?'))return;
btn.disabled=true;
fetch('{{ url_for("api_clear_images_archive") }}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:'{{ user_id }}'})})
.then(r=>r.json())
.then(function(data){
if(data.ok){location.reload()}
else{alert(data.error||'Ошибка');btn.disabled=false}
})
.catch(function(){alert('Ошибка');btn.disabled=false});
}
</script>
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
