"""Запись результатов в Excel в формате образцов (references/*_result2.xlsx)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

LINK_COLUMN = "Ссылка"


def fill_sheet(ws, columns: list[str], rows: list[dict], numbered: bool = True, link: bool = True) -> None:
    """Заполнить лист: «№» (если numbered), columns, «Ссылка» (если link). Жирная шапка, фильтр, автоширина."""
    headers = (["№"] if numbered else []) + list(columns) + ([LINK_COLUMN] if link else [])
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    for i, row in enumerate(rows, start=1):
        values = [_clean(row.get(c)) for c in columns]
        ws.append(([i] if numbered else []) + values + ([row.get(LINK_COLUMN, "")] if link else []))

    for idx, header in enumerate(headers, start=1):
        values = [str(header)] + [str(ws.cell(r, idx).value or "") for r in range(2, min(ws.max_row, 300) + 1)]
        width = max(len(v) for v in values) + 2
        narrow = numbered and idx == 1
        ws.column_dimensions[get_column_letter(idx)].width = 5 if narrow else min(max(width, 10), 60)

    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def write_xlsx(path: Path, columns: list[str], rows: list[dict]) -> Path:
    """columns - заголовки без «№» (он проставляется автоматически). В конец добавляется «Ссылка»."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Лист1"
    fill_sheet(ws, columns, rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    return value
