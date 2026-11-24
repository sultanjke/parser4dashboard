"""HTTP-клиент: вежливые задержки по хосту, ретраи с backoff, опциональный файловый кэш."""

from __future__ import annotations

import hashlib
import logging
import random
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# kn.kz просит Crawl-delay: 2 в robots.txt.
HOST_DELAYS = {"www.kn.kz": 2.0, "kn.kz": 2.0}

RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
# 403/429 - типичные ответы при блокировке за частые запросы: ждём дольше, чтобы сайт «остыл».
BLOCK_STATUSES = {403, 429}
BLOCK_WAITS = (30, 120, 300)


class NotFound(Exception):
    """Страница вернула 404/410 - повторять бессмысленно."""


class HttpClient:
    def __init__(
        self,
        delay: float = 1.0,
        retries: int = 4,
        timeout: float = 30.0,
        cache_dir: Path | None = None,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            }
        )
        self._last_request: dict[str, float] = {}

    # --- кэш -----------------------------------------------------------
    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        host = urlsplit(url).netloc or "local"
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / host / f"{digest}.html"

    # --- пауза ---------------------------------------------------------
    def _wait(self, host: str) -> None:
        min_delay = max(self.delay, HOST_DELAYS.get(host, 0.0))
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        pause = min_delay - elapsed
        if pause > 0:
            time.sleep(pause + random.uniform(0, min_delay * 0.3))
        self._last_request[host] = time.monotonic()

    # --- основной метод ------------------------------------------------
    def get(self, url: str, params: dict | list | None = None) -> str:
        """Вернуть текст страницы. Бросает NotFound на 404 и RuntimeError, если ретраи исчерпаны."""
        full_url = url
        if params:
            full_url = f"{url}{'&' if '?' in url else '?'}{urlencode(params, doseq=True)}"

        cache_path = self._cache_path(full_url)
        if cache_path and cache_path.exists():
            return cache_path.read_text(encoding="utf-8")

        host = urlsplit(full_url).netloc
        last_error: str = ""
        for attempt in range(1, self.retries + 1):
            self._wait(host)
            try:
                resp = self.session.get(full_url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code in (404, 410):
                    raise NotFound(full_url)
                if resp.status_code == 200 and resp.text.strip():
                    resp.encoding = resp.encoding or "utf-8"
                    if resp.encoding.lower() in ("iso-8859-1", "latin-1"):
                        resp.encoding = "utf-8"
                    text = resp.text
                    if cache_path:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(text, encoding="utf-8")
                    return text
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRY_STATUSES and resp.status_code != 200:
                    break
            if attempt == self.retries:
                break
            if last_error in {f"HTTP {c}" for c in BLOCK_STATUSES}:
                backoff = BLOCK_WAITS[min(attempt - 1, len(BLOCK_WAITS) - 1)]
            else:
                backoff = min(60.0, 3.0 * 2 ** (attempt - 1))
            log.warning("%s - %s, попытка %d/%d, жду %.0f с", full_url, last_error, attempt, self.retries, backoff)
            time.sleep(backoff)
        raise RuntimeError(f"Не удалось загрузить {full_url}: {last_error}")
