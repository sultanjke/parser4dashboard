"""krisha.kz - фильтр по году есть на сайте (`year[]`), одна строка на очередь."""
from __future__ import annotations

import logging
import math

from bs4 import BeautifulSoup

from ..deadline import parse_deadline
from ..http import HttpClient
from .common import coords, extract_js_object, format_price_sqm, run_details, text

log = logging.getLogger(__name__)

NAME = "krisha"
BASE = "https://krisha.kz"
SEARCH_URL = f"{BASE}/complex/search/"

COLUMNS = [
    "Город", "Название ЖК", "Адрес ЖК", "Статус строительства", "Цена за м²", "Срок сдачи",
    "Застройщик", "Разрешения", "Класс жилья", "Тип дома", "Фасад", "Лифт", "Этажность",
    "Отделка", "Паркинг", "Высота потолков", "Количество квартир", "Отопление",
]

# Колонка Excel -> подпись параметра на странице ЖК
PARAM_COLUMNS = {
    "Класс жилья": "Класс жилья", "Тип дома": "Тип дома", "Фасад": "Фасад", "Лифт": "Лифт",
    "Этажность": "Этажность", "Отделка": "Отделка", "Паркинг": "Паркинг",
    "Высота потолков": "Высота потолков", "Количество квартир": "Количество квартир",
    "Отопление": "Отопление",
}


def price_text(raw: str) -> str:
    """'от 799 000 ₸ за м²' -> 'от 799 000 ₸'; 'от 1.3 млн ₸ за м²' -> 'от 1.3 млн ₸'; 'Нет данных' -> ''."""
    raw = " ".join(raw.replace(" ", " ").split())
    if "₸" not in raw:
        return ""
    return raw.split("₸", 1)[0].strip() + " ₸"


# --- список ЖК ----------------------------------------------------------------
def parse_search_page(html: str) -> dict:
    """Вернуть {'nb', 'ids', 'years', 'cards': [{id, alias, city, name, state}]}."""
    data = extract_js_object(html, "window.data") or {}
    search = data.get("search") or {}
    ids = [int(i) for i in search.get("ids") or []]
    years = [int(v["key"]) for v in (search.get("store", {}).get("year", {}).get("values") or [])]
    soup = BeautifulSoup(html, "html.parser")
    cards = {}
    for card in soup.select("div.complex-card[data-complex-id]"):
        cid = int(card["data-complex-id"])
        if cid in cards:
            continue
        cards[cid] = {
            "id": cid,
            "alias": card.get("data-complex-alias", ""),
            "city": card.get("data-complex-region", ""),
            "name": text(card.select_one(".complex-card__title")) or card.get("data-name", ""),
            "state": text(card.select_one(".complex-card__state")),
            "price": price_text(text(card.select_one(".complex-card__square-price"))),
        }
    ordered = [cards[i] for i in ids if i in cards] if ids else list(cards.values())
    return {"nb": int(search.get("nb") or 0), "ids": ids, "years": years, "cards": ordered}


def _collect(client: HttpClient, params: list) -> list[dict]:
    first = parse_search_page(client.get(SEARCH_URL, params))
    cards = list(first["cards"])
    per_page = max(len(first["ids"]), 1)
    pages = math.ceil(first["nb"] / per_page) if first["nb"] else 1
    for page in range(2, pages + 2):  # +1 страница запаса на случай изменения числа ЖК
        result = parse_search_page(client.get(SEARCH_URL, params + [("page", page)]))
        if not result["cards"]:
            break
        cards.extend(result["cards"])
    unique = {c["id"]: c for c in cards}
    return list(unique.values())


def list_complexes(client: HttpClient, year_from: int) -> list[dict]:
    probe = parse_search_page(client.get(SEARCH_URL))
    years = sorted(y for y in probe["years"] if y >= year_from)
    if not years:
        log.warning("[krisha] на сайте нет годов >= %d (доступны: %s)", year_from, probe["years"])
        return []
    year_params = [("year[]", y) for y in years]
    log.info("[krisha] фильтр по годам: %s", years)
    cards = _collect(client, year_params)
    # Исключаем зарубежную недвижимость (Ташкент и т.п.)
    foreign = {c["id"] for c in _collect(client, year_params + [("regionAlias", "foreign")])}
    result = [c for c in cards if c["id"] not in foreign]
    log.info("[krisha] найдено ЖК: %d (исключено зарубежных: %d)", len(result), len(cards) - len(result))
    return result


# --- страница ЖК --------------------------------------------------------------
def parse_complex(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    sidebar: dict[str, str] = {}
    deadlines: list[str] = []
    for block in soup.select(".complex__sidebar-info"):
        title = text(block.select_one(".complex__sidebar-info-title"))
        value_el = block.select_one(".complex__sidebar-info-text")
        if not title or value_el is None:
            continue
        if title == "Срок сдачи":
            deadlines = [s.strip() for s in value_el.get_text("\n").split("\n") if s.strip()]
        sidebar[title] = text(value_el)

    params: dict[str, str] = {}
    for dl in soup.select("dl.complex-parameters__block"):
        if dl.dt is not None and dl.dd is not None:
            params[text(dl.dt)] = text(dl.dd)

    h1 = text(soup.find("h1"))
    name = h1.rsplit(" в ", 1)[0] if " в " in h1 else h1
    complex_data = (extract_js_object(html, "window.data") or {}).get("complex") or {}
    point = complex_data.get("map") or {}
    return {
        "name": name,
        "address": sidebar.get("Расположение", ""),
        "state": sidebar.get("Статус строительства", ""),
        "developer": sidebar.get("Застройщик", ""),
        "permits": sidebar.get("Разрешения", ""),
        # .offer__price - цена самого ЖК; .complex-card__square-price на этой странице - соседние ЖК
        "price": price_text(text(soup.select_one(".offer__price"))),
        # Если на странице «Нет данных», сайт всё равно отдаёт цену в window.data.complex
        # (isPriceSquareVisible=false) - именно она стоит в образце выгрузки.
        "hidden_price": format_price_sqm(complex_data.get("priceSquareFrom")),
        "coords": coords(point.get("lat"), point.get("lon")),
        "deadlines": deadlines,
        "params": params,
    }


def build_rows(card: dict, info: dict, year_from: int, url: str) -> list[dict]:
    base = {
        "Город": card.get("city") or info["address"].split(",")[0].strip(),
        "Название ЖК": card.get("name") or info["name"],
        "Адрес ЖК": info["address"],
        "Статус строительства": info["state"] or card.get("state", ""),
        "Цена за м²": info["price"] or info.get("hidden_price") or card.get("price", ""),
        "Застройщик": info["developer"],
        "Разрешения": info["permits"],
        "Ссылка": url,
        **info.get("coords", {}),
    }
    for col, label in PARAM_COLUMNS.items():
        base[col] = info["params"].get(label, "")

    rows = []
    for line in info["deadlines"]:
        year, _ = parse_deadline(line)
        if year is not None and year >= year_from:
            rows.append({**base, "Срок сдачи": line})
    if not rows and not any(parse_deadline(l)[0] for l in info["deadlines"]):
        # Срок не указан, но сайт сам отнёс ЖК к нужным годам - не теряем его.
        rows.append({**base, "Срок сдачи": " / ".join(info["deadlines"])})
    return rows


def scrape(client: HttpClient, year_from: int, limit: int | None = None, **options) -> tuple[list[dict], list[str]]:
    cards = list_complexes(client, year_from)
    if limit:
        cards = cards[:limit]

    def fetch(card: dict) -> list[dict]:
        url = f"{BASE}/complex/show/{card['alias']}/"
        card["url"] = url
        return build_rows(card, parse_complex(client.get(url)), year_from, url)

    return run_details(cards, fetch, NAME, options.get("on_progress"))
