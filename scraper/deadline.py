"""Разбор сроков сдачи из разных форматов сайтов в (год, квартал)."""

from __future__ import annotations

import re

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}

# «IV квартал 2027 г.» (krisha)
_ROMAN_Q = re.compile(r"\b(IV|I{1,3})\s+квартал\w*\s+(20\d{2})", re.IGNORECASE)
# «4 квартал 2026», «4 кв. 2027», «4-й квартал 2026» (kn, korter)
_ARABIC_Q = re.compile(r"\b([1-4])\s*(?:-?\s*й\s*)?(?:кв\.?|квартал\w*)\s*(20\d{2})", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")


def parse_deadline(text: str | None) -> tuple[int | None, int | None]:
    """Вернуть (год, квартал) из одной строки срока. Квартал может быть None."""
    if not text:
        return None, None
    m = _ROMAN_Q.search(text)
    if m:
        return int(m.group(2)), ROMAN[m.group(1).upper()]
    m = _ARABIC_Q.search(text)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = _YEAR.search(text)
    if m:
        return int(m.group(1)), None
    return None, None


def deadline_key(year: int | None, quarter: int | None) -> tuple[int, int]:
    """Ключ для сортировки сроков: неизвестный квартал считается концом года."""
    return (year or 0, quarter or 5)
