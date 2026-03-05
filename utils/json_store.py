"""Атомарная запись JSON-файлов. Используется вместо дублирования _save() в каждом модуле."""

import json
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_save(path: Path, data: dict | list, indent: int = 2) -> None:
    """Атомарно записывает данные в JSON-файл через tempfile + rename."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=indent)
        with tempfile.NamedTemporaryFile(
            "w", delete=False, encoding="utf-8", dir=path.parent, suffix=".tmp"
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except Exception as e:
        logger.warning("atomic_save(%s): %s", path.name, e)


def safe_load(path: Path, default=None):
    """Загружает JSON из файла. При ошибке возвращает default (dict() если не указан)."""
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, (dict, list)) else default
    except Exception as e:
        logger.warning("safe_load(%s): %s", path.name, e)
        return default
