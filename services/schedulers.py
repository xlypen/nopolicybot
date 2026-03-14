import asyncio
import os
import sys
from pathlib import Path


async def social_graph_daily_task(process_pending_days, logger) -> None:
    """
    Обрабатывает накопленные диалоги: саммари и обновление дерева связей.
    Запускается при старте (догон пропущенных дней) и каждые 4 часа.
    """
    await asyncio.sleep(120)
    while True:
        try:
            loop = asyncio.get_event_loop()
            n = await loop.run_in_executor(None, process_pending_days)
            if n > 0:
                logger.info("Дерево связей: обработано %s дней", n)
        except Exception as e:
            logger.warning("Ошибка обработки дерева связей: %s", e)
        await asyncio.sleep(4 * 3600)


async def social_graph_realtime_task(process_realtime_updates, logger) -> None:
    """
    Инкрементальное обновление дерева связей в реальном времени.
    """
    await asyncio.sleep(45)
    while True:
        try:
            import bot_settings

            enabled = bool(bot_settings.get("social_graph_realtime_enabled"))
            interval = bot_settings.get_int("social_graph_realtime_interval_sec", lo=15, hi=1800)
            min_new = bot_settings.get_int("social_graph_realtime_min_new_messages", lo=1, hi=20)
            if enabled:
                loop = asyncio.get_event_loop()
                n = await loop.run_in_executor(None, lambda: process_realtime_updates(min_new_messages=min_new))
                if n > 0:
                    logger.info("Дерево связей (live): обновлено %s связей", n)
            await asyncio.sleep(interval if enabled else 30)
        except Exception as e:
            logger.warning("Ошибка realtime-обновления дерева связей: %s", e)
            await asyncio.sleep(30)


async def restart_checker(restart_flag_path: Path, logger) -> None:
    """Проверяет флаг перезапуска из админ-панели."""
    while True:
        await asyncio.sleep(30)
        if restart_flag_path.exists():
            try:
                restart_flag_path.unlink()
            except OSError:
                pass
            logger.info("Перезапуск бота по запросу из админ-панели")
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve().parent.parent / "bot.py")] + sys.argv[1:])


async def marketing_metrics_rollup_task(logger) -> None:
    """
    Periodically computes marketing metric rollups used by admin API.
    """
    await asyncio.sleep(180)
    while True:
        try:
            from services.marketing_metrics import run_daily_rollups

            loop = asyncio.get_event_loop()
            chats = await loop.run_in_executor(None, run_daily_rollups)
            if chats > 0:
                logger.info("Marketing metrics: rollups refreshed for %s chats", chats)
        except Exception as e:
            logger.warning("Marketing metrics rollup error: %s", e)
        await asyncio.sleep(3600)


async def churn_detection_task(bot, logger) -> None:
    """
    Hourly retention/churn snapshots and optional outreach DMs.
    """
    await asyncio.sleep(210)
    while True:
        try:
            import bot_settings
            import user_stats
            from services.recommendations import (
                mark_retention_dm_sent,
                pick_at_risk_for_outreach,
                run_churn_detection,
                should_send_retention_dm,
            )

            enabled = bool(bot_settings.get("churn_detection_enabled"))
            interval = bot_settings.get_int("churn_check_interval_sec", lo=300, hi=86400)
            auto_dm_enabled = bool(bot_settings.get("retention_auto_dm_enabled"))
            dm_limit_per_run = bot_settings.get_int("retention_auto_dm_limit_per_run", lo=1, hi=20)
            min_churn_risk = bot_settings.get_float("retention_auto_dm_min_churn_risk", lo=0.5, hi=0.99)
            cooldown_hours = bot_settings.get_int("retention_auto_dm_cooldown_hours", lo=1, hi=168)
            dm_text = str(bot_settings.get("retention_auto_dm_text") or "").strip()
            if not dm_text:
                dm_text = "Мы давно тебя не видели. Заходи в чат, нам важна твоя точка зрения."

            if enabled:
                chats = user_stats.get_chats() if hasattr(user_stats, "get_chats") else []
                if not chats:
                    snapshot = run_churn_detection(None)
                    summary = snapshot.get("summary") or {}
                    logger.info(
                        "Churn job: users=%s at_risk=%s high_value=%s",
                        summary.get("users_considered", 0),
                        summary.get("at_risk_count", 0),
                        summary.get("high_value_at_risk_count", 0),
                    )
                sent_total = 0
                for row in chats:
                    cid_raw = row.get("chat_id")
                    if not str(cid_raw).lstrip("-").isdigit():
                        continue
                    chat_id = int(cid_raw)
                    loop = asyncio.get_event_loop()
                    snapshot = await loop.run_in_executor(None, lambda cid=chat_id: run_churn_detection(cid))
                    summary = snapshot.get("summary") or {}
                    logger.info(
                        "Churn job chat=%s: users=%s at_risk=%s high_value=%s",
                        chat_id,
                        summary.get("users_considered", 0),
                        summary.get("at_risk_count", 0),
                        summary.get("high_value_at_risk_count", 0),
                    )
                    if not auto_dm_enabled:
                        continue
                    candidates = pick_at_risk_for_outreach(
                        chat_id,
                        days=30,
                        min_churn_risk=min_churn_risk,
                        limit=dm_limit_per_run,
                    )
                    for candidate in candidates:
                        if sent_total >= dm_limit_per_run:
                            break
                        user_id = int(candidate.get("user_id", 0) or 0)
                        if not user_id:
                            continue
                        if not should_send_retention_dm(user_id, chat_id, cooldown_hours=cooldown_hours):
                            continue
                        try:
                            await bot.send_message(chat_id=user_id, text=dm_text)
                            mark_retention_dm_sent(user_id, chat_id)
                            sent_total += 1
                            logger.info(
                                "Retention DM sent: chat=%s user=%s churn=%.2f influence=%.2f",
                                chat_id,
                                user_id,
                                float(candidate.get("churn_risk", 0.0) or 0.0),
                                float(candidate.get("influence_score", 0.0) or 0.0),
                            )
                        except Exception as e:
                            logger.debug("Retention DM skipped for user %s: %s", user_id, e)
                    if sent_total >= dm_limit_per_run:
                        break
            await asyncio.sleep(interval if enabled else 60)
        except Exception as e:
            logger.warning("Churn detection job error: %s", e)
            await asyncio.sleep(60)


async def storage_parity_monitor_task(logger) -> None:
    """
    Periodically compares JSON and DB counts and writes parity diffs to data/parity_diff.log.
    """
    await asyncio.sleep(90)
    while True:
        try:
            from services.storage_cutover import run_parity_check_once

            interval = max(60, int(os.getenv("PARITY_CHECK_INTERVAL_SEC", "300") or 300))
            loop = asyncio.get_event_loop()
            payload = await loop.run_in_executor(None, run_parity_check_once)
            if bool((payload or {}).get("critical")):
                logger.warning(
                    "Storage parity critical drift: keys=%s delta=%s",
                    payload.get("critical_keys"),
                    payload.get("delta_db_minus_json"),
                )
            await asyncio.sleep(interval)
        except Exception as e:
            logger.warning("Storage parity monitor error: %s", e)
            await asyncio.sleep(120)


async def data_retention_task(logger) -> None:
    """
    Periodically deletes raw messages older than MESSAGE_RETENTION_DAYS.
    """
    await asyncio.sleep(240)
    while True:
        try:
            from services.data_privacy import run_retention_once

            retention_days = max(7, int(os.getenv("MESSAGE_RETENTION_DAYS", "90") or 90))
            interval = max(600, int(os.getenv("RETENTION_CHECK_INTERVAL_SEC", "21600") or 21600))
            result = await run_retention_once(days=retention_days)
            total_removed = int((result or {}).get("total_removed_messages", 0) or 0)
            if total_removed > 0:
                logger.info("Data retention cleanup removed %s raw messages (days=%s)", total_removed, retention_days)
            await asyncio.sleep(interval)
        except Exception as e:
            logger.warning("Data retention task error: %s", e)
            await asyncio.sleep(300)


def process_portrait_images_due() -> int:
    """
    Обновляет картинки портретов пользователей, у которых прошло больше 7 дней с последней генерации.
    Возвращает количество обновлённых портретов.
    Ограничение: до 5 пользователей за раз.
    """
    from datetime import date, timedelta
    from user_stats import _load, _save
    from services.portrait_image import generate_portrait_image

    cutoff = (date.today() - timedelta(days=7)).isoformat()
    data = _load()
    users = data.get("users", {})
    updated = 0
    max_per_run = 5

    for user_id_str, u in users.items():
        if updated >= max_per_run:
            break
        portrait = (u.get("portrait") or "").strip()
        if not portrait:
            continue
        last_date = (u.get("portrait_image_updated_date") or "").strip()
        if last_date and last_date >= cutoff:
            continue
        try:
            user_id = int(user_id_str)
            path = generate_portrait_image(
                user_id,
                portrait,
                u.get("display_name", ""),
            )
            if path:
                u["portrait_image_updated_date"] = date.today().isoformat()
                updated += 1
        except Exception:
            pass

    if updated > 0:
        _save(data)
    return updated


async def portrait_image_daily_task(logger) -> None:
    """
    Один раз в день обновляет картинки портретов пользователей (старше 7 дней).
    """
    await asyncio.sleep(3600)  # первый запуск через час после старта
    while True:
        try:
            loop = asyncio.get_event_loop()
            n = await loop.run_in_executor(None, process_portrait_images_due)
            if n > 0:
                logger.info("Картинки портретов: обновлено %s пользователей", n)
        except Exception as e:
            logger.warning("Ошибка обновления картинок портретов: %s", e)
        await asyncio.sleep(24 * 3600)  # раз в сутки
