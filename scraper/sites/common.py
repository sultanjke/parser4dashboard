"""Общие помощники для парсеров сайтов."""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Iterable

log = logging.getLogger(__name__)


def extract_js_object(html: str, marker: str):
    """Достать JSON, присвоенный в скрипте: `window.data = {...};`."""
    i = html.find(marker)
    if i < 0:
        return None
    rest = html[i + len(marker):]
    rest = rest[rest.find("=") + 1:].lstrip()
    try:
        return json.JSONDecoder().raw_decode(rest)[0]
    except json.JSONDecodeError:
        return None


def coords(lat, lon) -> dict:
    """Скрытые поля координат ЖК (_lat/_lon): их нет в COLUMNS, поэтому формат Excel не меняется.
    Точки вне Казахстана (ошибки на сайте) отбрасываются."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return {}
    if not (40.0 <= lat <= 56.0 and 46.0 <= lon <= 88.0):
        return {}
    return {"_lat": round(lat, 6), "_lon": round(lon, 6)}


def text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el is not None else ""


def format_price_sqm(value) -> str:
    """545000 -> 'от 545 000 ₸' (формат образцов)."""
    if value in (None, "", 0):
        return ""
    try:
        return f"от {int(float(value)):,} ₸".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def strip_price(raw: str) -> str:
    """'от 799 000 ₸ за м²' / 'Застройщик: от 741 000 ₸ / м²' -> 'от 799 000 ₸'."""
    raw = " ".join(raw.replace("\xa0", " ").split())
    m = re.search(r"от\s+[\d\s]+₸", raw)
    if m:
        return " ".join(m.group(0).split())
    m = re.search(r"[\d\s]{4,}₸", raw)
    return " ".join(m.group(0).split()) if m else ""


MAX_CONSECUTIVE_ERRORS = 10


class TooManyErrors(RuntimeError):
    """Много ошибок подряд - скорее всего, сайт блокирует или поменял вёрстку. Несёт уже собранные строки."""

    def __init__(self, label: str, rows: list[dict], errors: list[str]) -> None:
        super().__init__(f"[{label}] {MAX_CONSECUTIVE_ERRORS} ошибок подряд - выгрузка остановлена")
        self.rows = rows
        self.errors = errors


def run_details(
    items: Iterable, fetch_rows: Callable, label: str, on_progress: Callable[[int, int], None] | None = None
) -> tuple[list[dict], list[str]]:
    """Обойти карточки ЖК; ошибка на одном ЖК не останавливает выгрузку.

    Но если ошибок MAX_CONSECUTIVE_ERRORS подряд, дальше идти бессмысленно (блокировка/новая вёрстка):
    бросаем TooManyErrors с уже собранными строками, чтобы не ждать часами впустую.
    """
    rows: list[dict] = []
    errors: list[str] = []
    streak = 0
    items = list(items)
    for n, item in enumerate(items, start=1):
        if on_progress:
            on_progress(n - 1, len(items))
        try:
            got = fetch_rows(item)
        except Exception as exc:  # noqa: BLE001 - логируем и идём дальше
            errors.append(f"{_name(item)}: {exc}")
            log.error("[%s] %d/%d %s - ОШИБКА: %s", label, n, len(items), _name(item), exc)
            streak += 1
            if streak >= MAX_CONSECUTIVE_ERRORS:
                raise TooManyErrors(label, rows, errors) from exc
            continue
        streak = 0
        rows.extend(got)
        log.info("[%s] %d/%d %s - строк: %d", label, n, len(items), _name(item), len(got))
    if on_progress:
        on_progress(len(items), len(items))
    return rows, errors


def _name(item) -> str:
    if isinstance(item, dict):
        return item.get("url") or item.get("name") or str(item)
    return str(item)
