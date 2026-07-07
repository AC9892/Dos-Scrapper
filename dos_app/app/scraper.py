from __future__ import annotations

import html as html_lib
import re
import threading
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from dos_app.app.models import ScrapedPage, ScrapeSettings
from dos_app.app.parser import parse_html


LogCallback = Callable[[str], None]
ResultCallback = Callable[[ScrapedPage], None]
ProgressCallback = Callable[[int, int], None]

ACCESS_CHALLENGE_PATTERNS = [
    re.compile(r"\bcaptcha\b", re.IGNORECASE),
    re.compile(r"\brecaptcha\b", re.IGNORECASE),
    re.compile(r"\bhcaptcha\b", re.IGNORECASE),
    re.compile(r"verify you are (a )?human", re.IGNORECASE),
    re.compile(r"checking your browser", re.IGNORECASE),
    re.compile(r"unusual traffic", re.IGNORECASE),
]

NON_HTML_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bmp",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".rar",
    ".svg",
    ".tar",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}


@dataclass(slots=True)
class ScrapeCallbacks:
    log: LogCallback
    result: ResultCallback
    progress: ProgressCallback


class ScrapeEngine:
    def __init__(self, settings: ScrapeSettings, callbacks: ScrapeCallbacks, stop_event: threading.Event) -> None:
        self.settings = settings
        self.callbacks = callbacks
        self.stop_event = stop_event
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})
        self.robot_parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def run(self) -> list[ScrapedPage]:
        urls = self._resolve_urls()
        results: list[ScrapedPage] = []
        total_hint = self.settings.max_pages if is_crawl_mode(self.settings.mode) else len(urls)

        for index, url in enumerate(urls, start=1):
            if self.stop_event.is_set():
                self.callbacks.log("Scraping stopped by user.")
                break
            if len(results) >= self.settings.max_pages:
                self.callbacks.log("Reached max pages limit.")
                break

            self.callbacks.progress(len(results), max(total_hint, 1))
            page = self._scrape_one(url)
            results.append(page)
            self.callbacks.result(page)
            self.callbacks.progress(len(results), max(total_hint, len(results), 1))

            if self.settings.delay_seconds > 0 and not self.stop_event.is_set():
                time.sleep(self.settings.delay_seconds)

        self.callbacks.log(f"Finished. Scraped {len(results)} page(s).")
        return results

    def _resolve_urls(self) -> Iterable[str]:
        normalized = normalize_url(self.settings.url)
        mode = normalized_mode(self.settings.mode)
        if mode == "Single Page":
            return [normalized]
        if mode == "Sitemap":
            if self.settings.selected_sitemap_urls:
                return self._filter_sitemap_page_urls(self.settings.selected_sitemap_urls, normalized)
            return self._filter_sitemap_page_urls(self._sitemap_urls(normalized), normalized)
        if mode in {"Whole Website", "Page and Beyond"}:
            return self._crawl_urls(normalized)
        return [normalized]

    def _scrape_one(self, url: str) -> ScrapedPage:
        if self.settings.respect_robots and not self._robots_allowed(url):
            self.callbacks.log(f"Blocked by robots.txt: {url}")
            return ScrapedPage(url=url, error="Blocked by robots.txt")

        self.callbacks.log(f"Scraping: {url}")
        try:
            if self.settings.use_playwright:
                html, final_url, status, content_type = self._fetch_with_playwright(url)
            else:
                response = self.session.get(url, timeout=self.settings.timeout_seconds)
                status = response.status_code
                content_type = response.headers.get("content-type", "")
                response.raise_for_status()
                html = response.text
                final_url = response.url
            page = parse_html(final_url, html, status, content_type)
            page.raw_html = html
            if is_access_challenge(html, status):
                page.error = "Access challenge or CAPTCHA detected; not bypassed."
                self.callbacks.log(f"Access challenge detected: {final_url}")
            return page
        except Exception as exc:  # noqa: BLE001 - surfaced to GUI instead of crashing the worker.
            self.callbacks.log(f"Error scraping {url}: {exc}")
            return ScrapedPage(url=url, error=str(exc))

    def _crawl_urls(self, start_url: str) -> Iterable[str]:
        start_url = canonicalize_url(start_url)
        queue: deque[tuple[str, int]] = deque([(start_url, 0)])
        seen: set[str] = set()
        queued: set[str] = {start_url}

        while queue and len(seen) < self.settings.max_pages and not self.stop_event.is_set():
            url, depth = queue.popleft()
            queued.discard(url)
            skip_reason = self._skip_reason(url, start_url, seen, depth)
            if skip_reason:
                self._log_skip(url, skip_reason)
                continue
            if self.settings.respect_robots and not self._robots_allowed(url):
                self._log_skip(url, "blocked by robots.txt")
                continue
            seen.add(url)
            yield url

            if self.stop_event.is_set():
                break
            if depth >= self.settings.crawl_depth:
                continue

            try:
                response = self.session.get(url, timeout=self.settings.timeout_seconds)
                response.raise_for_status()
                page = parse_html(response.url, response.text, response.status_code, response.headers.get("content-type", ""))
                for link in page.links:
                    clean = canonicalize_url(link)
                    link_skip_reason = self._skip_reason(clean, start_url, seen, depth + 1, queued)
                    if link_skip_reason:
                        self._log_skip(clean, link_skip_reason)
                        continue
                    queue.append((clean, depth + 1))
                    queued.add(clean)
            except Exception as exc:  # noqa: BLE001
                self.callbacks.log(f"Crawl discovery error on {url}: {exc}")

    def _skip_reason(
        self,
        url: str,
        start_url: str,
        seen: set[str],
        depth: int,
        queued: set[str] | None = None,
    ) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return "unsupported URL scheme"
        if url in seen or (queued is not None and url in queued):
            return "duplicate URL"
        if self.settings.skip_direct_media_files and is_probably_non_html_url(url):
            return "direct media/file URL"
        if depth > self.settings.crawl_depth:
            return "outside crawl depth"
        if self.settings.stay_on_same_domain and not same_domain(start_url, url):
            return "outside domain"
        if self.settings.restrict_to_starting_path and not under_starting_path(start_url, url):
            return "outside starting path"
        return ""

    def _filter_sitemap_page_urls(self, urls: Iterable[str], start_url: str) -> list[str]:
        allowed: list[str] = []
        seen: set[str] = set()
        for url in urls:
            clean = canonicalize_url(url)
            reason = self._skip_reason(clean, start_url, seen, 0)
            if reason:
                self._log_skip(clean, reason)
                continue
            seen.add(clean)
            allowed.append(clean)
            if len(allowed) >= self.settings.max_pages:
                break
        return allowed

    def _log_skip(self, url: str, reason: str) -> None:
        self.callbacks.log(f"Skipped URL ({reason}): {url}")

    def _sitemap_urls(self, url: str) -> Iterable[str]:
        sitemap_queue: deque[str] = deque(self._sitemap_candidates(url))
        visited_sitemaps: set[str] = set()
        page_urls: list[str] = []

        while sitemap_queue and len(page_urls) < self.settings.max_pages and not self.stop_event.is_set():
            sitemap_url = sitemap_queue.popleft()
            if sitemap_url in visited_sitemaps:
                continue
            visited_sitemaps.add(sitemap_url)
            self.callbacks.log(f"Loading sitemap: {sitemap_url}")

            try:
                response = self.session.get(sitemap_url, timeout=self.settings.timeout_seconds)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                self.callbacks.log(f"Sitemap load failed for {sitemap_url}: {exc}")
                continue

            locs = extract_sitemap_locs(response.content)
            if not locs:
                self.callbacks.log(f"No URLs found in sitemap: {sitemap_url}")
                continue

            for loc in locs:
                if len(page_urls) >= self.settings.max_pages:
                    break
                if looks_like_sitemap(loc):
                    sitemap_queue.append(loc)
                else:
                    page_urls.append(loc)

        if not page_urls:
            self.callbacks.log("Sitemap scrape found no page URLs.")
        return page_urls

    def _sitemap_candidates(self, url: str) -> list[str]:
        normalized = normalize_url(url)
        if looks_like_sitemap(normalized):
            return [normalized]

        candidates: list[str] = []
        robots_url = urljoin(site_root(normalized), "/robots.txt")
        try:
            response = self.session.get(robots_url, timeout=self.settings.timeout_seconds)
            if response.ok:
                for line in response.text.splitlines():
                    name, separator, value = line.partition(":")
                    if separator and name.strip().lower() == "sitemap":
                        candidates.append(value.strip())
        except Exception as exc:  # noqa: BLE001
            self.callbacks.log(f"Could not inspect robots.txt for sitemap entries: {exc}")

        candidates.append(urljoin(site_root(normalized), "/sitemap.xml"))
        return list(dict.fromkeys(candidate for candidate in candidates if candidate))

    def collect_sitemap_urls(self) -> list[str]:
        return list(self._sitemap_urls(normalize_url(self.settings.url)))[: self.settings.max_pages]

    def _robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        parser = self.robot_parsers.get(base)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(urljoin(base, "/robots.txt"))
            try:
                parser.read()
            except Exception as exc:  # noqa: BLE001
                self.callbacks.log(f"Could not read robots.txt for {base}; continuing without robots restrictions: {exc}")
                return True
            self.robot_parsers[base] = parser
        return parser.can_fetch(self.settings.user_agent, url)

    def _fetch_with_playwright(self, url: str) -> tuple[str, str, int | None, str]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Install it with 'pip install playwright' and 'playwright install chromium'.") from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(user_agent=self.settings.user_agent)
            page = context.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=int(self.settings.timeout_seconds * 1000))
            html = page.content()
            final_url = page.url
            status = response.status if response else None
            content_type = response.headers.get("content-type", "") if response else ""
            browser.close()
            return html, final_url, status, content_type


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url}"
    return strip_fragment(url)


def normalized_mode(mode: str) -> str:
    return {
        "Single page": "Single Page",
        "Single Page": "Single Page",
        "Crawl same domain": "Whole Website",
        "Whole Website": "Whole Website",
        "Page and Beyond": "Page and Beyond",
        "Sitemap scrape": "Sitemap",
        "Sitemap": "Sitemap",
    }.get(mode, mode)


def is_crawl_mode(mode: str) -> bool:
    return normalized_mode(mode) in {"Whole Website", "Page and Beyond"}


def canonicalize_url(url: str) -> str:
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return parsed._replace(path=path, fragment="").geturl()


def strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def same_domain(start_url: str, url: str) -> bool:
    return urlparse(start_url).netloc.lower() == urlparse(url).netloc.lower()


def under_starting_path(start_url: str, url: str) -> bool:
    if not same_domain(start_url, url):
        return False
    start_path = urlparse(start_url).path or "/"
    target_path = urlparse(url).path or "/"
    if not start_path.endswith("/"):
        start_path = f"{start_path}/"
    return target_path == start_path.rstrip("/") or target_path.startswith(start_path)


def is_probably_non_html_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(extension) for extension in NON_HTML_EXTENSIONS)


def site_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def is_access_challenge(html: str, status_code: int | None) -> bool:
    if status_code in {401, 403, 429}:
        return True
    return any(pattern.search(html) for pattern in ACCESS_CHALLENGE_PATTERNS)


def looks_like_sitemap(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".xml", ".xml.gz")) or "sitemap" in path


def extract_sitemap_locs(content: bytes) -> list[str]:
    locs = _extract_locs_with_element_tree(content)
    if locs:
        return locs

    text = content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "xml")
    locs = [tag.get_text(strip=True) for tag in soup.find_all("loc")]
    if locs:
        return [html_lib.unescape(loc) for loc in locs if loc]

    matches = re.findall(r"<loc\b[^>]*>(.*?)</loc>", text, flags=re.IGNORECASE | re.DOTALL)
    return [html_lib.unescape(re.sub(r"\s+", " ", match).strip()) for match in matches if match.strip()]


def _extract_locs_with_element_tree(content: bytes) -> list[str]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    urls: list[str] = []
    for element in root.iter():
        if element.tag.endswith("loc") and element.text:
            loc = element.text.strip()
            if loc:
                urls.append(html_lib.unescape(loc))
    return urls
