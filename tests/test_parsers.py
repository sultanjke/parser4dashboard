from pathlib import Path

import pytest

from scraper.deadline import parse_deadline
from scraper.export import write_xlsx
from scraper.sites import kn, korter, krisha

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- сроки ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Пятая очередь - IV квартал 2027 г.", (2027, 4)),
        ("II квартал 2026 г.", (2026, 2)),
        ("I квартал 2028 г.", (2028, 1)),
        ("Строится, сдача в 3 кв. 2026", (2026, 3)),
        ("4 квартал 2026", (2026, 4)),
        ("2026 г.", (2026, None)),
        ("Сдан в 2026 году", (2026, None)),
        ("", (None, None)),
        ("не указан", (None, None)),
    ],
)
def test_parse_deadline(raw, expected):
    assert parse_deadline(raw) == expected


# --- krisha ---------------------------------------------------------------------
def test_krisha_search_page():
    page = krisha.parse_search_page(load("krisha_search.html"))
    assert page["nb"] > 0
    assert len(page["cards"]) == len(page["ids"]) > 0
    assert 2026 in page["years"]
    card = page["cards"][0]
    assert card["alias"] and card["city"] and card["name"].startswith("ЖК")


def test_krisha_complex_rows_per_queue():
    info = krisha.parse_complex(load("krisha_complex_kaspii.html"))
    assert len(info["deadlines"]) == 7
    rows = krisha.build_rows({"city": "Алматы", "name": "ЖК Каспий"}, info, 2026, "url")
    # из 7 очередей у 4 срок >= 2026
    assert [r["Срок сдачи"] for r in rows] == [
        "Четвертая очередь - III квартал 2027 г.",
        "Пятая очередь - IV квартал 2027 г.",
        "Шестая очередь - III квартал 2026 г.",
        "Седьмая очередь - IV квартал 2027 г.",
    ]
    row = rows[0]
    assert row["Застройщик"] == "ТОО SMR-EURASIA"
    assert row["Класс жилья"] == "эконом"
    assert row["Количество квартир"] == "1440"
    assert row["Цена за м²"].startswith("от ") and row["Цена за м²"].endswith("₸")
    assert set(krisha.COLUMNS) <= set(row)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("от 799 000 ₸ за м²", "от 799 000 ₸"),
        ("от 1.3 млн ₸ за м²", "от 1.3 млн ₸"),
        ("Нет данных", ""),
        ("", ""),
    ],
)
def test_krisha_price_text(raw, expected):
    assert krisha.price_text(raw) == expected


def test_krisha_price_falls_back_to_listing_card():
    info = krisha.parse_complex(load("krisha_complex_kaspii.html"))
    info["price"] = ""  # на странице ЖК "нет данных"
    info["hidden_price"] = ""  # и в JSON страницы цены нет
    rows = krisha.build_rows({"city": "Алматы", "price": "от 880 000 ₸"}, info, 2026, "url")
    assert rows[0]["Цена за м²"] == "от 880 000 ₸"


def test_krisha_year_threshold():
    info = krisha.parse_complex(load("krisha_complex_kaspii.html"))
    rows = krisha.build_rows({"city": "Алматы"}, info, 2027, "url")
    assert len(rows) == 3


# --- korter ---------------------------------------------------------------------
def test_korter_year_pages_selection():
    pages = [("/новостройки-астаны-сдача-2025", 2025), ("/новостройки-астаны-сдача-2026", 2026),
             ("/новостройки-астаны-сдача-2027", 2027), ("/новостройки-астаны-сдача-2028", 2028)]
    assert korter.select_year_pages(pages, 2026) == [p for p, _ in pages[1:]]
    # 2028 — это «2028+», поэтому для порога 2030 берём её и фильтруем локально
    assert korter.select_year_pages(pages, 2030) == ["/новостройки-астаны-сдача-2028"]


def test_korter_listing():
    listing = korter.parse_listing(load("korter_listing.html"))
    assert listing["total"] > 20
    assert len(listing["buildings"]) == 20
    assert listing["buildings"][0]["url"].startswith("/")


def test_korter_multi_queue_building():
    rows = korter.build_rows(korter.parse_building(load("korter_building_miraj.html")), 2026, "url")
    assert len(rows) == 1
    row = rows[0]
    assert row["№ очереди"] == "4 очередь"
    assert row["Срок сдачи"] == "Строится, сдача в 4 кв. 2027"
    assert row["Количество очередей"] == 4
    assert row["Количество домов в очереди"] == 3
    assert row["Застройщик"] == "ТОО Дилан-Куш"
    assert row["Утепление"] == "базальтовая плита"
    assert "<sup>" not in row["Площадь квартир"]


def test_korter_single_queue_uses_complex_name():
    rows = korter.build_rows(korter.parse_building(load("korter_building_fortis.html")), 2026, "url")
    assert len(rows) == 1
    assert rows[0]["№ очереди"] == "ЖК Fortis"
    assert rows[0]["Срок сдачи"] == "Строится, сдача в 4 кв. 2026"
    assert rows[0]["Цена за м2"].startswith("от ")


# --- kn -------------------------------------------------------------------------
def test_kn_search_page():
    page = kn.parse_search_page(load("kn_search.html"))
    assert page["last_page"] > 1
    assert len(page["cards"]) >= 20
    card = page["cards"][0]
    assert card["url"].startswith("https://www.kn.kz/zhilye-kompleksy/")
    assert card["city"] and card["year"]


def test_kn_multi_building_complex():
    card = {"url": "u", "city": "Алматы", "year": 2026}
    row = kn.build_row(card, kn.parse_complex(load("kn_complex_arena_park.html")), 2026)
    assert row["Название ЖК"] == "ЖК Arena City. Park"
    assert row["Срок сдачи"] == "4 квартал 2026"
    assert row["Ход строительства"] == "Строится"
    assert row["Статус"] == "Идут продажи от застройщика"
    assert row["Застройщик"] == "BI Group"
    assert row["Класс жилья"] == "бизнес"


def test_kn_single_building_complex():
    card = {"url": "u", "city": "Алматы", "year": 2026}
    row = kn.build_row(card, kn.parse_complex(load("kn_complex_millenium.html")), 2026)
    assert row["Адрес ЖК"] == "ул. Жангир хана, 25"
    assert row["Ход строительства"] == "Построен"
    assert row["Цена за м²"] == "от 1 260 000 ₸"


def test_kn_filters_old_deadline():
    card = {"url": "u", "city": "Алматы", "year": None}
    assert kn.build_row(card, kn.parse_complex(load("kn_complex_alau.html")), 2028) is None


# --- export ---------------------------------------------------------------------
def test_write_xlsx(tmp_path):
    from openpyxl import load_workbook

    path = write_xlsx(tmp_path / "x.xlsx", ["Город", "Название ЖК"], [{"Город": "Астана", "Название ЖК": "ЖК A", "Ссылка": "u"}])
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == ["№", "Город", "Название ЖК", "Ссылка"]
    assert [c.value for c in ws[2]] == [1, "Астана", "ЖК A", "u"]


def test_kn_houses_from_sidebar_address_block():

    card = {"url": "https://www.kn.kz/zhilye-kompleksy/almaty/dostyq-300", "city": "", "year": 2026}
    row = kn.build_row(card, kn.parse_complex(load("kn_complex_dostyq300.html")), 2026)
    assert row is not None
    assert row["Срок сдачи"] == "2026 г."
    assert row["Адрес ЖК"] == "пр. Достык, 300/1 стр"
    assert row["Город"] == "Алматы"  # из URL, если в карточке города нет


def test_kn_skips_complex_without_deadline():
    info = kn.parse_complex(load("kn_complex_millenium.html"))
    info["houses"] = []
    assert kn.build_row({"url": "u", "city": "Алматы", "year": None}, info, 2026) is None


# --- запоминание давно сданных ЖК (korter) -------------------------------------------
def test_korter_finished_year():
    miraj = korter.parse_building(load("korter_building_miraj.html"))["landing"]
    assert korter.finished_year(miraj) == (False, None)  # 4 очередь строится
    fake = {"houses": {"houses": [
        {"constructionStatus": "ready", "endYear": 2021},
        {"constructionStatus": "ready", "endYear": 2023},
    ]}}
    assert korter.finished_year(fake) == (True, 2023)
    assert korter.finished_year({"houses": {"houses": []}}) == (False, None)


def test_finished_registry_roundtrip(tmp_path):
    from scraper.finished import FinishedRegistry

    path = tmp_path / "state" / "korter_finished.json"
    reg = FinishedRegistry(path)
    reg.add("/жк-старый", 2023)
    reg.add("/жк-без-сроков", None)
    reg.add("/жк-сдан-в-2026", 2026)
    reg.save()

    reg = FinishedRegistry(path)
    assert reg.can_skip("/жк-старый", 2026)
    assert reg.can_skip("/жк-без-сроков", 2026)
    assert not reg.can_skip("/жк-сдан-в-2026", 2026)  # подходит под порог - проверяем всегда
    assert reg.can_skip("/жк-сдан-в-2026", 2027)
    assert not reg.can_skip("/жк-новый", 2026)
    reg.discard("/жк-старый")
    assert not reg.can_skip("/жк-старый", 2026)


def test_finished_registry_ignores_broken_file(tmp_path):
    from scraper.finished import FinishedRegistry

    path = tmp_path / "korter_finished.json"
    path.write_text("{broken", encoding="utf-8")
    assert FinishedRegistry(path).items == {}


# --- идеи из andprov/krisha.kz ----------------------------------------------------------
def test_krisha_hidden_price_from_page_json():
    info = krisha.parse_complex(load("krisha_complex_kaspii.html"))
    assert info["hidden_price"] == "от 450 000 ₸"
    info["price"] = ""  # на странице «Нет данных», но в window.data цена есть
    rows = krisha.build_rows({"city": "Алматы", "price": "от 1 ₸"}, info, 2026, "url")
    assert rows[0]["Цена за м²"] == "от 450 000 ₸"


def test_run_details_stops_after_consecutive_errors():
    from scraper.sites.common import MAX_CONSECUTIVE_ERRORS, TooManyErrors, run_details

    def fetch(item):
        if item == "ok":
            return [{"x": 1}]
        raise ValueError("boom")

    # единичные ошибки не мешают
    rows, errors = run_details(["ok", "bad", "ok"], fetch, "t")
    assert len(rows) == 2 and len(errors) == 1
    # серия ошибок подряд - стоп, собранные строки не теряются
    with pytest.raises(TooManyErrors) as exc:
        run_details(["ok"] + ["bad"] * MAX_CONSECUTIVE_ERRORS + ["ok"], fetch, "t")
    assert len(exc.value.rows) == 1


def test_http_waits_longer_when_blocked(monkeypatch):
    from scraper import http

    class Resp:
        def __init__(self, code):
            self.status_code, self.text, self.encoding = code, "<html>ok</html>", "utf-8"

    answers = iter([Resp(429), Resp(200)])
    sleeps = []
    client = http.HttpClient(delay=0)
    monkeypatch.setattr(client.session, "get", lambda *a, **k: next(answers))
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    assert client.get("https://example.kz/") == "<html>ok</html>"
    assert sleeps == [http.BLOCK_WAITS[0]]


# --- координаты ЖК (скрытые поля _lat/_lon, в Excel не попадают) --------------------------
def test_coordinates_from_all_sites():
    kr = krisha.build_rows({"city": "Алматы"}, krisha.parse_complex(load("krisha_complex_kaspii.html")), 2026, "u")[0]
    assert abs(kr["_lat"] - 43.2949) < 0.01 and abs(kr["_lon"] - 76.8304) < 0.01
    ko = korter.build_rows(korter.parse_building(load("korter_building_fortis.html")), 2026, "u")[0]
    assert abs(ko["_lat"] - 51.112) < 0.01 and abs(ko["_lon"] - 71.388) < 0.01
    kn_row = kn.build_row({"url": "u", "city": "Алматы", "year": 2026}, kn.parse_complex(load("kn_complex_arena_park.html")), 2026)
    assert 43.2 < kn_row["_lat"] < 43.3 and 76.8 < kn_row["_lon"] < 76.85


def test_coordinates_not_in_excel(tmp_path):
    from openpyxl import load_workbook

    rows = krisha.build_rows({"city": "Алматы"}, krisha.parse_complex(load("krisha_complex_kaspii.html")), 2026, "u")
    ws = load_workbook(write_xlsx(tmp_path / "k.xlsx", krisha.COLUMNS, rows)).active
    assert "_lat" not in [c.value for c in ws[1]]


def test_coords_rejects_points_outside_kazakhstan():
    from scraper.sites.common import coords

    assert coords(0, 0) == {}
    assert coords("43.2", "76.9") == {"_lat": 43.2, "_lon": 76.9}
