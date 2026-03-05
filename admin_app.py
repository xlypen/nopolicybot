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

from flask import Flask, jsonify, redirect, render_template, Response, request, session, url_for
from dotenv import load_dotenv
from routes.social_graph_routes import register_social_graph_routes
import bot_settings

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
_avatar_img_cache: dict[str, tuple[float, bytes, str]] = {}
_AVATAR_IMG_CACHE_TTL_SEC = 3600
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


register_social_graph_routes(app, login_required)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not ADMIN_PASSWORD:
        return redirect(url_for("index"))
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("index"))
    return render_template("login.html")


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

    digest_preview = ""
    if chat_id and chat_id != "all" and str(chat_id).lstrip("-").isdigit():
        try:
            import social_graph
            digest_preview = social_graph.build_chat_digest(int(chat_id), period_days=1)
        except Exception:
            digest_preview = ""

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
        portrait_building_user_ids=list(_portrait_building),
        digest_preview=digest_preview,
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
        is_portrait_building=user_id in _portrait_building,
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


if __name__ == "__main__":
    host = os.getenv("ADMIN_HOST", "127.0.0.1")
    port = int(os.getenv("ADMIN_PORT", "5000"))
    print(f"Админ-панель: http://{host}:{port}")
    if not ADMIN_PASSWORD:
        print("Внимание: ADMIN_PASSWORD не задан — вход без пароля (только localhost)")
    app.run(host=host, port=port, debug=False)


# === Inline HTML templates removed — now in templates/ ===
