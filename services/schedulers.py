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
