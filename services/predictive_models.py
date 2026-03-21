from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from services import marketing_metrics

_CHURN_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "churn_snapshots.json"


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


@dataclass
class _Forecast:
    current: float
    predicted: float
    slope: float
    confidence: float


def _linear_forecast(values: list[float], horizon_days: int) -> _Forecast:
    rows = [float(v or 0.0) for v in values if v is not None]
    if not rows:
        return _Forecast(current=0.0, predicted=0.0, slope=0.0, confidence=0.0)
    if len(rows) == 1:
        x = rows[-1]
        return _Forecast(current=x, predicted=x, slope=0.0, confidence=0.2)

    n = len(rows)
    x_vals = [float(i) for i in range(n)]
    y_vals = rows
    mean_x = sum(x_vals) / float(n)
    mean_y = sum(y_vals) / float(n)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals))
    den = sum((x - mean_x) ** 2 for x in x_vals)
    slope = (num / den) if den > 0 else 0.0
    intercept = mean_y - slope * mean_x
    target_x = (n - 1) + float(max(1, int(horizon_days or 7)))
    pred = intercept + slope * target_x
    current = float(rows[-1])
    confidence = min(1.0, 0.25 + (n / 40.0))
    return _Forecast(current=current, predicted=pred, slope=slope, confidence=confidence)


def _direction(current: float, predicted: float, eps: float = 0.015) -> str:
    delta = float(predicted) - float(current)
    if delta > eps:
        return "up"
    if delta < -eps:
        return "down"
    return "flat"


def _metrics_data() -> dict:
    try:
        return marketing_metrics._load_data()  # type: ignore[attr-defined]
    except Exception:
        return {"chats": {}}


def _chat_daily_rows(chat_id: int) -> list[tuple[str, dict]]:
    try:
        from services.storage_cutover import storage_db_reads_enabled

        if storage_db_reads_enabled():
            from services.marketing_metrics_db import chat_daily_series

            rows = chat_daily_series(chat_id=int(chat_id), lookback_days=120)
            if rows:
                return rows
    except Exception:
        pass
    data = _metrics_data()
    chat = ((data.get("chats") or {}).get(str(int(chat_id))) or {})
    daily = chat.get("chat_daily") or {}
    rows: list[tuple[str, dict]] = []
    for day, payload in daily.items():
        if not isinstance(payload, dict):
            continue
        rows.append((str(day), payload))
    rows.sort(key=lambda x: x[0])
    return rows


def _metrics_chat_ids_for_overview(limit: int = 40) -> list[int]:
    try:
        from services.storage_cutover import storage_db_reads_enabled

        if storage_db_reads_enabled():
            from db.sync_engine import sync_session_scope
            from services.marketing_metrics_db import all_distinct_chat_ids

            with sync_session_scope() as s:
                ids = all_distinct_chat_ids(s)
            return ids[: max(1, int(limit or 40))]
    except Exception:
        pass
    data = _metrics_data()
    out: list[int] = []
    for key in sorted((data.get("chats") or {}).keys()):
        if not str(key).lstrip("-").isdigit():
            continue
        out.append(int(key))
    return out[: max(1, int(limit or 40))]


def predict_toxicity(chat_id: int, *, horizon_days: int = 7, lookback_days: int = 30) -> dict:
    rows = _chat_daily_rows(chat_id)[-max(3, int(lookback_days or 30)) :]
    series: list[float] = []
    for _day, payload in rows:
        msg = float(payload.get("messages", 0.0) or 0.0)
        tox = float(payload.get("toxic_messages", 0.0) or 0.0)
        series.append(_clamp01(tox / max(1.0, msg)))
    fc = _linear_forecast(series, horizon_days)
    current = _clamp01(fc.current)
    predicted = _clamp01(fc.predicted)
    return {
        "signal": "toxicity",
        "horizon_days": int(max(1, int(horizon_days or 7))),
        "current": round(current, 4),
        "predicted": round(predicted, 4),
        "delta": round(predicted - current, 4),
        "direction": _direction(current, predicted),
        "confidence": round(float(fc.confidence), 4),
        "model": "linear_trend",
        "samples": len(series),
    }


def _load_churn_snapshots() -> list[dict]:
    if not _CHURN_SNAPSHOT_PATH.exists():
        return []
    try:
        payload = json.loads(_CHURN_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        rows = payload.get("snapshots") or []
        return [x for x in rows if isinstance(x, dict)]
    except Exception:
        return []


def predict_churn(chat_id: int | None, *, horizon_days: int = 7, lookback_days: int = 30) -> dict:
    rows = list(reversed(_load_churn_snapshots()))
    target_key = "all" if chat_id is None else str(int(chat_id))
    series: list[float] = []
    for row in rows:
        if str(row.get("chat_id")) != target_key:
            continue
        summary = row.get("summary") or {}
        users = float(summary.get("users_considered", summary.get("users_total", 0.0)) or 0.0)
        at_risk = float(summary.get("at_risk_count", 0.0) or 0.0)
        ratio = _clamp01(at_risk / max(1.0, users))
        series.append(ratio)
        if len(series) >= max(3, int(lookback_days or 30)):
            break
    if len(series) < 3:
        from services.recommendations import build_retention_dashboard

        dashboard = build_retention_dashboard(chat_id, days=30, limit=200)
        summary = dashboard.get("summary") or {}
        users = float(summary.get("users_considered", summary.get("users_total", 0.0)) or 0.0)
        at_risk = float(summary.get("at_risk_count", 0.0) or 0.0)
        current = _clamp01(at_risk / max(1.0, users))
        series = [current, current, current]
    series = list(reversed(series))
    fc = _linear_forecast(series, horizon_days)
    current = _clamp01(fc.current)
    predicted = _clamp01(fc.predicted)
    return {
        "signal": "churn_risk",
        "horizon_days": int(max(1, int(horizon_days or 7))),
        "current": round(current, 4),
        "predicted": round(predicted, 4),
        "delta": round(predicted - current, 4),
        "direction": _direction(current, predicted),
        "confidence": round(float(fc.confidence), 4),
        "model": "linear_trend",
        "samples": len(series),
    }


def predict_virality(chat_id: int, *, horizon_days: int = 7, lookback_days: int = 30) -> dict:
    rows = _chat_daily_rows(chat_id)[-max(3, int(lookback_days or 30)) :]
    msg_series: list[float] = []
    active_series: list[float] = []
    for _day, payload in rows:
        msg_series.append(float(payload.get("messages", 0.0) or 0.0))
        active_series.append(float(len(payload.get("active_users") or [])))
    if not msg_series:
        msg_series = [0.0, 0.0, 0.0]
    if not active_series:
        active_series = [0.0, 0.0, 0.0]
    msg_fc = _linear_forecast(msg_series, horizon_days)
    act_fc = _linear_forecast(active_series, horizon_days)
    curr_msg = max(0.0, msg_fc.current)
    pred_msg = max(0.0, msg_fc.predicted)
    curr_act = max(0.0, act_fc.current)
    pred_act = max(0.0, act_fc.predicted)
    msg_growth = (pred_msg - curr_msg) / max(1.0, curr_msg)
    act_growth = (pred_act - curr_act) / max(1.0, curr_act)
    current = _clamp01((curr_msg / (curr_msg + 40.0)) * 0.6 + (curr_act / (curr_act + 20.0)) * 0.4)
    predicted = _clamp01(current + (msg_growth * 0.25) + (act_growth * 0.2))
    confidence = min(1.0, (msg_fc.confidence + act_fc.confidence) / 2.0)
    return {
        "signal": "virality",
        "horizon_days": int(max(1, int(horizon_days or 7))),
        "current": round(current, 4),
        "predicted": round(predicted, 4),
        "delta": round(predicted - current, 4),
        "direction": _direction(current, predicted),
        "confidence": round(float(confidence), 4),
        "model": "linear_trend_proxy",
        "samples": max(len(msg_series), len(active_series)),
    }


def predict_overview(chat_id: int | None, *, horizon_days: int = 7, lookback_days: int = 30) -> dict:
    if chat_id is None:
        churn = predict_churn(None, horizon_days=horizon_days, lookback_days=lookback_days)
        # For global mode, virality/toxicity use weighted average over known chats.
        chat_ids = _metrics_chat_ids_for_overview(40)
        toxicity_rows = []
        virality_rows = []
        for cid in chat_ids:
            toxicity_rows.append(predict_toxicity(cid, horizon_days=horizon_days, lookback_days=lookback_days))
            virality_rows.append(predict_virality(cid, horizon_days=horizon_days, lookback_days=lookback_days))
        if toxicity_rows:
            t_current = sum(float(x["current"]) for x in toxicity_rows) / len(toxicity_rows)
            t_pred = sum(float(x["predicted"]) for x in toxicity_rows) / len(toxicity_rows)
            toxicity = {
                "signal": "toxicity",
                "horizon_days": int(horizon_days),
                "current": round(t_current, 4),
                "predicted": round(t_pred, 4),
                "delta": round(t_pred - t_current, 4),
                "direction": _direction(t_current, t_pred),
                "confidence": round(sum(float(x["confidence"]) for x in toxicity_rows) / len(toxicity_rows), 4),
                "model": "mean_chat_linear_trend",
                "samples": len(toxicity_rows),
            }
        else:
            toxicity = predict_toxicity(0, horizon_days=horizon_days, lookback_days=lookback_days)
        if virality_rows:
            v_current = sum(float(x["current"]) for x in virality_rows) / len(virality_rows)
            v_pred = sum(float(x["predicted"]) for x in virality_rows) / len(virality_rows)
            virality = {
                "signal": "virality",
                "horizon_days": int(horizon_days),
                "current": round(v_current, 4),
                "predicted": round(v_pred, 4),
                "delta": round(v_pred - v_current, 4),
                "direction": _direction(v_current, v_pred),
                "confidence": round(sum(float(x["confidence"]) for x in virality_rows) / len(virality_rows), 4),
                "model": "mean_chat_linear_trend",
                "samples": len(virality_rows),
            }
        else:
            virality = predict_virality(0, horizon_days=horizon_days, lookback_days=lookback_days)
        cid_out = "all"
    else:
        cid = int(chat_id)
        churn = predict_churn(cid, horizon_days=horizon_days, lookback_days=lookback_days)
        toxicity = predict_toxicity(cid, horizon_days=horizon_days, lookback_days=lookback_days)
        virality = predict_virality(cid, horizon_days=horizon_days, lookback_days=lookback_days)
        cid_out = cid
    return {
        "chat_id": cid_out,
        "horizon_days": int(max(1, int(horizon_days or 7))),
        "lookback_days": int(max(7, int(lookback_days or 30))),
        "signals": {
            "churn_risk": churn,
            "toxicity": toxicity,
            "virality": virality,
        },
    }
