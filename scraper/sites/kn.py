"""kn.kz - фильтра по сроку на сайте нет: отбираем ЖК по бейджу карточки в общем списке.

Одна строка на ЖК. Срок сдачи - самый поздний из блока «Ход строительства».
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from ..deadline import deadline_key, parse_deadline
from ..http import HttpClient
from .common import coords, run_details, strip_price, text

log = logging.getLogger(__name__)

NAME = "kn"
BASE = "https://www.kn.kz"
SEARCH_URL = f"{BASE}/zhilye-kompleksy/search"

COLUMNS = [
    "Город", "Название ЖК", "Адрес ЖК", "Цена за м²", "Статус", "Застройщик", "Ход строительства",
    "Срок сдачи", "Класс жилья", "Технология строительства", "Вариант отделки", "Отопление",
    "Умный дом", "Этажность", "Лифт", "Высота потолков", "Паркинг", "Окна",
]

# Колонка Excel -> подпись в блоке «Характеристики»
CHAR_COLUMNS = {
    "Класс жилья": "Класс ЖК", "Технология строительства": "Технология строительства",
    "Вариант отделки": "Варианты отделки", "Отопление": "Отопление", "Умный дом": "Умный дом",
    "Этажность": "Этажность", "Лифт": "Лифт", "Высота потолков": "Высота потолков",
    "Паркинг": "Паркинг", "Окна": "Окна",
}

_COMPLEX_HREF = re.compile(r"^/zhilye-kompleksy/(?!search)[a-z0-9\-]+/[a-z0-9\-]+/?$")
_BADGE = re.compile(r"(Сдача:|Сдан[а-я]*\s+в)\s*(20\d{2})")


# --- список ЖК --------------------------------------------------------------------
def parse_search_page(html: str) -> dict:
    """Вернуть {'cards': [{url, city, name, year}], 'last_page'}."""
    soup = BeautifulSoup(html, "html.parser")
    cards, seen = [], set()
    for card in soup.select("div.kn-shadow"):
        link = next((a for a in card.select("a[href]") if _COMPLEX_HREF.match(a["href"])), None)
        if link is None:
            continue
        href = link["href"].rstrip("/")
        if href in seen:
            continue
        seen.add(href)
        badge = _BADGE.search(text(card))
        img = card.select_one("img[alt]")
        alt = img.get("alt", "") if img else ""  # «ЖК «Millenium», Алматы»
        city = alt.rsplit(",", 1)[1].strip() if "," in alt else ""
        title = next((text(a) for a in card.select("a[href]") if a["href"].rstrip("/") == href and text(a)), "")
        cards.append({
            "url": BASE + href,
            "city": city,
            "name": title.replace("«", "").replace("»", ""),
            "year": int(badge.group(2)) if badge else None,
        })
    pages = [int(p) for p in re.findall(r"[?&]page=(\d+)", html)]
    return {"cards": cards, "last_page": max(pages) if pages else 1}


def list_complexes(client: HttpClient, year_from: int) -> list[dict]:
    first = parse_search_page(client.get(SEARCH_URL))
    cards = list(first["cards"])
    last = first["last_page"]
    page = 2
    while page <= last:
        result = parse_search_page(client.get(SEARCH_URL, {"page": page}))
        if not result["cards"]:
            break
        cards.extend(result["cards"])
        last = max(last, result["last_page"])
        if page % 20 == 0:
            log.info("[kn] просмотрено страниц списка: %d/%d", page, last)
        page += 1
    unique = list({c["url"]: c for c in cards}.values())
    selected = [c for c in unique if c["year"] is None or c["year"] >= year_from]
    log.info(
        "[kn] всего ЖК: %d, со сроком >= %d: %d (из них без срока в карточке: %d)",
        len(unique), year_from, len(selected), sum(1 for c in selected if c["year"] is None),
    )
    return selected


# --- страница ЖК ------------------------------------------------------------------
def parse_complex(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    labeled: dict[str, object] = {}
    for label in soup.select("div.kn-text-color-grey-6.kn-mb-15"):
        key = text(label)
        if not key or key in labeled:
            continue
        values = [text(v) for v in label.find_next_siblings("div")]
        labeled[key] = [v for v in values if v]

    h1 = text(soup.find("h1"))
    return {
        "name": h1.rsplit(" в ", 1)[0] if " в " in h1 else h1,
        "labeled": labeled,
        "houses": _stage_houses(soup) or _address_houses(soup),
        "coords": _map_center(soup),
    }


def _map_center(soup) -> dict:
    """Точки домов из блока карты (data-map) - центр ЖК как среднее по домам."""
    points = []
    for el in soup.select("[data-complex-map-target=point][data-map]"):
        try:
            for p in json.loads(el["data-map"]):
                points.append((float(p["latitude"]), float(p["longitude"])))
        except (ValueError, KeyError, TypeError):
            continue
    if not points:
        return {}
    return coords(sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _house(address: str, deadline: str, status: str = "") -> dict:
    year, quarter = parse_deadline(deadline)
    return {"address": address, "deadline": deadline, "status": status, "year": year, "quarter": quarter}


def _stage_houses(soup) -> list[dict]:
    """Блок «Ход строительства»: строки (адрес | срок | статус) по каждому дому."""
    header = soup.find(id="complex-stage")
    container = header.find_next_sibling("div") if header is not None else None
    houses = []
    for row in container.select("div.row") if container else []:
        cols = [text(c) for c in row.find_all("div", recursive=False)]
        if len(cols) >= 2 and cols[0]:
            houses.append(_house(cols[0], cols[1], cols[2] if len(cols) > 2 else ""))
    return houses


def _address_houses(soup) -> list[dict]:
    """Блок «Адрес» в сайдбаре: строки (адрес | значок карты | срок), если «Хода строительства» нет."""
    label = next((l for l in soup.select("div.kn-text-color-grey-6.kn-mb-15") if text(l) == "Адрес"), None)
    if label is None:
        return []
    houses = []
    for row in label.parent.select("div.row"):
        cols = [text(c) for c in row.find_all("div", recursive=False)]
        cols = [c for c in cols if c]
        if cols:
            houses.append(_house(cols[0], cols[-1] if len(cols) > 1 else ""))
    return houses


CITY_BY_SLUG = {
    "aktau": "Актау", "aktobe": "Актобе", "almaty": "Алматы", "astana": "Астана", "atyrau": "Атырау",
    "karaganda": "Караганда", "kokshetau": "Кокшетау", "kostanay": "Костанай", "pavlodar": "Павлодар",
    "petropavlovsk": "Петропавловск", "shymkent": "Шымкент", "uralsk": "Уральск",
    "ust-kamenogorsk": "Усть-Каменогорск",
}


def _city_from_url(url: str) -> str:
    m = re.search(r"/zhilye-kompleksy/([a-z\-]+)/", url)
    return CITY_BY_SLUG.get(m.group(1), m.group(1)) if m else ""


def _first(labeled: dict, key: str) -> str:
    values = labeled.get(key) or []
    return values[0] if values else ""


def build_row(card: dict, info: dict, year_from: int) -> dict | None:
    """Строка по ЖК; срок - самый поздний из домов. None, если срок неизвестен или раньше year_from."""
    labeled, houses = info["labeled"], info["houses"]

    dated = [h for h in houses if h["year"]]
    latest = max(dated, key=lambda h: deadline_key(h["year"], h["quarter"])) if dated else None
    if latest:
        deadline, year = latest["deadline"], latest["year"]
    elif card.get("year"):
        deadline, year = f"{card['year']} г.", card["year"]
    else:
        return None  # срок нигде не указан - как правило, давно сданные ЖК
    if year < year_from:
        return None

    status_lines = labeled.get("Статус") or []
    construction = latest["status"].capitalize() if latest and latest["status"] else (status_lines[0] if status_lines else "")

    row = {
        "Город": card.get("city") or _city_from_url(card["url"]),
        "Название ЖК": info["name"] or card.get("name", ""),
        "Адрес ЖК": latest["address"] if latest else (houses[0]["address"] if houses else ""),
        "Цена за м²": strip_price(" ".join(labeled.get("Цена") or [])),
        "Статус": status_lines[-1] if status_lines else "",
        "Застройщик": _first(labeled, "Застройщик"),
        "Ход строительства": construction,
        "Срок сдачи": deadline,
        "Ссылка": card["url"],
        **info.get("coords", {}),
    }
    for col, label in CHAR_COLUMNS.items():
        row[col] = _first(labeled, label)
    return row


def scrape(client: HttpClient, year_from: int, limit: int | None = None, **options) -> tuple[list[dict], list[str]]:
    cards = list_complexes(client, year_from)
    if limit:
        cards = cards[:limit]

    def fetch(card: dict) -> list[dict]:
        row = build_row(card, parse_complex(client.get(card["url"])), year_from)
        return [row] if row else []

    return run_details(cards, fetch, NAME, options.get("on_progress"))
