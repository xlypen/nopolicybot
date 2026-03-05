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
