"""korter.kz - SEO-страницы «новостройки-<город>-сдача-<год>», JSON в window.INITIAL_STATE.

Одна строка на очередь ЖК (дома группируются по queueId).
"""
from __future__ import annotations

import json
import logging
import math
import re
from urllib.parse import unquote, urlsplit

from pathlib import Path

from ..deadline import deadline_key
from ..finished import FinishedRegistry
from ..http import HttpClient, NotFound
from .common import coords, extract_js_object, format_price_sqm, run_details

log = logging.getLogger(__name__)

NAME = "korter"
BASE = "https://korter.kz"
YEAR_SITEMAP = f"{BASE}/sitemap/building_geo_stateReadyAtYear.xml"
BUILDING_SITEMAP = f"{BASE}/sitemap/building_landing.xml"
PER_PAGE = 20

COLUMNS = [
    "Город", "Название ЖК", "Адрес ЖК", "Цена за м2", "Застройщик", "Контакты Застройщика",
    "Количество очередей", "№ очереди", "Срок сдачи", "Количество домов в очереди", "Всего домов",
    "Наличие лифта", "Территория", "Окна", "Класс жилья", "Отопление", "Утепление",
    "Площадь квартир", "Этажность", "Паркинг", "Фасад", "Технология строительства", "Стены",
    "Количество квартир",
]

# Колонка Excel -> название атрибута в attributes.house / attributes.flat
ATTR_COLUMNS = {
    "Наличие лифта": "Наличие лифта", "Территория": "Территория", "Окна": "Окна",
    "Класс жилья": "Класс", "Отопление": "Отопление", "Утепление": "Утепление",
    "Площадь квартир": "Площадь квартир", "Этажность": "Этажность", "Паркинг": "Паркинг",
    "Фасад": "Фасад", "Технология строительства": "Технология строительства", "Стены": "Стены",
    "Количество квартир": "Количество квартир",
}

_YEAR_PAGE = re.compile(r"-сдача-(\d{4})$")


# --- список страниц «город × год» -------------------------------------------------
def parse_year_sitemap(xml: str) -> list[tuple[str, int]]:
    """[(path, year)] из sitemap. Страница «-сдача-2028» на сайте означает «2028+»."""
    out = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
        path = unquote(urlsplit(loc.strip()).path)
        m = _YEAR_PAGE.search(path)
        if m:
            out.append((path, int(m.group(1))))
    return out


def select_year_pages(pages: list[tuple[str, int]], year_from: int) -> list[str]:
    if not pages:
        return []
    top = max(y for _, y in pages)  # последняя страница - «top+»
    return [p for p, y in pages if y >= year_from or (y == top and year_from > top)]


def parse_listing(html: str) -> dict:
    state = extract_js_object(html, "window.INITIAL_STATE") or {}
    store = state.get("buildingListingStore") or {}
    total = (store.get("filtersStore") or {}).get("totalCount") or 0
    buildings = [
        {"id": b.get("buildingId"), "url": b.get("url"), "name": b.get("name")}
        for b in store.get("buildings") or []
        if b.get("url")
    ]
    return {"total": int(total), "buildings": buildings}


def list_buildings(client: HttpClient, year_from: int) -> list[dict]:
    pages = select_year_pages(parse_year_sitemap(client.get(YEAR_SITEMAP)), year_from)
    log.info("[korter] страниц «город × год сдачи»: %d", len(pages))
    found: dict = {}
    for path in pages:
        try:
            first = parse_listing(client.get(BASE + path))
        except NotFound:
            log.warning("[korter] нет страницы %s", path)
            continue
        items = list(first["buildings"])
        for page in range(2, math.ceil(first["total"] / PER_PAGE) + 1):
            items.extend(parse_listing(client.get(BASE + path, {"page": page}))["buildings"])
        for b in items:
            found.setdefault(b["id"] or b["url"], b)
        log.info("[korter] %s - ЖК: %d (уникальных всего: %d)", path, first["total"], len(found))
    return list(found.values())


def parse_building_sitemap(xml: str) -> list[str]:
    """Пути всех страниц ЖК из sitemap/building_landing.xml."""
    return [unquote(urlsplit(loc.strip()).path) for loc in re.findall(r"<loc>([^<]+)</loc>", xml)]


def list_all_buildings(client: HttpClient, year_from: int, finished: FinishedRegistry) -> list[dict]:
    """Списки «сдача-год» + все ЖК из sitemap, кроме уже известных давно сданных.

    Списки korter не показывают полностью распроданные ЖК (даже строящиеся), поэтому
    для полноты дополнительно обходим все страницы ЖК из sitemap и фильтруем по срокам домов.
    """
    listed = list_buildings(client, year_from)
    seen = {b["url"] for b in listed}
    sitemap = [p for p in parse_building_sitemap(client.get(BUILDING_SITEMAP)) if p not in seen]
    extra = [{"id": None, "url": p, "name": ""} for p in sitemap if not finished.can_skip(p, year_from)]
    log.info(
        "[korter] в списках: %d ЖК, в sitemap ещё %d, из них пропущено давно сданных: %d, к проверке: %d",
        len(listed), len(sitemap), len(sitemap) - len(extra), len(extra),
    )
    return listed + extra


def finished_year(landing: dict) -> tuple[bool, int | None]:
    """(все дома сданы?, последний год сдачи). ЖК без домов сданным не считается."""
    houses = (landing.get("houses") or {}).get("houses") or []
    if not houses or any(h.get("constructionStatus") != "ready" for h in houses):
        return False, None
    years = [h["endYear"] for h in houses if h.get("endYear")]
    return True, max(years) if years else None


# --- страница ЖК ----------------------------------------------------------------
def parse_building(html: str) -> dict:
    state = extract_js_object(html, "window.INITIAL_STATE") or {}
    landing = state.get("buildingLandingStore")
    if not landing:
        raise ValueError("на странице нет buildingLandingStore")
    contacts = ((state.get("contactsStore") or {}).get("main")) or {}
    return {"landing": landing, "contacts": contacts}


def _json_field(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _attributes(landing: dict) -> dict[str, str]:
    attrs = {}
    for group in (landing.get("attributes") or {}).values():
        for item in group or []:
            name, value = item.get("name"), item.get("value")
            if name and value not in (None, ""):
                attrs[name] = re.sub(r"<[^>]+>", "", str(value)).replace("м2", "м²")
    return attrs


def _min_price_sqm(landing: dict, main: dict):
    if main.get("minPriceSqm"):
        return main["minPriceSqm"]
    values = [u.get("minPriceSqm") for u in (landing.get("prices") or {}).get("unitTypes") or [] if u.get("minPriceSqm")]
    return min(values) if values else None


def format_deadline(houses: list[dict]) -> tuple[str, int | None]:
    """По домам очереди -> («Строится, сдача в 4 кв. 2027» | «Сдано в 1 кв. 2026», год)."""
    dated = [h for h in houses if h.get("endYear")]
    if not dated:
        return "", None
    last = max(dated, key=lambda h: deadline_key(h.get("endYear"), h.get("endQuarter")))
    year, quarter = last["endYear"], last.get("endQuarter")
    when = f"{quarter} кв. {year}" if quarter else f"{year}"
    ready = all(h.get("constructionStatus") == "ready" for h in houses)
    return (f"Сдано в {when}" if ready else f"Строится, сдача в {when}"), year


def build_rows(info: dict, year_from: int, url: str) -> list[dict]:
    landing, contacts = info["landing"], info["contacts"]
    main = landing.get("main") or {}
    houses_block = landing.get("houses") or {}
    queues = houses_block.get("queues") or []
    houses = houses_block.get("houses") or []
    attrs = _attributes(landing)

    developers = _json_field(main.get("developers")) or []
    developer = ", ".join(d.get("name", "") for d in developers if isinstance(d, dict)) if isinstance(developers, list) else str(developers)
    phones = [p for p in (main.get("mainPhone"), contacts.get("mainPhone"), contacts.get("whatsAppPhone")) if p]

    base = {
        "Город": main.get("mainGeoObjectNominative", ""),
        "Название ЖК": main.get("name") or main.get("nameOrAddress", ""),
        "Адрес ЖК": main.get("address", ""),
        "Цена за м2": format_price_sqm(_min_price_sqm(landing, main)),
        "Застройщик": developer,
        "Контакты Застройщика": ", ".join(dict.fromkeys(str(p) for p in phones)),
        "Количество очередей": len(queues) or "",
        "Всего домов": len(houses) or main.get("houseCount") or "",
        "Ссылка": url,
        **coords((landing.get("map") or {}).get("lat"), (landing.get("map") or {}).get("lng")),
    }
    for col, attr in ATTR_COLUMNS.items():
        base[col] = attrs.get(attr, "")

    queue_names = {q.get("queueId"): q.get("name", "") for q in queues}
    groups: dict = {}
    for h in houses:
        groups.setdefault(h.get("queueId"), []).append(h)

    # порядок как на сайте: сначала очереди из списка queues, затем дома без известной очереди
    ordered_ids = [q.get("queueId") for q in queues if q.get("queueId") in groups]
    ordered_ids += [qid for qid in groups if qid not in ordered_ids]

    rows = []
    for queue_id in ordered_ids:
        q_houses = groups[queue_id]
        deadline, year = format_deadline(q_houses)
        if year is None or year < year_from:
            continue
        name = queue_names.get(queue_id) or ""
        if len(groups) == 1:
            name = base["Название ЖК"]  # как в образце: при одной очереди - имя ЖК
        rows.append({
            **base,
            "№ очереди": name,
            "Срок сдачи": deadline,
            "Количество домов в очереди": len(q_houses),
        })
    return rows


def scrape(
    client: HttpClient,
    year_from: int,
    limit: int | None = None,
    fast: bool = False,
    state_dir: Path | None = None,
    recheck: bool = False,
    on_progress=None,
) -> tuple[list[dict], list[str]]:
    """fast=True - только списки сайта (~10 мин, без распроданных ЖК).

    Иначе - списки + все ЖК из sitemap: первый раз ~1 ч, дальше ~15 мин, потому что давно сданные ЖК
    запоминаются в state/korter_finished.json и не перепроверяются (recheck=True - проверить всё заново).
    """
    finished = FinishedRegistry(state_dir / "korter_finished.json" if state_dir else None)
    if recheck:
        finished.items.clear()
    buildings = list_buildings(client, year_from) if fast else list_all_buildings(client, year_from, finished)
    log.info("[korter] ЖК к проверке: %d", len(buildings))
    if limit:
        buildings = buildings[:limit]
    seen_ids: set = set()

    def fetch(b: dict) -> list[dict]:
        url = BASE + b["url"]
        try:
            info = parse_building(client.get(url))
        except NotFound:
            log.info("[korter] %s - страница удалена (404)", b["url"])
            return []
        is_finished, last_year = finished_year(info["landing"])
        if is_finished:
            finished.add(b["url"], last_year)
        else:
            finished.discard(b["url"])
        building_id = info["landing"].get("buildingId")
        if building_id in seen_ids:  # один ЖК под разными адресами страницы
            return []
        seen_ids.add(building_id)
        return build_rows(info, year_from, url)

    try:
        return run_details(buildings, fetch, NAME, on_progress)
    finally:
        finished.save()
