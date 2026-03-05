from flask import jsonify, render_template, request, url_for

import bot_settings
import social_graph as social_graph_store
import user_stats
import re
from datetime import date


def register_social_graph_routes(app, login_required):
    tone_ru = {
        "friendly": "дружелюбный",
        "neutral": "нейтральный",
        "conflict": "конфликтный",
        "toxic": "токсичный",
    }
    topic_ru = {
        "general": "общее",
        "technical": "техническое",
        "work": "работа",
        "politics": "политика",
        "humor": "юмор",
        "personal": "личное",
    }
    role_ru = {
        "connector": "связующий",
        "expert": "эксперт",
        "mediator": "медиатор",
        "provocateur": "провокатор",
        "participant": "участник",
    }
    alert_ru = {
        "new_connection": "новая связь",
        "rising_activity": "рост активности",
        "toxicity_spike": "риск токсичности",
    }
    alert_priority = {
        "toxicity_spike": 3,
        "rising_activity": 2,
        "new_connection": 1,
    }

    def _summary_preview(text: str, max_len: int = 420) -> tuple[str, bool]:
        s = (text or "").strip()
        if not s:
            return "", False
        # Для превью берём самый свежий блок (последняя строка саммари),
        # чтобы текст был цельным и не выглядел «склеенным» из истории.
        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        if lines:
            s = lines[-1]
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) <= max_len:
            return s, False
        chunk = s[:max_len]
        for sep in (". ", "! ", "? ", "; ", ", ", " "):
            pos = chunk.rfind(sep)
            if pos >= max_len * 0.55:
                return chunk[: pos + len(sep)].strip() + " …", True
        return chunk.rstrip() + " …", True

    @app.route("/social-graph")
    @login_required
    def social_graph():
        """Дерево связей пользователей: кто с кем общается, о чём."""
        advanced_enabled = bool(bot_settings.get("social_graph_advanced_insights_enabled"))
        ranked_layout_enabled = bool(bot_settings.get("social_graph_ranked_layout_enabled"))
        forecast_enabled = bool(bot_settings.get("social_graph_conflict_forecast_enabled"))
        roles_enabled = bool(bot_settings.get("social_graph_roles_enabled"))
        recommender_enabled = bool(bot_settings.get("chat_topic_recommender_enabled"))
        chat_id = request.args.get("chat")
        period = (request.args.get("period") or ("7d" if advanced_enabled else "all")).strip().lower()
        if period not in {"24h", "7d", "30d", "all"}:
            period = "7d" if advanced_enabled else "all"
        min_weight = request.args.get("min_weight", "1")
        try:
            min_weight_n = max(0, int(min_weight))
        except Exception:
            min_weight_n = 1 if advanced_enabled else 0
        only_new = advanced_enabled and (request.args.get("only_new") == "1")
        only_rising = advanced_enabled and (request.args.get("only_rising") == "1")
        only_alerts = advanced_enabled and (request.args.get("only_alerts") == "1")
        topic_filter = (request.args.get("topic") or "").strip().lower()
        user_focus = request.args.get("user_focus")
        user_focus_id = int(user_focus) if user_focus and str(user_focus).lstrip("-").isdigit() else None
        pair_focus = request.args.get("pair")
        pair_focus_set = None
        if pair_focus and "|" in pair_focus:
            a, b = pair_focus.split("|", 1)
            if a.lstrip("-").isdigit() and b.lstrip("-").isdigit():
                pair_focus_set = {int(a), int(b)}

        chat_id_int = int(chat_id) if chat_id and str(chat_id).lstrip("-").isdigit() else None
        connections = social_graph_store.get_connections(chat_id_int)
        total_connections_before_filter = len(connections)
        chats = social_graph_store.get_chats_with_connections()
        names = user_stats.get_user_display_names()

        def _period_weight(conn: dict) -> int:
            if period == "24h":
                return int(conn.get("message_count_24h", 0) or 0)
            if period == "7d":
                return int(conn.get("message_count_7d", 0) or 0)
            if period == "30d":
                return int(conn.get("message_count_30d", 0) or 0)
            return int(conn.get("message_count_total", conn.get("message_count", 0)) or 0)

        def _parse_iso_day(value: str) -> date | None:
            try:
                return date.fromisoformat(str(value or "").strip())
            except Exception:
                return None

        def _confidence_label(value: float) -> str:
            if value >= 0.8:
                return "высокая"
            if value >= 0.55:
                return "средняя"
            return "низкая"

        filtered = []
        for conn in connections:
            conn["name_a"] = names.get(str(conn.get("user_a", "")), str(conn.get("user_a", "")))
            conn["name_b"] = names.get(str(conn.get("user_b", "")), str(conn.get("user_b", "")))
            preview, has_more = _summary_preview(conn.get("summary", ""))
            conn["summary_preview"] = preview
            conn["summary_has_more"] = has_more
            conn["tone_label"] = tone_ru.get(str(conn.get("tone", "neutral")), str(conn.get("tone", "neutral")))
            conn["topics_label"] = [topic_ru.get(str(t), str(t)) for t in (conn.get("topics") or [])]
            conn["weight_in_period"] = _period_weight(conn)
            conn["history_preview"] = list(reversed((conn.get("summary_by_date") or [])[-8:]))
            flags = set(conn.get("alert_flags") or [])
            conn["has_alerts"] = bool(flags)
            sorted_flags = sorted(flags, key=lambda f: alert_priority.get(str(f), 0), reverse=True)
            conn["alert_flags_label"] = [alert_ru.get(str(f), str(f)) for f in sorted_flags]
            conf = float(conn.get("confidence", 0.0) or 0.0)
            conn["confidence_label"] = _confidence_label(conf)
            conn["confidence_pct"] = int(round(conf * 100))
            d_last = _parse_iso_day(conn.get("last_seen_at", ""))
            d_first = _parse_iso_day(conn.get("first_seen_at", ""))
            conn["last_seen_label"] = d_last.isoformat() if d_last else "—"
            conn["first_seen_label"] = d_first.isoformat() if d_first else "—"
            conn["days_since_last"] = (date.today() - d_last).days if d_last else None
            conn["search_blob"] = (
                f"{conn['name_a']} {conn['name_b']} {conn.get('user_a', '')} {conn.get('user_b', '')}"
            ).lower()
            conn["sort_activity"] = int(conn.get("weight_in_period", 0) or 0)
            conn["sort_trend"] = float(conn.get("trend_delta", 0.0) or 0.0)
            conn["sort_confidence"] = conf
            conn["sort_freshness"] = d_last.toordinal() if d_last else 0
            conn["sort_risk"] = (
                (8 if "toxicity_spike" in flags else 0)
                + (3 if "rising_activity" in flags else 0)
                + (2 if str(conn.get("tone")) in {"toxic", "conflict"} else 0)
                + max(0.0, float(conn.get("trend_delta", 0.0) or 0.0))
            )
            ua = int(conn.get("user_a", 0) or 0)
            ub = int(conn.get("user_b", 0) or 0)
            if user_focus_id is not None and user_focus_id not in {ua, ub}:
                continue
            if pair_focus_set is not None and {ua, ub} != pair_focus_set:
                continue
            if advanced_enabled:
                if conn["weight_in_period"] < min_weight_n:
                    continue
                if only_new and "new_connection" not in flags:
                    continue
                if only_rising and "rising_activity" not in flags:
                    continue
                if only_alerts and not flags:
                    continue
            if topic_filter and topic_filter not in [str(t).lower() for t in (conn.get("topics") or [])]:
                continue
            filtered.append(conn)
        connections = sorted(
            filtered,
            key=lambda c: (int(c.get("weight_in_period", 0) or 0), float(c.get("trend_delta", 0) or 0)),
            reverse=True,
        )
        empty_state_kind = ""
        if not connections:
            empty_state_kind = "filtered_out" if total_connections_before_filter > 0 else "no_data"

        node_ids = set()
        edges = []
        for conn in connections:
            ua = int(conn.get("user_a", 0) or 0)
            ub = int(conn.get("user_b", 0) or 0)
            if not ua or not ub:
                continue
            node_ids.add(ua)
            node_ids.add(ub)
            edges.append({
                "source": ua,
                "target": ub,
                "weight": int(conn.get("message_count", 0) or 0),
                "last_updated": conn.get("last_updated", ""),
            })

        nodes = []
        degree_by_user: dict[int, int] = {}
        for e in edges:
            degree_by_user[e["source"]] = degree_by_user.get(e["source"], 0) + 1
            degree_by_user[e["target"]] = degree_by_user.get(e["target"], 0) + 1
        for uid in sorted(node_ids):
            uid_str = str(uid)
            nodes.append({
                "id": uid,
                "label": names.get(uid_str, uid_str),
                "avatar": url_for("avatar", user_id=uid_str),
                "degree": int(degree_by_user.get(uid, 0)),
            })
        top_connectors = sorted(
            [{"id": n["id"], "label": n["label"], "degree": n["degree"]} for n in nodes],
            key=lambda x: x["degree"],
            reverse=True,
        )[:7]

        # Агрегаты для визуализации метрик и тем.
        topic_counts: dict[str, int] = {}
        tone_counts: dict[str, int] = {}
        period_messages = 0
        active_24h = 0
        alerts_count = 0
        confidence_sum = 0.0
        for conn in connections:
            period_messages += int(conn.get("weight_in_period", 0) or 0)
            if int(conn.get("message_count_24h", 0) or 0) > 0:
                active_24h += 1
            if conn.get("has_alerts"):
                alerts_count += 1
            confidence_sum += float(conn.get("confidence", 0.0) or 0.0)
            tone = str(conn.get("tone", "neutral") or "neutral")
            tone_counts[tone] = tone_counts.get(tone, 0) + 1
            for t in (conn.get("topics") or []):
                tt = str(t or "").strip()
                if tt:
                    topic_counts[tt] = topic_counts.get(tt, 0) + 1

        total_conn = len(connections)
        avg_conf = round(confidence_sum / total_conn, 2) if total_conn else 0.0
        top_topics = [
            {"topic": k, "topic_label": topic_ru.get(k, k), "count": v}
            for k, v in sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)[:12]
        ]
        tone_total = sum(tone_counts.values()) or 1
        tone_bars = [
            {
                "tone": tone,
                "tone_label": tone_ru.get(tone, tone),
                "count": cnt,
                "pct": round((cnt / tone_total) * 100, 1),
            }
            for tone, cnt in sorted(tone_counts.items(), key=lambda kv: kv[1], reverse=True)
        ]
        metrics = {
            "connections": total_conn,
            "users": len(nodes),
            "period_messages": period_messages,
            "active_24h": active_24h,
            "alerts": alerts_count,
            "avg_confidence": avg_conf,
        }
        base_query: dict[str, str | int] = {
            "period": period,
            "min_weight": min_weight_n,
        }
        if chat_id:
            base_query["chat"] = chat_id
        if only_new:
            base_query["only_new"] = "1"
        if only_rising:
            base_query["only_rising"] = "1"
        if only_alerts:
            base_query["only_alerts"] = "1"
        if user_focus_id is not None:
            base_query["user_focus"] = user_focus_id
        if pair_focus:
            base_query["pair"] = pair_focus
        clear_filters_query: dict[str, str | int] = {}
        if chat_id:
            clear_filters_query["chat"] = chat_id
        reset_filters_href = url_for("social_graph", **clear_filters_query)
        active_filters: list[str] = []
        if period != ("7d" if advanced_enabled else "all"):
            active_filters.append(f"период: {period}")
        if min_weight_n > (1 if advanced_enabled else 0):
            active_filters.append(f"мин. вес: {min_weight_n}")
        if only_new:
            active_filters.append("только новые")
        if only_rising:
            active_filters.append("только растущие")
        if only_alerts:
            active_filters.append("только с алертами")
        if topic_filter:
            active_filters.append(f"тема: {topic_filter}")
        if user_focus_id is not None:
            active_filters.append(f"участник: {user_focus_id}")
        if pair_focus:
            active_filters.append(f"пара: {pair_focus}")
        for item in top_topics:
            item["href"] = url_for("social_graph", **base_query, topic=item["topic"])
        topic_clear_href = url_for("social_graph", **base_query)
        focus_clear_href = url_for("social_graph", **{k: v for k, v in base_query.items() if k not in {"user_focus", "pair"}})
        conflict_forecast = social_graph_store.get_conflict_forecast(chat_id_int, limit=8) if forecast_enabled else []
        roles = social_graph_store.get_user_roles(chat_id_int, limit=20) if roles_enabled else []
        def _user_href(uid: int) -> str:
            if chat_id:
                return url_for("user_detail", user_id=str(uid), chat=chat_id)
            return url_for("user_detail", user_id=str(uid), chat="all")
        for f in conflict_forecast:
            ua = int(f.get("user_a", 0) or 0)
            ub = int(f.get("user_b", 0) or 0)
            f["name_a"] = names.get(str(ua), str(ua))
            f["name_b"] = names.get(str(ub), str(ub))
            f["tone_label"] = tone_ru.get(str(f.get("tone", "neutral")), str(f.get("tone", "neutral")))
            f["href_a"] = _user_href(ua) if ua else "#"
            f["href_b"] = _user_href(ub) if ub else "#"
            f["href_pair"] = url_for("social_graph", **{**base_query, "pair": f"{min(ua, ub)}|{max(ua, ub)}"}) if ua and ub else "#"
        for rr in roles:
            uid_s = str(rr.get("user_id"))
            rr["name"] = names.get(uid_s, uid_s)
            rr["role_label"] = role_ru.get(str(rr.get("role", "participant")), str(rr.get("role", "participant")))
            rr["top_topics_labels"] = [topic_ru.get(str(t), str(t)) for t in (rr.get("top_topics") or [])]
            rr["href_user"] = _user_href(int(rr.get("user_id", 0) or 0))
            rr["href_focus"] = url_for("social_graph", **{**base_query, "user_focus": int(rr.get("user_id", 0) or 0)})

        graph_focus_rows = []
        for conn in connections:
            graph_focus_rows.append({
                "user_a": int(conn.get("user_a", 0) or 0),
                "user_b": int(conn.get("user_b", 0) or 0),
                "name_a": conn.get("name_a", ""),
                "name_b": conn.get("name_b", ""),
                "weight_in_period": int(conn.get("weight_in_period", 0) or 0),
                "message_count_24h": int(conn.get("message_count_24h", 0) or 0),
                "message_count_7d": int(conn.get("message_count_7d", 0) or 0),
                "message_count_30d": int(conn.get("message_count_30d", 0) or 0),
                "trend_delta": float(conn.get("trend_delta", 0.0) or 0.0),
                "tone_label": conn.get("tone_label", ""),
                "topics_label": list(conn.get("topics_label") or []),
                "confidence_pct": int(conn.get("confidence_pct", 0) or 0),
                "alert_flags_label": list(conn.get("alert_flags_label") or []),
                "last_seen_label": conn.get("last_seen_label", "—"),
                "days_since_last": conn.get("days_since_last"),
                "summary_preview": conn.get("summary_preview", ""),
            })

        return render_template(
            "social_graph.html",
            connections=connections,
            chats=chats,
            current_chat=chat_id or "",
            graph_nodes=nodes,
            graph_edges=edges,
            period=period,
            min_weight=min_weight_n,
            only_new=only_new,
            only_rising=only_rising,
            only_alerts=only_alerts,
            topic_filter=topic_filter,
            advanced_enabled=advanced_enabled,
            ranked_layout_enabled=ranked_layout_enabled,
            top_connectors=top_connectors,
            realtime_enabled=bool(bot_settings.get("social_graph_realtime_enabled")),
            realtime_interval_sec=bot_settings.get_int("social_graph_realtime_interval_sec", lo=15, hi=1800),
            realtime_min_new=bot_settings.get_int("social_graph_realtime_min_new_messages", lo=1, hi=20),
            graph_metrics=metrics,
            top_topics=top_topics,
            tone_bars=tone_bars,
            topic_clear_href=topic_clear_href,
            focus_clear_href=focus_clear_href,
            forecast_enabled=forecast_enabled,
            roles_enabled=roles_enabled,
            recommender_enabled=recommender_enabled,
            conflict_forecast=conflict_forecast,
            user_roles=roles,
            user_focus_id=user_focus_id,
            pair_focus=pair_focus or "",
            active_filters=active_filters,
            reset_filters_href=reset_filters_href,
            empty_state_kind=empty_state_kind,
            total_connections_before_filter=total_connections_before_filter,
            graph_focus_rows=graph_focus_rows,
        )

    @app.route("/api/process-social-graph", methods=["POST"])
    @login_required
    def api_process_social_graph():
        """Запустить обработку накопленных диалогов (саммари, обновление связей)."""
        try:
            processed_days = social_graph_store.process_pending_days()
            live_updated = social_graph_store.process_realtime_updates(min_new_messages=1)
            return jsonify({"ok": True, "processed_days": processed_days, "live_updated": live_updated})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
