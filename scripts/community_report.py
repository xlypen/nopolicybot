#!/usr/bin/env python3
"""
Generate an automatic community report from edges, profiles, and messages.

Produces a Markdown report with:
  - Community overview (size, activity, date range)
  - Opinion leaders (PageRank, in-degree from social graph)
  - User roles and personality highlights
  - Conflict pairs and toxic edges
  - Activity trends and churn signals
  - Topic distribution
  - Tone summary (if tone_score is filled)

Usage:
  python scripts/community_report.py [--out data/reports/community_report.md]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sqlite_util import sqlite_connect

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bot.db"


def get_conn() -> sqlite3.Connection:
    return sqlite_connect(DB_PATH)


def section(title: str, level: int = 2) -> str:
    return f"\n{'#' * level} {title}\n"


def generate_report() -> str:
    conn = get_conn()
    lines: list[str] = []

    # --- Basic stats ---
    total_msgs = conn.execute(
        "SELECT count(*) FROM messages WHERE text IS NOT NULL AND text != ''"
    ).fetchone()[0]
    total_users = conn.execute("SELECT count(DISTINCT user_id) FROM messages").fetchone()[0]
    total_chats = conn.execute("SELECT count(DISTINCT chat_id) FROM messages").fetchone()[0]
    date_range = conn.execute(
        "SELECT min(sent_at), max(sent_at) FROM messages"
    ).fetchone()
    total_edges = conn.execute("SELECT count(*) FROM edges").fetchone()[0]

    lines.append("# Community Report")
    lines.append(f"\n*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

    lines.append(section("Overview"))
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Messages | {total_msgs:,} |")
    lines.append(f"| Active users | {total_users} |")
    lines.append(f"| Chats | {total_chats} |")
    lines.append(f"| Social graph edges | {total_edges} |")
    lines.append(f"| Period | {date_range[0][:10] if date_range[0] else '?'} — {date_range[1][:10] if date_range[1] else '?'} |")

    if date_range[0] and date_range[1]:
        try:
            d0 = datetime.fromisoformat(date_range[0][:19])
            d1 = datetime.fromisoformat(date_range[1][:19])
            days = max(1, (d1 - d0).days)
            lines.append(f"| Duration | {days} days |")
            lines.append(f"| Avg messages/day | {total_msgs / days:.0f} |")
        except ValueError:
            pass

    # --- Opinion leaders ---
    lines.append(section("Opinion Leaders"))

    user_msg_counts = {}
    for row in conn.execute("""
        SELECT u.first_name, count(*) as cnt
        FROM messages m JOIN users u ON m.user_id = u.id
        GROUP BY m.user_id ORDER BY cnt DESC
    """).fetchall():
        user_msg_counts[row[0]] = row[1]

    # Build social graph for PageRank
    import networkx as nx
    G = nx.DiGraph()
    edge_rows = conn.execute("""
        SELECT e.from_user, e.to_user, e.weight, e.tone,
               u1.first_name as fn, u2.first_name as tn
        FROM edges e
        LEFT JOIN users u1 ON e.from_user = u1.id
        LEFT JOIN users u2 ON e.to_user = u2.id
    """).fetchall()

    uid_to_name = {}
    for row in edge_rows:
        fn = row[4] or str(row[0])
        tn = row[5] or str(row[1])
        uid_to_name[row[0]] = fn
        uid_to_name[row[1]] = tn
        G.add_edge(fn, tn, weight=row[2] or 1, tone=row[3] or "neutral")

    if G.number_of_nodes() > 0:
        pr = nx.pagerank(G, weight="weight")
        in_deg = dict(G.in_degree(weight="weight"))

        lines.append("**By PageRank** (influence in conversation graph):\n")
        lines.append("| # | User | PageRank | Messages | In-degree |")
        lines.append("|---|------|----------|----------|-----------|")
        for i, (name, score) in enumerate(sorted(pr.items(), key=lambda x: -x[1])[:10], 1):
            msgs_n = user_msg_counts.get(name, 0)
            indeg = in_deg.get(name, 0)
            lines.append(f"| {i} | {name} | {score:.4f} | {msgs_n:,} | {indeg:.0f} |")
    else:
        lines.append("*No social graph data available.*")

    # --- User Roles & Personalities ---
    lines.append(section("User Roles & Personalities"))

    profiles = conn.execute("""
        SELECT pp.user_id, u.first_name, pp.messages_analyzed,
               pp.confidence, pp.profile_json
        FROM personality_profiles pp
        LEFT JOIN users u ON pp.user_id = u.id
        WHERE pp.messages_analyzed > 0
        ORDER BY pp.messages_analyzed DESC
    """).fetchall()

    # Deduplicate by user_id (take highest messages_analyzed)
    seen_uids = set()
    unique_profiles = []
    for row in profiles:
        if row[0] not in seen_uids:
            seen_uids.add(row[0])
            unique_profiles.append(row)

    if unique_profiles:
        role_counts = Counter()
        lines.append("| User | Role | Style | Key Traits | Confidence |")
        lines.append("|------|------|-------|------------|------------|")

        for uid, name, msgs_analyzed, confidence, pj_raw in unique_profiles[:15]:
            try:
                pj = json.loads(pj_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            role = pj.get("role_in_community", "—")
            role_counts[role] += 1
            style = pj.get("communication", {}).get("style", "—")
            ocean = pj.get("ocean", {})
            traits = []
            if ocean.get("extraversion", 0.5) > 0.65:
                traits.append("extraverted")
            if ocean.get("agreeableness", 0.5) < 0.35:
                traits.append("confrontational")
            if ocean.get("neuroticism", 0.5) > 0.65:
                traits.append("emotional")
            if ocean.get("openness", 0.5) > 0.65:
                traits.append("curious")
            trait_str = ", ".join(traits) if traits else "balanced"
            lines.append(f"| {name or uid} | {role} | {style} | {trait_str} | {confidence:.0%} |")

        lines.append(f"\n**Role distribution**: {dict(role_counts)}")

    # --- Conflict & Toxic Edges ---
    lines.append(section("Conflict & Toxic Relationships"))

    conflict_edges = [(r[4] or str(r[0]), r[5] or str(r[1]), r[2], r[3])
                      for r in edge_rows if r[3] in ("conflict", "toxic")]

    if conflict_edges:
        conflict_edges.sort(key=lambda x: -(x[2] or 0))
        lines.append("| From | To | Weight | Tone |")
        lines.append("|------|----|--------|------|")
        for fn, tn, w, tone in conflict_edges[:15]:
            lines.append(f"| {fn} | {tn} | {w:.0f} | {tone} |")
        lines.append(f"\n*Total conflict/toxic edges: {len(conflict_edges)} out of {len(edge_rows)}*")
    else:
        lines.append("*No conflict or toxic edges detected.*")

    # --- Activity Trends & Churn ---
    lines.append(section("Activity Trends & Churn Signals"))

    weekly_activity = conn.execute("""
        SELECT u.first_name,
               strftime('%W', m.sent_at) as week,
               count(*) as cnt
        FROM messages m
        JOIN users u ON m.user_id = u.id
        GROUP BY m.user_id, week
        ORDER BY u.first_name, week
    """).fetchall()

    user_weeks: dict[str, dict[str, int]] = defaultdict(dict)
    all_weeks: set[str] = set()
    for name, week, cnt in weekly_activity:
        user_weeks[name][week] = cnt
        all_weeks.add(week)

    if len(all_weeks) >= 3:
        sorted_weeks = sorted(all_weeks)
        # Use second-to-last full week vs the one before it
        # (current week is likely incomplete)
        last_full = sorted_weeks[-2]
        prev_full = sorted_weeks[-3]

        churn_signals = []
        for name, weeks in user_weeks.items():
            total = sum(weeks.values())
            if total < 20:
                continue
            last_cnt = weeks.get(last_full, 0)
            prev_cnt = weeks.get(prev_full, 0)

            if prev_cnt >= 10 and last_cnt < prev_cnt * 0.3:
                churn_signals.append((name, prev_cnt, last_cnt, total))

        if churn_signals:
            lines.append("**Potential churn** (>70% drop from previous week):\n")
            lines.append("| User | Prev Week | Last Week | Total |")
            lines.append("|------|-----------|-----------|-------|")
            for name, prev, last, total in sorted(churn_signals, key=lambda x: -x[3]):
                lines.append(f"| {name} | {prev} | {last} | {total} |")
        else:
            lines.append("*No significant churn signals detected.*")

    # --- Topic Distribution ---
    lines.append(section("Topic Distribution"))

    all_topics: Counter = Counter()
    for row in edge_rows:
        # topics is JSON in the DB
        pass

    for uid, name, _, _, pj_raw in unique_profiles:
        try:
            pj = json.loads(pj_raw)
            for t in pj.get("topics", {}).get("primary", []):
                all_topics[t] += 1
            for t in pj.get("topics", {}).get("secondary", []):
                all_topics[t] += 1
        except (json.JSONDecodeError, TypeError):
            pass

    if all_topics:
        lines.append("| Topic | Users Interested |")
        lines.append("|-------|-----------------|")
        for topic, count in all_topics.most_common(15):
            lines.append(f"| {topic} | {count} |")

    # --- Tone Summary ---
    lines.append(section("Tone Summary"))

    tone_stats = conn.execute("""
        SELECT count(*),
               avg(tone_score),
               min(tone_score),
               max(tone_score)
        FROM messages WHERE tone_score IS NOT NULL
    """).fetchone()

    if tone_stats[0] and tone_stats[0] > 0:
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Scored messages | {tone_stats[0]:,} |")
        lines.append(f"| Average tone | {tone_stats[1]:.3f} |")
        lines.append(f"| Range | {tone_stats[2]:.2f} to {tone_stats[3]:.2f} |")

        tone_dist = conn.execute("""
            SELECT
                sum(CASE WHEN tone_score < -0.3 THEN 1 ELSE 0 END) as negative,
                sum(CASE WHEN tone_score BETWEEN -0.3 AND 0.3 THEN 1 ELSE 0 END) as neutral,
                sum(CASE WHEN tone_score > 0.3 THEN 1 ELSE 0 END) as positive
            FROM messages WHERE tone_score IS NOT NULL
        """).fetchone()

        total_scored = sum(tone_dist)
        lines.append(f"\n**Tone distribution**:")
        lines.append(f"- Negative (< -0.3): {tone_dist[0]:,} ({tone_dist[0]/total_scored*100:.1f}%)")
        lines.append(f"- Neutral (-0.3 to 0.3): {tone_dist[1]:,} ({tone_dist[1]/total_scored*100:.1f}%)")
        lines.append(f"- Positive (> 0.3): {tone_dist[2]:,} ({tone_dist[2]/total_scored*100:.1f}%)")

        # Top negative / positive users
        user_tones = conn.execute("""
            SELECT u.first_name, avg(m.tone_score) as avg_tone, count(*) as cnt
            FROM messages m JOIN users u ON m.user_id = u.id
            WHERE m.tone_score IS NOT NULL
            GROUP BY m.user_id
            HAVING cnt >= 20
            ORDER BY avg_tone
        """).fetchall()

        if user_tones:
            lines.append("\n**Most negative users** (by avg tone):")
            for name, avg_tone, cnt in user_tones[:5]:
                lines.append(f"- {name}: {avg_tone:.3f} ({cnt} msgs)")

            positive = [r for r in user_tones if r[1] > 0]
            if positive:
                lines.append("\n**Most positive users**:")
                for name, avg_tone, cnt in sorted(positive, key=lambda x: -x[1])[:5]:
                    lines.append(f"- {name}: {avg_tone:.3f} ({cnt} msgs)")
    else:
        lines.append("*No tone scores available yet. Run fill_tone_scores.py first.*")

    # --- Reply patterns ---
    lines.append(section("Reply Patterns"))

    reply_stats = conn.execute("""
        SELECT count(*) FROM messages WHERE replied_to IS NOT NULL
    """).fetchone()[0]

    lines.append(f"- Messages with replies: {reply_stats:,} ({reply_stats/total_msgs*100:.1f}% of total)")

    top_reply_pairs = conn.execute("""
        SELECT u1.first_name as replier, u2.first_name as replied_to, count(*) as cnt
        FROM messages m
        JOIN users u1 ON m.user_id = u1.id
        JOIN users u2 ON m.replied_to = u2.id
        WHERE m.replied_to IS NOT NULL
        GROUP BY m.user_id, m.replied_to
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()

    if top_reply_pairs:
        lines.append("\n**Top reply pairs**:\n")
        lines.append("| Replier | Replied To | Count |")
        lines.append("|---------|-----------|-------|")
        for replier, replied_to, cnt in top_reply_pairs:
            lines.append(f"| {replier} | {replied_to} | {cnt} |")

    conn.close()
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate community report")
    parser.add_argument("--out", default="data/reports/community_report.md",
                        help="Output file path")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    report = generate_report()
    out.write_text(report, encoding="utf-8")
    print(f"Report written to {out.resolve()}")
    print(f"Size: {len(report):,} chars")


if __name__ == "__main__":
    main()
