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
