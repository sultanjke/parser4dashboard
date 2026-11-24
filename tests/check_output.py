import argparse, collections, glob, sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.deadline import parse_deadline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = {"krisha": "krisha_result2.xlsx", "korter": "korter_result2.xlsx", "kn": "kn_result2.xlsx"}

p = argparse.ArgumentParser()
p.add_argument("sites", nargs="*", default=list(SAMPLES))
p.add_argument("--year-from", type=int, default=2026)
args = p.parse_args()
sys.stdout.reconfigure(encoding="utf-8")

for site in args.sites:
    files = sorted(glob.glob(str(ROOT / "output" / f"{site}_result_*.xlsx")))
    if not files:
        print(f"##### {site}: файла нет"); continue
    ws = openpyxl.load_workbook(files[-1]).active
    hdr = [c.value for c in ws[1]]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    sample = ROOT / "references" / SAMPLES[site]
    note = ""
    if sample.exists():
        shdr = [(c.value or "").strip().replace("Утипление", "Утепление") for c in openpyxl.load_workbook(sample).active[1]]
        note = f"; заголовки как в образце: {hdr[:-1] == shdr}"
    print(f"##### {site}: {files[-1]} — строк {len(rows)}{note}")
    di = hdr.index("Срок сдачи")
    years = collections.Counter(parse_deadline(str(r[di] or ""))[0] for r in rows)
    print("   годы сдачи:", dict(sorted(years.items(), key=lambda x: (x[0] is None, x[0] or 0))))
    bad = [r for r in rows if (y := parse_deadline(str(r[di] or ""))[0]) is not None and y < args.year_from]
    print(f"   строк со сроком < {args.year_from}: {len(bad)}")
    print("   ЖК:", len({r[-1] for r in rows}), "| города:", collections.Counter(r[1] for r in rows).most_common(8))
    print("   заполненность:", ", ".join(f"{h} {sum(1 for r in rows if r[i] not in (None, '')) * 100 // max(len(rows), 1)}%" for i, h in enumerate(hdr) if i))
