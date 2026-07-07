from __future__ import annotations

import hashlib
import html as html_module
import json
import re
from datetime import datetime
from pathlib import Path
from threading import Event
from collections.abc import Callable
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from dos_scraper.app.models import ScrapedPage


ASSET_TAGS = [
    ("img", "src"),
    ("script", "src"),
    ("link", "href"),
    ("source", "src"),
    ("video", "src"),
    ("audio", "src"),
]

MEDIA_DATA_ATTRIBUTES = {
    "data-mp4-source",
    "data-mp4-hd-source",
    "data-webm-source",
    "data-webm-hd-source",
    "data-video-source",
    "data-video-src",
    "data-poster",
    "data-background",
    "data-screenshot",
    "data-full",
    "data-thumb",
}

VIDEO_SOURCE_ATTRIBUTES = [
    "data-mp4-source",
    "data-mp4-hd-source",
    "data-webm-source",
    "data-webm-hd-source",
    "data-video-source",
    "data-video-src",
]

POSTER_ATTRIBUTES = [
    "poster",
    "data-poster",
    "data-background",
    "data-screenshot",
    "data-full",
    "data-thumb",
]

DIRECT_MEDIA_EXTENSIONS = (".mp4", ".webm", ".ogv", ".ogg", ".mov", ".m4v", ".mp3", ".m4a", ".wav")

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]


def export_local_website(
    results: list[ScrapedPage],
    output_folder: Path,
    user_agent: str,
    timeout_seconds: float,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
    stop_event: Event | None = None,
) -> Path:
    html_pages = [page for page in results if page.raw_html and not page.error and is_html_page(page)]
    if not html_pages:
        raise ValueError("No HTML pages are available to export as a local website.")

    folder = unique_folder(output_folder / folder_name(html_pages[0], multi_page=len(html_pages) > 1))
    assets_folder = folder / "assets"
    pages_folder = folder / "pages"
    assets_folder.mkdir(parents=True, exist_ok=True)
    pages_folder.mkdir(parents=True, exist_ok=True)

    page_paths = build_page_paths(html_pages, folder, pages_folder)
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    downloaded_assets: dict[str, Path] = {}
    media_records: list[dict[str, str]] = []
    progress_total = max(len(html_pages), 1)

    for index, page in enumerate(html_pages, start=1):
        if stop_event and stop_event.is_set():
            raise RuntimeError("Local website export stopped.")
        if log:
            log(f"Exporting local page {index}/{len(html_pages)}: {page.url}")
        if progress:
            progress(index - 1, progress_total)
        page_path = page_paths[canonical_url(page.url)]
        soup = BeautifulSoup(page.raw_html, "lxml")
        add_base_metadata(soup, page)
        rewrite_page_links(soup, page, page_paths, page_path, folder)
        rewrite_and_download_assets(
            soup,
            page,
            page_path,
            assets_folder,
            downloaded_assets,
            session,
            timeout_seconds,
            log,
            stop_event,
        )
        download_discovered_media(page, page_path, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)
        media_records.extend(media_records_for_page(page, soup, page_path, folder, downloaded_assets))
        replace_external_video_embeds(soup)
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(str(soup), encoding="utf-8")
        if progress:
            progress(index, progress_total)

    write_open_this_file(folder)
    write_local_index(folder, html_pages, page_paths)
    write_media_library(folder, media_records)
    if log:
        log(f"Local website export complete: {folder}")
    return folder


def build_page_paths(html_pages: list[ScrapedPage], folder: Path, pages_folder: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    used_names: set[str] = set()
    for index, page in enumerate(html_pages):
        if index == 0:
            paths[canonical_url(page.url)] = folder / "index.html"
            continue
        name = unique_name(safe_slug(page.title or url_to_name(page.url) or f"page-{index + 1}"), used_names)
        paths[canonical_url(page.url)] = pages_folder / f"{name}.html"
    return paths


def rewrite_page_links(
    soup: BeautifulSoup,
    page: ScrapedPage,
    page_paths: dict[str, Path],
    page_path: Path,
    folder: Path,
) -> None:
    offline_notice_id = "dos-scraper-offline-notice"
    for anchor in soup.find_all("a", href=True):
        if anchor.get("data-dos-scraper-internal") == "1":
            continue
        href = anchor.get("href", "")
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = canonical_url(urljoin(page.url, href))
        target = page_paths.get(absolute)
        if target:
            anchor["href"] = relative_path(page_path, target)
        else:
            anchor["data-original-href"] = absolute
            anchor["href"] = f"#{offline_notice_id}"
            existing_title = anchor.get("title", "")
            anchor["title"] = f"{existing_title} Original URL not included in this local export: {absolute}".strip()

    for form in soup.find_all("form"):
        action = form.get("action")
        if action:
            form["data-original-action"] = canonical_url(urljoin(page.url, action))
        form["action"] = f"#{offline_notice_id}"
        form["method"] = "get"

    for tag in soup.find_all("link", href=True):
        if should_download_link(tag):
            continue
        rel_values = {str(value).lower() for value in tag.get("rel", [])}
        if rel_values & {"canonical", "alternate", "pingback", "search", "shortlink"}:
            tag["data-original-href"] = canonical_url(urljoin(page.url, tag.get("href", "")))
            del tag["href"]

    inject_offline_helpers(soup, offline_notice_id, relative_path(page_path, folder / "media.html"))


def rewrite_and_download_assets(
    soup: BeautifulSoup,
    page: ScrapedPage,
    page_path: Path,
    assets_folder: Path,
    downloaded_assets: dict[str, Path],
    session: requests.Session,
    timeout_seconds: float,
    log: LogCallback | None = None,
    stop_event: Event | None = None,
) -> None:
    for tag_name, attribute in ASSET_TAGS:
        for tag in soup.find_all(tag_name):
            if tag_name == "link" and not should_download_link(tag):
                continue
            value = tag.get(attribute)
            if not value or value.startswith(("data:", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(page.url, value)
            asset_path = download_asset(absolute, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)
            if asset_path:
                tag[attribute] = relative_path(page_path, asset_path)

    for tag in soup.find_all(srcset=True):
        rewritten = rewrite_srcset(
            tag.get("srcset", ""),
            page.url,
            page_path,
            assets_folder,
            downloaded_assets,
            session,
            timeout_seconds,
            log,
            stop_event,
        )
        if rewritten:
            tag["srcset"] = rewritten

    rewrite_media_data_attributes(soup, page, page_path, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)
    inject_video_fallbacks(soup)


def rewrite_media_data_attributes(
    soup: BeautifulSoup,
    page: ScrapedPage,
    page_path: Path,
    assets_folder: Path,
    downloaded_assets: dict[str, Path],
    session: requests.Session,
    timeout_seconds: float,
    log: LogCallback | None = None,
    stop_event: Event | None = None,
) -> None:
    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            attr_name = str(attribute).lower()
            if attr_name not in MEDIA_DATA_ATTRIBUTES:
                continue
            value = tag.get(attribute)
            if not isinstance(value, str) or not should_download_attribute_value(value):
                continue
            absolute = urljoin(page.url, value)
            asset_path = download_asset(absolute, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)
            if asset_path:
                tag[attribute] = relative_path(page_path, asset_path)


def inject_video_fallbacks(soup: BeautifulSoup) -> None:
    injected_sources: set[str] = set()
    for tag in soup.find_all(True):
        if not looks_like_media_container(tag):
            continue
        sources = video_sources_from_tag(tag)
        if not sources:
            continue
        if tag.find("video", attrs={"data-dos-scraper-video": "1"}):
            continue
        unique_sources = []
        for source in sources:
            key = media_source_key(source)
            if key in injected_sources:
                continue
            unique_sources.append(source)
            injected_sources.add(key)
        sources = unique_sources
        if not sources:
            continue

        video = soup.new_tag("video", controls=True)
        video["data-dos-scraper-video"] = "1"
        video["style"] = "display:block;width:100%;max-width:100%;height:auto;background:#000;"

        poster = poster_from_tag(tag)
        if poster:
            video["poster"] = poster

        for source_url in sources:
            source = soup.new_tag("source", src=source_url)
            suffix = Path(urlparse(source_url).path).suffix.lower()
            if suffix == ".mp4":
                source["type"] = "video/mp4"
            elif suffix == ".webm":
                source["type"] = "video/webm"
            video.append(source)

        fallback_text = soup.new_string("Your browser can open this downloaded video from the source above.")
        video.append(fallback_text)
        tag.insert(0, video)


def replace_external_video_embeds(soup: BeautifulSoup) -> None:
    for iframe in soup.find_all("iframe", src=True):
        src = str(iframe.get("src", ""))
        if not is_external_video_url(src):
            continue
        placeholder = soup.new_tag("div")
        placeholder["class"] = "dos-scraper-external-video"
        placeholder["style"] = (
            "display:flex;align-items:center;justify-content:center;min-height:220px;"
            "padding:20px;background:#111827;color:#f9fafb;border:1px solid #374151;"
            "font:15px/1.5 Segoe UI,Arial,sans-serif;text-align:center;"
        )

        link = soup.new_tag("a", href=src)
        link["style"] = "color:#93c5fd;text-decoration:underline;"
        link["data-original-href"] = src
        link.string = src

        placeholder.append("External video embed is not included in this offline export. Original URL: ")
        placeholder.append(link)
        iframe.replace_with(placeholder)


def download_discovered_media(
    page: ScrapedPage,
    page_path: Path,
    assets_folder: Path,
    downloaded_assets: dict[str, Path],
    session: requests.Session,
    timeout_seconds: float,
    log: LogCallback | None = None,
    stop_event: Event | None = None,
) -> None:
    for media_url in discover_direct_media_urls(page.raw_html, page.url):
        download_asset(media_url, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)


def media_records_for_page(
    page: ScrapedPage,
    soup: BeautifulSoup,
    page_path: Path,
    folder: Path,
    downloaded_assets: dict[str, Path],
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_record(kind: str, title: str, original_url: str, local_path: str = "", downloadable: bool = False) -> None:
        key = f"{kind}|{original_url}|{local_path}"
        if key in seen:
            return
        seen.add(key)
        records.append(
            {
                "kind": kind,
                "title": title or page.title or page.url,
                "page": page.url,
                "original_url": original_url,
                "local_path": local_path,
                "downloadable": "1" if downloadable else "0",
            }
        )

    for original_url, asset_path in downloaded_assets.items():
        if is_direct_media_url(original_url):
            add_record(
                "local",
                Path(urlparse(original_url).path).name,
                original_url,
                relative_path(folder / "media.html", asset_path),
                downloadable=False,
            )

    for media_url in discover_direct_media_urls(page.raw_html, page.url):
        asset_path = downloaded_assets.get(canonical_url(media_url))
        if asset_path:
            add_record("local", Path(urlparse(media_url).path).name, media_url, relative_path(folder / "media.html", asset_path))
        else:
            add_record("remote-direct", Path(urlparse(media_url).path).name, media_url, downloadable=True)

    for iframe in soup.find_all("iframe", src=True):
        src = str(iframe.get("src", ""))
        if is_external_video_url(src):
            add_record("external-embed", "External video embed", src)

    for stream in discover_stream_manifest_urls(page.raw_html):
        add_record("stream", Path(urlparse(stream).path).name or "Streaming manifest", stream)

    return records


def discover_direct_media_urls(html: str, base_url: str) -> list[str]:
    candidates: set[str] = set()
    normalized_html = html_unescape_js_escapes(html)
    quoted_pattern = r"""(?:"|')([^"']+(?:\.mp4|\.webm|\.ogv|\.ogg|\.mov|\.m4v|\.mp3|\.m4a|\.wav)(?:\?[^"']*)?)(?:"|')"""
    bare_pattern = r"""https?://[^\s"'<>]+(?:\.mp4|\.webm|\.ogv|\.ogg|\.mov|\.m4v|\.mp3|\.m4a|\.wav)(?:\?[^\s"'<>]*)?"""

    for match in re.findall(quoted_pattern, normalized_html, flags=re.IGNORECASE):
        candidates.add(urljoin(base_url, match.replace("\\/", "/")))
    for match in re.findall(bare_pattern, normalized_html, flags=re.IGNORECASE):
        candidates.add(match.replace("\\/", "/"))

    return sorted(url for url in candidates if is_direct_media_url(url))


def discover_stream_manifest_urls(html: str) -> list[str]:
    candidates: set[str] = set()
    normalized_html = html_unescape_js_escapes(html)
    for pattern in (
        r"""https?://[^\s"'<>]+\.m3u8(?:\?[^\s"'<>]*)?""",
        r"""https?://[^\s"'<>]+\.mpd(?:\?[^\s"'<>]*)?""",
    ):
        for match in re.findall(pattern, normalized_html, flags=re.IGNORECASE):
            candidates.add(match.replace("\\/", "/").replace("&amp;", "&"))

    for tag_match in re.findall(r"data-props=(['\"])(.*?)\1", normalized_html, flags=re.IGNORECASE | re.DOTALL):
        decoded = html_module.unescape(tag_match[1])
        if "Manifest" not in decoded:
            continue
        try:
            props = json.loads(decoded)
        except json.JSONDecodeError:
            continue
        for trailer in props.get("trailers", []):
            if not isinstance(trailer, dict):
                continue
            hls = trailer.get("hlsManifest")
            if isinstance(hls, str):
                candidates.add(hls)
            manifests = trailer.get("dashManifests")
            if isinstance(manifests, list):
                candidates.update(manifest for manifest in manifests if isinstance(manifest, str))
    return sorted(candidates)


def html_unescape_js_escapes(value: str) -> str:
    value = value.replace("\\/", "/")
    value = value.replace("&amp;", "&")
    return value


def is_direct_media_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(DIRECT_MEDIA_EXTENSIONS)


def is_external_video_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(
        domain in host
        for domain in (
            "youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
            "vimeo.com",
            "player.vimeo.com",
            "twitch.tv",
        )
    )


def video_sources_from_tag(tag) -> list[str]:
    sources: list[str] = []
    for attribute in VIDEO_SOURCE_ATTRIBUTES:
        value = tag.get(attribute)
        if isinstance(value, str) and value and not value.startswith(("http://", "https://")):
            sources.append(value)
    return list(dict.fromkeys(sources))


def looks_like_media_container(tag) -> bool:
    if tag.name in {"video", "audio"}:
        return True
    class_text = " ".join(str(value).lower() for value in tag.get("class", []))
    id_text = str(tag.get("id", "")).lower()
    marker = f"{class_text} {id_text}"
    return any(token in marker for token in ("highlight", "movie", "video", "player", "trailer", "screenshot"))


def media_source_key(source: str) -> str:
    parsed = urlparse(source)
    return parsed._replace(query="", fragment="").geturl()


def poster_from_tag(tag) -> str:
    for attribute in POSTER_ATTRIBUTES:
        value = tag.get(attribute)
        if isinstance(value, str) and value and not value.startswith(("http://", "https://")):
            return value
    return ""


def should_download_attribute_value(value: str) -> bool:
    value = value.strip()
    return bool(value) and not value.startswith(("data:", "mailto:", "tel:", "javascript:", "#"))


def rewrite_srcset(
    srcset: str,
    base_url: str,
    page_path: Path,
    assets_folder: Path,
    downloaded_assets: dict[str, Path],
    session: requests.Session,
    timeout_seconds: float,
    log: LogCallback | None = None,
    stop_event: Event | None = None,
) -> str:
    rewritten_parts: list[str] = []
    for part in srcset.split(","):
        pieces = part.strip().split()
        if not pieces:
            continue
        absolute = urljoin(base_url, pieces[0])
        asset_path = download_asset(absolute, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)
        if asset_path:
            pieces[0] = relative_path(page_path, asset_path)
        rewritten_parts.append(" ".join(pieces))
    return ", ".join(rewritten_parts)


def download_asset(
    url: str,
    assets_folder: Path,
    downloaded_assets: dict[str, Path],
    session: requests.Session,
    timeout_seconds: float,
    log: LogCallback | None = None,
    stop_event: Event | None = None,
) -> Path | None:
    if stop_event and stop_event.is_set():
        raise RuntimeError("Local website export stopped.")
    clean_url = canonical_url(url)
    if clean_url in downloaded_assets:
        return downloaded_assets[clean_url]

    try:
        if log:
            log(f"Downloading asset: {clean_url}")
        response = session.get(clean_url, timeout=timeout_seconds)
        response.raise_for_status()
    except Exception as exc:
        if log:
            log(f"Skipped asset ({exc}): {clean_url}")
        return None

    path = assets_folder / asset_filename(clean_url, response.headers.get("content-type", ""))
    path.write_bytes(response.content)
    downloaded_assets[clean_url] = path
    if is_css_asset(path, response.headers.get("content-type", "")):
        rewrite_css_urls(path, clean_url, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)
    return path


def rewrite_css_urls(
    css_path: Path,
    css_url: str,
    assets_folder: Path,
    downloaded_assets: dict[str, Path],
    session: requests.Session,
    timeout_seconds: float,
    log: LogCallback | None = None,
    stop_event: Event | None = None,
) -> None:
    try:
        css = css_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    def replace_url(match: re.Match[str]) -> str:
        raw_value = match.group(1).strip().strip("'\"")
        if not raw_value or raw_value.startswith(("data:", "about:", "#")):
            return match.group(0)
        absolute = urljoin(css_url, raw_value)
        asset_path = download_asset(absolute, assets_folder, downloaded_assets, session, timeout_seconds, log, stop_event)
        if not asset_path:
            return match.group(0)
        return f"url('{relative_path(css_path, asset_path)}')"

    rewritten = re.sub(r"url\(([^)]+)\)", replace_url, css)
    if rewritten != css:
        css_path.write_text(rewritten, encoding="utf-8")


def is_css_asset(path: Path, content_type: str) -> bool:
    return path.suffix.lower() == ".css" or "text/css" in content_type.lower()


def add_base_metadata(soup: BeautifulSoup, page: ScrapedPage) -> None:
    if not soup.html:
        return
    comment = soup.new_string(f" Saved by DOS Scraper from {page.url} at {datetime.now().isoformat(timespec='seconds')} ")
    soup.html.insert(0, comment)


def inject_offline_helpers(soup: BeautifulSoup, notice_id: str, media_library_href: str) -> None:
    if not soup.body:
        return
    if soup.find(id=notice_id):
        return
    inject_network_guard(soup)

    notice = soup.new_tag("div", id=notice_id)
    notice["style"] = (
        "position:sticky;top:0;z-index:2147483647;padding:10px 14px;"
        "background:#fef3c7;color:#422006;border-bottom:1px solid #f59e0b;"
        "font:14px/1.4 Segoe UI,Arial,sans-serif;"
    )
    notice.string = (
        "Offline local copy. Links that were not part of this export are disabled "
        "so this page will not navigate to the internet."
    )
    media_link = soup.new_tag("a", href=media_library_href)
    media_link["style"] = "margin-left:12px;color:#1d4ed8;font-weight:700;"
    media_link["data-dos-scraper-internal"] = "1"
    media_link.string = "Media Library"
    notice.append(media_link)
    soup.body.insert(0, notice)


def inject_network_guard(soup: BeautifulSoup) -> None:
    if soup.find("script", attrs={"data-dos-scraper-network-guard": "1"}):
        return
    script = soup.new_tag("script")
    script["data-dos-scraper-network-guard"] = "1"
    script.string = """
(function () {
  if (window.location.protocol !== 'file:') return;
  const blocked = function (url) {
    try {
      const parsed = new URL(String(url), window.location.href);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:' || parsed.protocol === 'ws:' || parsed.protocol === 'wss:';
    } catch (_) {
      return false;
    }
  };
  const originalFetch = window.fetch;
  window.fetch = function (resource, init) {
    const url = resource && resource.url ? resource.url : resource;
    if (blocked(url)) {
      console.info('DOS Scraper blocked network fetch in offline export:', url);
      return Promise.reject(new Error('Blocked network fetch in offline export'));
    }
    return originalFetch.apply(this, arguments);
  };
  const OriginalXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = function () {
    const xhr = new OriginalXHR();
    const open = xhr.open;
    xhr.open = function (method, url) {
      if (blocked(url)) {
        console.info('DOS Scraper blocked network XHR in offline export:', url);
        throw new Error('Blocked network XHR in offline export');
      }
      return open.apply(xhr, arguments);
    };
    return xhr;
  };
  window.WebSocket = function (url) {
    console.info('DOS Scraper blocked WebSocket in offline export:', url);
    throw new Error('Blocked WebSocket in offline export');
  };
})();
"""
    if soup.head:
        soup.head.insert(0, script)
    elif soup.body:
        soup.body.insert(0, script)


def write_local_index(folder: Path, pages: list[ScrapedPage], page_paths: dict[str, Path]) -> None:
    rows = []
    for page in pages:
        path = page_paths[canonical_url(page.url)]
        href = path.relative_to(folder).as_posix()
        title = page.title or page.url
        rows.append(f'<li><a href="{href}">{escape_html(title)}</a><br><small>{escape_html(page.url)}</small></li>')

    index = folder / "scraped-pages.html"
    index.write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Scraped Pages</title>"
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;line-height:1.45}"
        "li{margin:0 0 12px}</style></head><body><h1>Scraped Pages</h1><ol>"
        + "".join(rows)
        + "</ol></body></html>",
        encoding="utf-8",
    )


def write_media_library(folder: Path, records: list[dict[str, str]]) -> None:
    unique_records: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        key = f"{record.get('kind')}|{record.get('original_url')}|{record.get('local_path')}"
        if key in seen:
            continue
        seen.add(key)
        unique_records.append(record)

    rows = []
    remote_downloads = []
    for index, record in enumerate(unique_records, start=1):
        title = escape_html(record.get("title", "Media"))
        kind = escape_html(record.get("kind", "media"))
        page = escape_html(record.get("page", ""))
        original = record.get("original_url", "")
        local = record.get("local_path", "")

        preview = ""
        suffix = Path(urlparse(local or original).path).suffix.lower()
        if local and suffix in {".mp4", ".webm", ".ogv", ".mov", ".m4v"}:
            preview = f'<video controls src="{escape_html(local)}"></video>'
        elif local and suffix in {".mp3", ".m4a", ".wav", ".ogg"}:
            preview = f'<audio controls src="{escape_html(local)}"></audio>'

        links = []
        if local:
            links.append(f'<a href="{escape_html(local)}" download>Download local file</a>')
        if original:
            links.append(f'<a href="{escape_html(original)}" target="_blank" rel="noreferrer">Open original</a>')
        if record.get("downloadable") == "1" and original:
            remote_downloads.append(original)

        rows.append(
            "<article>"
            f"<h2>{index}. {title}</h2>"
            f"<div class=\"kind\">{kind}</div>"
            f"{preview}"
            f"<p class=\"page\">Page: {page}</p>"
            f"<p>{' | '.join(links)}</p>"
            "</article>"
        )

    download_urls_json = json.dumps(remote_downloads)
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Media Library</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; background: #101820; color: #e5edf5; }}
    a {{ color: #66c0f4; }}
    .toolbar {{ display: flex; gap: 10px; align-items: center; margin-bottom: 18px; }}
    button {{ padding: 8px 12px; cursor: pointer; }}
    article {{ border: 1px solid #2d4054; background: #162333; padding: 12px; margin: 0 0 12px; }}
    h2 {{ margin: 0 0 6px; font-size: 18px; }}
    .kind, .page {{ color: #9fb3c8; }}
    video, audio {{ display: block; width: 100%; max-width: 960px; margin: 10px 0; background: #000; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <a href="index.html">Back to saved page</a>
    <button type="button" onclick="downloadRemoteMedia()">Download remote direct media</button>
    <span>{len(unique_records)} media item(s)</span>
  </div>
  {''.join(rows) if rows else '<p>No media records were found for this export.</p>'}
  <script>
    const remoteDownloads = {download_urls_json};
    function downloadRemoteMedia() {{
      if (!remoteDownloads.length) {{
        alert('No direct remote media files were found outside the export.');
        return;
      }}
      remoteDownloads.forEach((url, index) => {{
        setTimeout(() => {{
          const a = document.createElement('a');
          a.href = url;
          a.download = '';
          a.rel = 'noreferrer';
          document.body.appendChild(a);
          a.click();
          a.remove();
        }}, index * 500);
      }});
    }}
  </script>
</body>
</html>
"""
    (folder / "media.html").write_text(document, encoding="utf-8")


def write_open_this_file(folder: Path) -> None:
    open_this = folder / "OPEN_THIS.html"
    open_this.write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"refresh\" content=\"0; url=index.html\">"
        "<title>Open Local Website</title></head>"
        "<body><p>Open <a href=\"index.html\">index.html</a> to view the saved local website.</p></body></html>",
        encoding="utf-8",
    )


def should_download_link(tag) -> bool:
    rel_values = {str(value).lower() for value in tag.get("rel", [])}
    href = str(tag.get("href", "")).lower()
    return bool(rel_values & {"stylesheet", "icon", "preload", "modulepreload"}) or href.endswith(
        (".css", ".ico", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".mp4", ".webm")
    )


def is_html_page(page: ScrapedPage) -> bool:
    content_type = page.content_type.lower()
    return "html" in content_type or page.raw_html.lstrip().startswith(("<!doctype", "<html"))


def folder_name(page: ScrapedPage, multi_page: bool) -> str:
    parsed = urlparse(page.url)
    host = safe_filename_part(parsed.netloc or "website")
    title = safe_filename_part(page.title or url_to_name(page.url) or "Store")
    if multi_page:
        return f"{host} - {title}"
    if parsed.netloc and title and title.lower() != host.lower():
        return f"{host} - {title}"
    return title or host or "webpage"


def url_to_name(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path.strip("/"))
    return path.rsplit("/", maxsplit=1)[-1] or parsed.netloc


def asset_filename(url: str, content_type: str) -> str:
    parsed = urlparse(url)
    source_name = Path(unquote(parsed.path)).name
    suffix = Path(source_name).suffix or suffix_for_content_type(content_type)
    stem = safe_slug(Path(source_name).stem or parsed.netloc or "asset")[:45]
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{stem}-{digest}{suffix}"


def suffix_for_content_type(content_type: str) -> str:
    content_type = content_type.lower().split(";", maxsplit=1)[0].strip()
    return {
        "text/css": ".css",
        "text/javascript": ".js",
        "application/javascript": ".js",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/ogg": ".ogv",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/wav": ".wav",
        "font/woff": ".woff",
        "font/woff2": ".woff2",
    }.get(content_type, ".bin")


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def relative_path(from_file: Path, to_file: Path) -> str:
    import os

    return os.path.relpath(to_file, start=from_file.parent).replace("\\", "/")


def unique_folder(folder: Path) -> Path:
    if not folder.exists():
        return folder
    for index in range(2, 1000):
        candidate = folder.with_name(f"{folder.name}-{index}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"Could not create a unique export folder for {folder}")


def unique_name(name: str, used_names: set[str]) -> str:
    candidate = name or "page"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    for index in range(2, 1000):
        numbered = f"{candidate}-{index}"
        if numbered not in used_names:
            used_names.add(numbered)
            return numbered
    raise ValueError(f"Could not create a unique filename for {name}")


def safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._").lower()
    return (value or "webpage")[:90]


def safe_filename_part(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "webpage")[:80]


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
