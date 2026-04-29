"""Геймификация спарринга: пересчёт статов из БД (messages, marketing_signal_events, personality_profiles)."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, cast, delete, func, select
from sqlalchemy.orm import Session

from db.models import (
    MarketingSignalEvent,
    Message,
    PersonalityProfileRow,
    SparringWeeklyFighter,
    User,
)


def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def default_week_start() -> date:
    return monday_of_week(date.today())


def ensure_sparring_tables() -> None:
    from db.models import Base
    from db.sync_engine import get_sync_engine

    Base.metadata.create_all(bind=get_sync_engine(), tables=[SparringWeeklyFighter.__table__])


def _percentile_rank(sorted_vals: list[float], val: float) -> int:
    if not sorted_vals:
        return 50
    if len(sorted_vals) == 1:
        return 55
    rank = sum(1 for x in sorted_vals if x < val)
    return int(round(10 + 90 * rank / (len(sorted_vals) - 1)))


def _stable_int_seed(parts: tuple[Any, ...]) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:12], 16)


def _rand01(seed: bytes, round_i: int, tag: bytes) -> float:
    h = hashlib.sha256(seed + struct.pack("I", round_i) + tag).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def _latest_personality_rows(session: Session, chat_id: int) -> dict[int, dict[str, Any]]:
    """Последняя запись personality_profiles на пару (user_id, chat_id)."""
    sub = (
        select(
            PersonalityProfileRow.user_id.label("uid"),
            func.max(PersonalityProfileRow.generated_at).label("mx"),
        )
        .where(PersonalityProfileRow.chat_id == chat_id)
        .group_by(PersonalityProfileRow.user_id)
        .subquery()
    )
    stmt = select(PersonalityProfileRow).join(
        sub,
        (PersonalityProfileRow.user_id == sub.c.uid)
        & (PersonalityProfileRow.generated_at == sub.c.mx),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in session.execute(stmt).scalars():
        pj = row.profile_json if isinstance(row.profile_json, dict) else {}
        out[int(row.user_id)] = pj
    return out


def recompute_week(session: Session, chat_id: int, week_start: date) -> int:
    """Пересчитать строки sparring_weekly_fighters за календарную неделю [week_start, week_start+7)."""
    ws = monday_of_week(week_start)
    start_dt = datetime(ws.year, ws.month, ws.day)
    end_dt = start_dt + timedelta(days=7)

    session.execute(
        delete(SparringWeeklyFighter).where(
            SparringWeeklyFighter.week_start == ws,
            SparringWeeklyFighter.chat_id == chat_id,
        )
    )

    msg_stmt = (
        select(
            Message.user_id,
            func.count(Message.id).label("cnt"),
            func.count(func.distinct(cast(Message.sent_at, Date))).label("days"),
            func.avg(func.abs(Message.tone_score)).label("avg_abs_tone"),
        )
        .where(
            Message.chat_id == chat_id,
            Message.user_id.isnot(None),
            Message.sent_at >= start_dt,
            Message.sent_at < end_dt,
        )
        .group_by(Message.user_id)
    )
    rows = session.execute(msg_stmt).all()
    if not rows:
        return 0

    user_ids = [int(r[0]) for r in rows]
    political_map: dict[int, int] = {uid: 0 for uid in user_ids}
    try:
        pol_stmt = (
            select(MarketingSignalEvent.user_id, func.count(MarketingSignalEvent.id))
            .where(
                MarketingSignalEvent.chat_id == chat_id,
                MarketingSignalEvent.user_id.in_(user_ids),
                MarketingSignalEvent.occurred_at >= start_dt,
                MarketingSignalEvent.occurred_at < end_dt,
                MarketingSignalEvent.is_political.is_(True),
            )
            .group_by(MarketingSignalEvent.user_id)
        )
        for uid, c in session.execute(pol_stmt).all():
            political_map[int(uid)] = int(c or 0)
    except Exception:
        pass

    personality = _latest_personality_rows(session, chat_id)

    raw_power: dict[int, float] = {}
    raw_defense: dict[int, float] = {}
    raw_speed: dict[int, float] = {}
    raw_accuracy: dict[int, float] = {}
    raw_charisma: dict[int, float] = {}
    meta: dict[int, dict[str, Any]] = {}

    for r in rows:
        uid = int(r[0])
        cnt = int(r[1] or 0)
        days = max(1, int(r[2] or 1))
        avg_abs_tone = float(r[3] or 0.0)
        pol = political_map.get(uid, 0)
        pj = personality.get(uid) or {}
        ocean = pj.get("ocean") if isinstance(pj.get("ocean"), dict) else {}
        conf = float(pj.get("confidence") or 0.0) if pj else 0.0
        extra = float(ocean.get("extraversion", 0.5) or 0.5)
        agree = float(ocean.get("agreeableness", 0.5) or 0.5)
        neur = float(ocean.get("neuroticism", 0.5) or 0.5)

        raw_power[uid] = float(cnt) + 2.0 * float(pol)
        raw_speed[uid] = float(cnt) / float(days)
        raw_accuracy[uid] = conf if conf > 0 else min(1.0, float(cnt) / 50.0)
        raw_charisma[uid] = (extra + agree) / 2.0

        if ocean:
            raw_defense[uid] = (agree + (1.0 - neur)) / 2.0
        else:
            raw_defense[uid] = 1.0 / (1.0 + avg_abs_tone)

        meta[uid] = {
            "cnt": cnt,
            "pol": pol,
            "days": days,
            "conf": conf,
        }

    sp = sorted(raw_power.values())
    sd = sorted(raw_defense.values())
    ss = sorted(raw_speed.values())
    sa = sorted(raw_accuracy.values())
    sc = sorted(raw_charisma.values())

    now = datetime.utcnow()
    n_insert = 0
    for uid in user_ids:
        m = meta[uid]
        luck = 10 + (_stable_int_seed((ws.isoformat(), chat_id, uid)) % 86)
        body_variant = _stable_int_seed((uid, chat_id, "body")) % 4
        tint_hue = _stable_int_seed((uid, "hue")) % 360
        fighter = SparringWeeklyFighter(
            week_start=ws,
            user_id=uid,
            chat_id=chat_id,
            stat_power=_percentile_rank(sp, raw_power[uid]),
            stat_defense=_percentile_rank(sd, raw_defense[uid]),
            stat_speed=_percentile_rank(ss, raw_speed[uid]),
            stat_accuracy=_percentile_rank(sa, raw_accuracy[uid]),
            stat_charisma=_percentile_rank(sc, raw_charisma[uid]),
            stat_luck=min(99, max(10, luck)),
            body_variant=int(body_variant),
            tint_hue=int(tint_hue),
            message_count=int(m["cnt"]),
            political_hits=int(m["pol"]),
            active_days=int(m["days"]),
            personality_confidence=float(m["conf"]) if m["conf"] and m["conf"] > 0 else None,
            computed_at=now,
        )
        session.add(fighter)
        n_insert += 1
    return n_insert


def load_roster(session: Session, chat_id: int, week_start: date) -> list[SparringWeeklyFighter]:
    ws = monday_of_week(week_start)
    stmt = (
        select(SparringWeeklyFighter)
        .where(SparringWeeklyFighter.chat_id == chat_id, SparringWeeklyFighter.week_start == ws)
        .order_by(SparringWeeklyFighter.stat_power.desc(), SparringWeeklyFighter.user_id)
    )
    return list(session.execute(stmt).scalars().all())


def fighter_to_api_dict(session: Session, f: SparringWeeklyFighter) -> dict[str, Any]:
    display = str(f.user_id)
    u = session.get(User, f.user_id)
    if u:
        parts = [p for p in (u.first_name or "", u.last_name or "") if p]
        if parts:
            display = " ".join(parts).strip()
        elif u.username:
            display = "@" + str(u.username).lstrip("@")
    return {
        "user_id": f.user_id,
        "chat_id": f.chat_id,
        "week_start": f.week_start.isoformat() if f.week_start else None,
        "display_name": display,
        "stats": {
            "power": f.stat_power,
            "defense": f.stat_defense,
            "speed": f.stat_speed,
            "accuracy": f.stat_accuracy,
            "charisma": f.stat_charisma,
            "luck": f.stat_luck,
        },
        "body_variant": f.body_variant,
        "tint_hue": f.tint_hue,
        "message_count": f.message_count,
        "political_hits": f.political_hits,
        "active_days": f.active_days,
    }


@dataclass
class FightResult:
    winner_user_id: int
    loser_user_id: int
    rounds: list[dict[str, Any]]
    hp_max: int
    rage_max: int = 100
    stamina_max: int = 100
    weaker_user_id: int | None = None
    underdog_fortune: bool = False


# До 20 обменов; HP=160. С добавлением dodge/parry бой может закончиться раньше — это ок.
_FIGHT_HP_MAX = 160
_FIGHT_MAX_ROUNDS = 20
# Минимальный, но ненулевой шанс «фортуны» для суммарно более слабого бойца (детерминированно от seed пары).
_UNDERDOG_FORTUNE_CHANCE = 0.05
_WEAKER_OUTGOING_MULT = 1.14
_WEAKER_INCOMING_MULT = 0.88


def _fighter_total_stats(f: SparringWeeklyFighter) -> int:
    return (
        f.stat_power
        + f.stat_defense
        + f.stat_speed
        + f.stat_accuracy
        + f.stat_charisma
        + f.stat_luck
    )


def _fatigue01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# Усталость 0..1: снижает эффективную атаку и защиту в бою (турниры с несколькими матчами подряд).
_FATIGUE_ATK_FACTOR = 0.34
_FATIGUE_DEF_FACTOR = 0.28
_FATIGUE_MIN_ATK_RATIO = 0.58
_FATIGUE_MIN_DEF_RATIO = 0.62

# После каждого сыгранного матча в плей-офф (не bye).
_ELIMINATION_FATIGUE_STEP = 0.16
# Харизма (0..99) снижает накопление усталости: до -40% при максимальной харизме.
_CHARISMA_RECOVERY_FACTOR = 0.40

# Ярость 0.._RAGE_MAX: копится от своих атак и от полученных ударов; при полной — следующий удар этого бойца — удар ярости.
_RAGE_MAX = 100
_RAGE_GAIN_ATTACKER = 20
_RAGE_GAIN_DEFENDER_ON_HIT = 17
_RAGE_STRIKE_MULT_LO = 2.05
_RAGE_STRIKE_MULT_HI = 2.72
_RAGE_STRIKE_MIN = 22
_RAGE_STRIKE_CAP = 78
_RAGE_DEF_EXTRA_ON_HEAVY = 12
_RAGE_HEAVY_DAMAGE = 48
# Боец с меньшей суммой статов копит ярость быстрее: множитель до потолка, пропорционально отношению сумм.
_RAGE_UNDERDOG_MULT_CAP = 1.9

# Стамина 0..100 — отдельная шкала постуры. Тратится на каждой атаке/защите; при <25% эффективная атака/защита снижается.
_STAMINA_MAX = 100
_STAMINA_LOW_THRESHOLD = 25
_STAMINA_LOW_ATK_MULT = 0.70
_STAMINA_LOW_DEF_MULT = 0.85
_STAMINA_REGEN_PER_ROUND = 6
_STAMINA_DRAIN_DEFENDER_HIT = 8
# Затраты по типу атаки (берутся при выборе типа). Тяжёлый — больно платит.
_STAMINA_COST = {"light": 9, "heavy": 22, "combo": 17, "sweep": 14, "rage": 0}

# Типы атак, кроме ярости. Базовая light — punch/kick (старая логика).
# Шансы выбора (детерминированно от seed раунда) с биасом от статов.
_ATK_BASE_WEIGHTS = {"light": 60, "heavy": 15, "combo": 12, "sweep": 13}
# Модификаторы урона относительно light:
_ATK_DMG_MULT = {"light": 1.0, "heavy": 1.4, "combo_a": 0.4, "combo_b": 0.6, "sweep": 0.0}
# Точность для каждого типа (множитель к accuracy/200 для крита и dodge).
_ATK_ACC_MULT = {"light": 1.0, "heavy": 0.75, "combo": 0.95, "sweep": 1.10}
# Sweep вытягивает стамину защитника.
_SWEEP_STAMINA_DRAIN = 30

# Уклонение и парирование.
_DODGE_BASE = 0.32   # коэффициент при luck/accuracy
_DODGE_CAP = 0.32    # максимальный шанс уклонения
_PARRY_BASE = 0.45   # коэффициент при разнице accuracy
_PARRY_CAP = 0.35
_PARRY_COUNTER_FACTOR = 0.55  # доля собственного света защитника как ответный урон
_CRIT_FATIGUE_BONUS = 0.15    # +до 15% к шансу крита, если у защитника <100 стамины

# Второе дыхание — раз за бой: спасает от смертельного удара до 1 HP, восстанавливает стамину/ярость.
_SECOND_WIND_HP_TRIGGER = 20
_SECOND_WIND_STAMINA_RESTORE = 60
_SECOND_WIND_RAGE_BOOST = 30
_SECOND_WIND_MIN_REMAINING_ROUNDS = 3  # активируется только если до конца ещё ≥N раундов

# «Раз за бой удача» — 1 раз отменяет смерть до 1HP даже без второго дыхания (если уже было) при luck>=70.
_LUCK_SAVE_THRESHOLD = 70
_LUCK_SAVE_CHANCE = 0.55


def _rage_gain_mult_for(my_total: int, opp_total: int) -> float:
    if my_total <= 0:
        return 1.0
    if my_total >= opp_total:
        return 1.0
    return min(_RAGE_UNDERDOG_MULT_CAP, float(opp_total) / float(my_total))


def _pick_attack_type(fa: SparringWeeklyFighter, seed: bytes, r: int) -> str:
    """Детерминированный выбор типа атаки с биасом от статов бойца.

    Биасы:
      - power+charisma высокие → больше heavy/combo
      - speed высокий → больше combo и light
      - defense+accuracy высокие → больше sweep (тактический)
    """
    aggression = (fa.stat_power + fa.stat_charisma) / 200.0  # 0..1 примерно
    discipline = (fa.stat_defense + fa.stat_accuracy) / 200.0
    speed_norm = fa.stat_speed / 100.0
    weights = dict(_ATK_BASE_WEIGHTS)
    weights["heavy"] = int(round(weights["heavy"] * (0.6 + aggression * 1.4)))
    weights["combo"] = int(round(weights["combo"] * (0.7 + speed_norm * 1.2)))
    weights["sweep"] = int(round(weights["sweep"] * (0.6 + discipline * 1.4)))
    weights["light"] = max(20, weights["light"])
    total = sum(weights.values())
    roll = _rand01(seed, r, b"atk_type") * total
    acc = 0
    for k, w in weights.items():
        acc += w
        if roll <= acc:
            return k
    return "light"


def simulate_fight(
    a: SparringWeeklyFighter,
    b: SparringWeeklyFighter,
    *,
    fatigue: dict[int, float] | None = None,
    match_tag: bytes = b"",
) -> FightResult:
    """
    Боевой движок с механиками: stamina, dodge, parry/counter, типы атак,
    crit-from-fatigue, second-wind. Раунды по-прежнему чередуют A,B,A,B…,
    но раунд может быть «промахом» (dodged) или «парированием+контрударом»
    (parried). Поля раунда обратно совместимы (i, attacker, defender,
    damage, crit, rage, hp_after, rage_after) + новые: attack_type, dodged,
    parried, parry_damage, stamina_after, second_wind, chance_dodge,
    chance_parry, chance_crit.
    """
    if a.user_id == b.user_id:
        raise ValueError("same fighter")
    ua, ub = int(a.user_id), int(b.user_id)
    fat_a = _fatigue01(fatigue.get(ua, 0.0)) if fatigue else 0.0
    fat_b = _fatigue01(fatigue.get(ub, 0.0)) if fatigue else 0.0
    use_fatigue_seed = (fat_a > 0.0 or fat_b > 0.0 or bool(match_tag))
    if use_fatigue_seed:
        seed = hashlib.sha256(
            f"{a.week_start}|{a.chat_id}|{min(ua, ub)}|{max(ua, ub)}|{fat_a:.5f}|{fat_b:.5f}|".encode()
            + match_tag
        ).digest()
    else:
        seed = hashlib.sha256(
            f"{a.week_start}|{a.chat_id}|{min(ua, ub)}|{max(ua, ub)}".encode()
        ).digest()
    ta, tb = _fighter_total_stats(a), _fighter_total_stats(b)
    weaker_uid: int | None
    if ta < tb:
        weaker_uid = ua
    elif tb < ta:
        weaker_uid = ub
    else:
        weaker_uid = None
    underdog_fortune = weaker_uid is not None and _rand01(seed, 1001, b"underdog_fortune") < _UNDERDOG_FORTUNE_CHANCE

    rage_m_a = _rage_gain_mult_for(ta, tb)
    rage_m_b = _rage_gain_mult_for(tb, ta)

    hp = {ua: _FIGHT_HP_MAX, ub: _FIGHT_HP_MAX}
    rage: dict[int, int] = {ua: 0, ub: 0}
    stamina: dict[int, int] = {ua: _STAMINA_MAX, ub: _STAMINA_MAX}
    second_wind_used: dict[int, bool] = {ua: False, ub: False}
    luck_save_used: dict[int, bool] = {ua: False, ub: False}
    order = [ua, ub]
    rounds: list[dict[str, Any]] = []

    def _maybe_save_from_ko(target_uid: int, target_f: SparringWeeklyFighter, r_idx: int, incoming_dmg: int) -> tuple[int, bool, bool]:
        """Возвращает (final_dmg, second_wind_triggered, luck_save_triggered)."""
        new_hp = hp[target_uid] - incoming_dmg
        if new_hp > 0:
            return incoming_dmg, False, False
        # 1) Второе дыхание (раз за бой, если до конца ≥N раундов).
        rounds_left = _FIGHT_MAX_ROUNDS - r_idx - 1
        if (
            not second_wind_used[target_uid]
            and rounds_left >= _SECOND_WIND_MIN_REMAINING_ROUNDS
            and hp[target_uid] >= _SECOND_WIND_HP_TRIGGER
        ):
            second_wind_used[target_uid] = True
            stamina[target_uid] = max(stamina[target_uid], _SECOND_WIND_STAMINA_RESTORE)
            rage[target_uid] = min(_RAGE_MAX, rage[target_uid] + _SECOND_WIND_RAGE_BOOST)
            saved_dmg = max(0, hp[target_uid] - 1)
            return saved_dmg, True, False
        # 2) Удача (если luck высокая, раз за бой, шанс _LUCK_SAVE_CHANCE).
        if (
            not luck_save_used[target_uid]
            and target_f.stat_luck >= _LUCK_SAVE_THRESHOLD
            and _rand01(seed, r_idx, b"luck_save") < _LUCK_SAVE_CHANCE
        ):
            luck_save_used[target_uid] = True
            saved_dmg = max(0, hp[target_uid] - 1)
            return saved_dmg, False, True
        return incoming_dmg, False, False

    for r in range(_FIGHT_MAX_ROUNDS):
        attacker = order[r % 2]
        defender = order[(r + 1) % 2]
        fa = a if attacker == ua else b
        fd = a if defender == ua else b
        f_atk = fat_a if attacker == ua else fat_b
        f_def = fat_a if defender == ua else fat_b

        # Регенерация стамины перед действиями (медленно отрастает между ударами).
        stamina[attacker] = min(_STAMINA_MAX, stamina[attacker] + _STAMINA_REGEN_PER_ROUND)
        stamina[defender] = min(_STAMINA_MAX, stamina[defender] + _STAMINA_REGEN_PER_ROUND)

        is_rage = rage[attacker] >= _RAGE_MAX
        if is_rage:
            attack_type = "rage"
        else:
            attack_type = _pick_attack_type(fa, seed, r)
        # Если стамины не хватает на тяжёлый/комбо — даунгрейд в light.
        if not is_rage and stamina[attacker] < _STAMINA_COST.get(attack_type, 0):
            attack_type = "light"
        # Затраты стамины атакующего (rage не тратит — это «ультимэйт»).
        stamina[attacker] = max(0, stamina[attacker] - _STAMINA_COST.get(attack_type, 0))

        # Эффективные модификаторы от стамины и усталости.
        atk_low_stam = stamina[attacker] < _STAMINA_LOW_THRESHOLD
        def_low_stam = stamina[defender] < _STAMINA_LOW_THRESHOLD
        atk_stam_mult = _STAMINA_LOW_ATK_MULT if atk_low_stam else 1.0
        def_stam_mult = _STAMINA_LOW_DEF_MULT if def_low_stam else 1.0

        atk_stat = (fa.stat_power + fa.stat_speed) / 2.0 * max(
            _FATIGUE_MIN_ATK_RATIO, 1.0 - _FATIGUE_ATK_FACTOR * f_atk
        ) * atk_stam_mult
        def_stat = (fd.stat_defense + fd.stat_speed / 2.0) * max(
            _FATIGUE_MIN_DEF_RATIO, 1.0 - _FATIGUE_DEF_FACTOR * f_def
        ) * def_stam_mult

        # --- DODGE: уклонение защитника (только не в рейдже атакующего). ---
        dodge_chance = 0.0
        dodged = False
        if not is_rage:
            acc_for_type = (fa.stat_accuracy / 130.0) * _ATK_ACC_MULT.get(attack_type, 1.0)
            dodge_chance = max(
                0.0,
                min(_DODGE_CAP, (fd.stat_luck / 100.0) * (1.0 - acc_for_type) * _DODGE_BASE),
            )
            dodge_roll = _rand01(seed, r, b"dodge")
            dodged = dodge_roll < dodge_chance
        if dodged:
            rounds.append({
                "i": r + 1,
                "attacker": attacker,
                "defender": defender,
                "damage": 0,
                "crit": False,
                "rage": False,
                "dodged": True,
                "parried": False,
                "parry_damage": 0,
                "attack_type": attack_type,
                "hp_after": dict(hp),
                "rage_after": dict(rage),
                "stamina_after": dict(stamina),
                "second_wind": {},
                "chance_dodge": round(dodge_chance, 3),
                "chance_parry": 0.0,
                "chance_crit": 0.0,
            })
            continue

        # --- PARRY: парирование (только light/sweep, не rage/heavy/combo). ---
        parry_chance = 0.0
        parried = False
        parry_damage = 0
        if not is_rage and attack_type in ("light", "sweep"):
            acc_diff = (fd.stat_accuracy / 100.0) - (fa.stat_accuracy / 130.0)
            disc = ((fd.stat_defense + fd.stat_accuracy) / 200.0) - 0.5
            parry_chance = max(0.0, min(_PARRY_CAP, _PARRY_BASE * acc_diff + 0.18 * disc))
            parry_roll = _rand01(seed, r, b"parry")
            parried = parry_roll < parry_chance
        if parried:
            counter_base = (fd.stat_power + fd.stat_speed) / 2.0
            parry_damage = max(3, int(round(counter_base * 0.06 * _PARRY_COUNTER_FACTOR + 4)))
            # Контрудар наносится атакующему (тот, кто пытался ударить).
            applied_dmg, sw_trig, lk_trig = _maybe_save_from_ko(attacker, fa, r, parry_damage)
            hp[attacker] = max(0, hp[attacker] - applied_dmg)
            stamina[attacker] = max(0, stamina[attacker] - _STAMINA_DRAIN_DEFENDER_HIT)
            rage[defender] = min(_RAGE_MAX, rage[defender] + _RAGE_GAIN_ATTACKER)
            rounds.append({
                "i": r + 1,
                "attacker": attacker,
                "defender": defender,
                "damage": 0,
                "crit": False,
                "rage": False,
                "dodged": False,
                "parried": True,
                "parry_damage": applied_dmg,
                "attack_type": attack_type,
                "hp_after": dict(hp),
                "rage_after": dict(rage),
                "stamina_after": dict(stamina),
                "second_wind": ({str(attacker): True} if sw_trig else {}) | ({str(attacker): "luck"} if lk_trig else {}),
                "chance_dodge": round(dodge_chance, 3),
                "chance_parry": round(parry_chance, 3),
                "chance_crit": 0.0,
            })
            if hp[attacker] <= 0:
                break
            continue

        # --- HIT: расчёт урона ---
        luck_roll = (fa.stat_luck / 100.0) * _rand01(seed, r, b"luck")
        acc_roll = fa.stat_accuracy / 100.0
        base = atk_stat * (0.5 + 0.45 * acc_roll) * (0.78 + 0.35 * luck_roll)
        mitigation = 0.48 + 0.52 * (def_stat / 100.0)
        raw_dmg = int(round((base * 0.16) / mitigation))
        raw_dmg = max(4, min(28, raw_dmg))
        # Множитель типа атаки.
        type_mult = _ATK_DMG_MULT.get(attack_type, 1.0) if attack_type != "rage" else 1.0
        if attack_type == "sweep":
            raw_dmg = 0  # подсечка — без урона
        elif attack_type == "heavy":
            raw_dmg = int(round(raw_dmg * type_mult))
        elif attack_type == "combo":
            # Сейчас наносим первый удар (40%), затем второй (60%) — записываем как один раунд с суммой.
            raw_dmg = int(round(raw_dmg * (_ATK_DMG_MULT["combo_a"] + _ATK_DMG_MULT["combo_b"])))

        # --- CRIT: с бонусом от низкой стамины защитника ---
        crit_chance = (fa.stat_accuracy / 200.0) * _ATK_ACC_MULT.get(attack_type, 1.0)
        crit_chance += max(0.0, (1.0 - stamina[defender] / 100.0)) * _CRIT_FATIGUE_BONUS
        crit_chance = max(0.0, min(0.6, crit_chance))
        is_crit = _rand01(seed, r, b"crit") < crit_chance
        if is_crit and raw_dmg > 0:
            raw_dmg = min(40, int(round(raw_dmg * 1.28)))

        if is_rage:
            mult = _RAGE_STRIKE_MULT_LO + (_RAGE_STRIKE_MULT_HI - _RAGE_STRIKE_MULT_LO) * _rand01(
                seed, r, b"rage_m"
            )
            dmg = int(round(raw_dmg * mult)) if raw_dmg > 0 else _RAGE_STRIKE_MIN
            if is_crit:
                dmg = int(round(dmg * 1.12))
            dmg = max(_RAGE_STRIKE_MIN, min(_RAGE_STRIKE_CAP, dmg))
        else:
            dmg = raw_dmg

        if underdog_fortune and weaker_uid is not None:
            if attacker == weaker_uid:
                dmg = int(round(dmg * _WEAKER_OUTGOING_MULT))
            if defender == weaker_uid:
                dmg = int(round(dmg * _WEAKER_INCOMING_MULT))
            if is_rage:
                dmg = max(10, min(92, dmg))
            else:
                dmg = max(0, min(40, dmg))

        # --- Применение урона (с проверкой second-wind / luck-save) ---
        applied_dmg, sw_trig, lk_trig = _maybe_save_from_ko(defender, fd, r, dmg)
        hp[defender] = max(0, hp[defender] - applied_dmg)

        # Стамина: защитник теряет от удара; на sweep — особый дрейн.
        if attack_type == "sweep":
            stamina[defender] = max(0, stamina[defender] - _SWEEP_STAMINA_DRAIN)
        elif applied_dmg > 0:
            stamina[defender] = max(0, stamina[defender] - _STAMINA_DRAIN_DEFENDER_HIT)

        # Ярость
        m_atk = rage_m_a if attacker == ua else rage_m_b
        m_def = rage_m_a if defender == ua else rage_m_b
        gain_atk = max(1, int(round(_RAGE_GAIN_ATTACKER * m_atk)))
        gain_def = max(1, int(round(_RAGE_GAIN_DEFENDER_ON_HIT * m_def)))
        if is_rage:
            rage[attacker] = 0
        else:
            rage[attacker] = min(_RAGE_MAX, rage[attacker] + gain_atk)
        if applied_dmg > 0:
            rage[defender] = min(_RAGE_MAX, rage[defender] + gain_def)
        if is_rage and dmg >= _RAGE_HEAVY_DAMAGE:
            extra_def = max(1, int(round(_RAGE_DEF_EXTRA_ON_HEAVY * m_def)))
            rage[defender] = min(_RAGE_MAX, rage[defender] + extra_def)

        sw_record: dict[str, Any] = {}
        if sw_trig:
            sw_record[str(defender)] = True
        if lk_trig:
            sw_record[str(defender)] = "luck"

        rounds.append({
            "i": r + 1,
            "attacker": attacker,
            "defender": defender,
            "damage": applied_dmg,
            "crit": is_crit,
            "rage": is_rage,
            "dodged": False,
            "parried": False,
            "parry_damage": 0,
            "attack_type": attack_type,
            "hp_after": dict(hp),
            "rage_after": dict(rage),
            "stamina_after": dict(stamina),
            "second_wind": sw_record,
            "chance_dodge": round(dodge_chance, 3),
            "chance_parry": round(parry_chance, 3),
            "chance_crit": round(crit_chance, 3),
        })
        if hp[defender] <= 0:
            break

    if hp[ua] == hp[ub]:
        winner = ua if _rand01(seed, 999, b"tie") < 0.5 else ub
    else:
        winner = ua if hp[ua] > hp[ub] else ub
    loser = ub if winner == ua else ua
    return FightResult(
        winner_user_id=winner,
        loser_user_id=loser,
        rounds=rounds,
        hp_max=_FIGHT_HP_MAX,
        rage_max=_RAGE_MAX,
        stamina_max=_STAMINA_MAX,
        weaker_user_id=weaker_uid,
        underdog_fortune=underdog_fortune,
    )


def _unique_ids_preserve_order(raw: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for x in raw:
        uid = int(x)
        if uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def _deterministic_shuffle(items: list[int], seed_bytes: bytes) -> list[int]:
    arr = items[:]
    for i in range(len(arr) - 1, 0, -1):
        j = int(_rand01(seed_bytes, i, b"shuffle" + bytes([i % 256])) * (i + 1))
        arr[i], arr[j] = arr[j], arr[i]
    return arr


def run_elimination_tournament(
    session: Session,
    chat_id: int,
    week_start: date,
    participant_user_ids: list[int],
) -> dict[str, Any]:
    """
    Олимпийская сетка на выбывание: детерминированный жребий, пары подряд, нечётный — пропуск (bye).
    После каждого боя оба участника получают +_ELIMINATION_FATIGUE_STEP к усталости (0..1), влияет на следующие бои.
    """
    ws = monday_of_week(week_start)
    ids = _unique_ids_preserve_order([int(x) for x in participant_user_ids])
    if len(ids) < 2:
        return {"error": "Нужно минимум два разных участника", "rounds": [], "champion_user_id": None}

    roster = load_roster(session, chat_id, week_start)
    by_uid: dict[int, SparringWeeklyFighter] = {int(f.user_id): f for f in roster}
    missing = [uid for uid in ids if uid not in by_uid]
    if missing:
        return {
            "error": f"Нет статов за эту неделю для user_id: {missing[:8]}{'…' if len(missing) > 8 else ''}",
            "rounds": [],
            "champion_user_id": None,
        }

    seed_shuffle = hashlib.sha256(
        (f"elim-shuffle|{ws.isoformat()}|{chat_id}|" + ",".join(str(x) for x in sorted(ids))).encode()
    ).digest()
    order = _deterministic_shuffle(ids, seed_shuffle)
    fatigue: dict[int, float] = {uid: 0.0 for uid in order}
    display_cache: dict[int, str] = {}

    def _disp(uid: int) -> str:
        if uid not in display_cache:
            display_cache[uid] = str(fighter_to_api_dict(session, by_uid[uid])["display_name"])
        return display_cache[uid]

    bracket_rounds: list[dict[str, Any]] = []
    survivors = order
    global_match_i = 0

    while len(survivors) > 1:
        round_idx = len(bracket_rounds) + 1
        round_matches: list[dict[str, Any]] = []
        if len(survivors) % 2 == 1:
            bye_uid = survivors[-1]
            pair_block = survivors[:-1]
        else:
            bye_uid = None
            pair_block = survivors

        next_survivors: list[int] = []
        for k in range(0, len(pair_block), 2):
            ua, ub = int(pair_block[k]), int(pair_block[k + 1])
            fa, fb = by_uid[ua], by_uid[ub]
            global_match_i += 1
            fb_before = {str(ua): round(fatigue[ua], 4), str(ub): round(fatigue[ub], 4)}
            # При нулевой усталости у обоих — тот же seed, что у дуэли; иначе отдельный тег + учёт усталости.
            if fatigue[ua] <= 0.0 and fatigue[ub] <= 0.0:
                match_tag = b""
            else:
                match_tag = f"elim|rd={round_idx}|m={global_match_i}".encode()
            fr = simulate_fight(fa, fb, fatigue=fatigue, match_tag=match_tag)
            w, ell = int(fr.winner_user_id), int(fr.loser_user_id)
            # Харизма (0..99) снижает прирост усталости — обаятельные ребята
            # лучше восстанавливаются между матчами турнира.
            step_a = _ELIMINATION_FATIGUE_STEP * (1.0 - _CHARISMA_RECOVERY_FACTOR * (fa.stat_charisma / 100.0))
            step_b = _ELIMINATION_FATIGUE_STEP * (1.0 - _CHARISMA_RECOVERY_FACTOR * (fb.stat_charisma / 100.0))
            fatigue[ua] = min(1.0, fatigue[ua] + step_a)
            fatigue[ub] = min(1.0, fatigue[ub] + step_b)
            fa_after = {str(ua): round(fatigue[ua], 4), str(ub): round(fatigue[ub], 4)}
            round_matches.append(
                {
                    "type": "fight",
                    "user_a": ua,
                    "user_b": ub,
                    "name_a": _disp(ua),
                    "name_b": _disp(ub),
                    "winner_user_id": w,
                    "loser_user_id": ell,
                    "winner_name": _disp(w),
                    "loser_name": _disp(ell),
                    "rounds_in_fight": len(fr.rounds),
                    "fatigue_before": fb_before,
                    "fatigue_after": fa_after,
                }
            )
            next_survivors.append(w)

        if bye_uid is not None:
            bu = int(bye_uid)
            round_matches.append(
                {
                    "type": "bye",
                    "bye_user_id": bu,
                    "bye_name": _disp(bu),
                }
            )
            next_survivors.append(bu)

        bracket_rounds.append({"round_index": round_idx, "matches": round_matches})
        survivors = next_survivors

    champion = int(survivors[0])
    return {
        "error": None,
        "champion_user_id": champion,
        "champion_name": _disp(champion),
        "starting_order": [{"user_id": uid, "name": _disp(uid)} for uid in order],
        "bracket_rounds": bracket_rounds,
        "fatigue_final": {str(uid): round(fatigue.get(uid, 0.0), 4) for uid in ids},
        "fatigue_step": _ELIMINATION_FATIGUE_STEP,
        "fatigue_rules": (
            "После каждого матча оба получают усталость (bye не добавляет). "
            "Харизма снижает прирост усталости до −40%. "
            "Усталость снижает эффективную атаку и защиту в следующих боях."
        ),
    }
