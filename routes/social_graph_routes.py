from flask import jsonify, render_template, request

import social_graph as social_graph_store
import user_stats


def register_social_graph_routes(app, login_required):
    @app.route("/social-graph")
    @login_required
    def social_graph():
        """Дерево связей пользователей: кто с кем общается, о чём."""
        chat_id = request.args.get("chat")
        chat_id_int = int(chat_id) if chat_id and str(chat_id).lstrip("-").isdigit() else None
        connections = social_graph_store.get_connections(chat_id_int)
        chats = social_graph_store.get_chats_with_connections()
        names = user_stats.get_user_display_names()

        for conn in connections:
            conn["name_a"] = names.get(str(conn.get("user_a", "")), str(conn.get("user_a", "")))
            conn["name_b"] = names.get(str(conn.get("user_b", "")), str(conn.get("user_b", "")))

        return render_template(
            "social_graph.html",
            connections=connections,
            chats=chats,
            current_chat=chat_id or "",
        )

    @app.route("/api/process-social-graph", methods=["POST"])
    @login_required
    def api_process_social_graph():
        """Запустить обработку накопленных диалогов (саммари, обновление связей)."""
        try:
            n = social_graph_store.process_pending_days()
            return jsonify({"ok": True, "processed": n})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
