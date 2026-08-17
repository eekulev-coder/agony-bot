import json
import os
from datetime import datetime, timedelta

from config import PROCESSED_FILE


def load_processed() -> set:
    """Загружает ID обработанных сообщений"""
    if not os.path.exists(PROCESSED_FILE):
        return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Чистим старые (старше 24 часов)
            cutoff = (datetime.now() - timedelta(hours=24)).timestamp()
            return {int(k) for k, v in data.items() if v > cutoff}
    except Exception:
        return set()


def save_processed(processed: set):
    """Сохраняет ID с временем добавления"""
    now = datetime.now().timestamp()
    data = {str(mid): now for mid in processed}
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def add_processed(msg_id: int):
    """Добавляет одно сообщение в обработанные"""
    processed = load_processed()
    processed.add(msg_id)
    save_processed(processed)


def is_processed(msg_id: int) -> bool:
    return msg_id in load_processed()
