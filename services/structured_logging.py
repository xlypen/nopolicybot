from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": str(record.levelname),
            "logger": str(record.name),
            "service": str(getattr(record, "service", "") or ""),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(service_name: str) -> None:
    """
    Configures process logging once.
    LOG_JSON=1 enables JSON logs for log aggregation pipelines.
    """
    root = logging.getLogger()
    if getattr(root, "_nopolicy_logging_configured", False):
        return

    level_name = str(os.getenv("LOG_LEVEL", "INFO")).strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = str(os.getenv("LOG_JSON", "0")).strip() in {"1", "true", "yes", "on"}

    handler = logging.StreamHandler()
    if use_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    root.handlers = [handler]
    root.setLevel(level)
    root._nopolicy_logging_configured = True

    # Attach service label to root adapter via record factory.
    factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        rec = factory(*args, **kwargs)
        if not hasattr(rec, "service"):
            rec.service = service_name
        return rec

    logging.setLogRecordFactory(record_factory)
