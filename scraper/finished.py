"""Список давно сданных ЖК, которые можно не перепроверять при следующих запусках.

Хранится в state/<site>_finished.json: {путь страницы: последний год сдачи (или null, если сроков нет)}.
В список попадают только ЖК, у которых ВСЕ дома уже сданы, - их сроки больше не изменятся.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class FinishedRegistry:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.items: dict[str, int | None] = {}
        self._dirty = 0
        if path and path.exists():
            try:
                self.items = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Не удалось прочитать %s (%s) - начинаю с пустого списка", path, exc)

    def can_skip(self, key: str, year_from: int) -> bool:
        """True, если ЖК сдан целиком и его последний срок раньше year_from (или сроков нет вовсе)."""
        if key not in self.items:
            return False
        last_year = self.items[key]
        return last_year is None or last_year < year_from

    def add(self, key: str, last_year: int | None) -> None:
        if self.items.get(key, "missing") != last_year:
            self.items[key] = last_year
            self._dirty += 1
            if self._dirty >= 100:  # сохраняем по ходу, чтобы прерванный прогон не терял прогресс
                self.save()

    def discard(self, key: str) -> None:
        if self.items.pop(key, "missing") != "missing":
            self._dirty += 1

    def save(self) -> None:
        if not self.path or not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = 0
