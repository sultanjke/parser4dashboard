from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import date
from pathlib import Path

from .export import write_xlsx
from .http import HttpClient
from .sites import SITES
from .sites.common import MAX_CONSECUTIVE_ERRORS, TooManyErrors

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scraper",
        description="Выгрузка ЖК с krisha.kz, korter.kz, kn.kz со сроком сдачи не раньше указанного года.",
    )
    parser.add_argument("--sites", nargs="+", choices=list(SITES), default=list(SITES), help="какие сайты парсить (по умолчанию все)")
    parser.add_argument("--year-from", type=int, default=2026, help="минимальный год сдачи (по умолчанию 2026)")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "output", help="папка для результатов")
    parser.add_argument("--limit", type=int, default=None, help="не больше N ЖК на сайт (для пробного запуска)")
    parser.add_argument("--delay", type=float, default=1.0, help="пауза между запросами к сайту, сек (kn.kz - минимум 2)")
    parser.add_argument("--fast", action="store_true", help="korter: только списки сайта (~10 мин вместо ~1 ч), без полностью распроданных ЖК")
    parser.add_argument("--recheck", action="store_true", help="korter: перепроверить и давно сданные ЖК из state/korter_finished.json")
    parser.add_argument("--sequential", action="store_true", help="парсить сайты по очереди, а не одновременно")
    parser.add_argument("--cache", action="store_true", help="кэшировать страницы в .cache/ (для повторных прогонов при отладке)")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    _setup_logging(args.out / f"scrape_{stamp}.log")
    log = logging.getLogger("scraper")
    log.info("Сайты: %s; год сдачи >= %d; %s", ", ".join(args.sites), args.year_from,
             "по очереди" if args.sequential else "одновременно")

    summary: dict[str, str] = {}

    def run_site(name: str) -> None:
        site = SITES[name]
        # У каждого сайта свой клиент: паузы считаются по сайту, сайты друг друга не ждут
        client = HttpClient(delay=args.delay, cache_dir=PROJECT_ROOT / ".cache" if args.cache else None)
        log.info("===== %s: старт =====", name)
        started = time.monotonic()
        try:
            rows, errors = site.scrape(
                client, args.year_from, args.limit,
                fast=args.fast, recheck=args.recheck, state_dir=PROJECT_ROOT / "state",
            )
            path = write_xlsx(args.out / f"{name}_result_{stamp}.xlsx", site.COLUMNS, rows)
        except TooManyErrors as exc:
            path = write_xlsx(args.out / f"{name}_result_{stamp}_partial.xlsx", site.COLUMNS, exc.rows)
            log.error("%s. Сохранено то, что успели собрать: %s", exc, path)
            summary[name] = f"{name}: ОСТАНОВЛЕН после {MAX_CONSECUTIVE_ERRORS} ошибок подряд, неполный файл ({len(exc.rows)} строк) -> {path}"
            return
        except Exception:  # noqa: BLE001 - сбой одного сайта не должен ронять остальные
            log.exception("[%s] выгрузка прервана", name)
            summary[name] = f"{name}: ОШИБКА, файл не создан (см. лог)"
            return
        for err in errors:
            log.warning("[%s] не удалось: %s", name, err)
        minutes = (time.monotonic() - started) / 60
        summary[name] = f"{name}: строк {len(rows)}, ошибок {len(errors)}, {minutes:.1f} мин -> {path}"
        log.info("===== %s: готово - %s", name, summary[name])

    if args.sequential:
        for name in args.sites:
            run_site(name)
    else:
        # daemon-потоки + join с таймаутом, чтобы Ctrl+C останавливал выгрузку сразу
        threads = [threading.Thread(target=run_site, args=(n,), name=n, daemon=True) for n in args.sites]
        for t in threads:
            t.start()
        try:
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(timeout=0.5)
        except KeyboardInterrupt:
            log.warning("Остановлено пользователем (Ctrl+C). Готовые файлы сохранены, незавершённые сайты - нет.")
            return 130

    log.info("===== Итог =====")
    for name in args.sites:
        log.info(summary.get(name, f"{name}: не завершён"))
    return 0


def _setup_logging(log_file: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
