from __future__ import annotations

import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dos_scraper.app.models import ScrapedPage


EXPORT_COLUMNS = [
    "source_site",
    "url",
    "domain",
    "path",
    "title",
    "status_code",
    "content_type",
    "description",
    "keywords",
    "canonical_url",
    "text_length",
    "word_count",
    "html_length",
    "link_count",
    "internal_link_count",
    "external_link_count",
    "image_count",
    "document_count",
    "video_count",
    "links",
    "internal_links",
    "external_links",
    "images",
    "documents",
    "videos",
    "text",
    "error",
    "scraped_at",
]

DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".zip", ".rar", ".7z")
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv", ".mpd", ".m3u8")


def export_results(results: list[ScrapedPage], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        _export_csv(results, path)
    elif suffix == ".json":
        _export_json(results, path)
    elif suffix == ".txt":
        _export_txt(results, path)
    elif suffix in {".html", ".htm"}:
        _export_html(results, path)
    else:
        raise ValueError(f"Unsupported export format: {suffix}")
    _export_page_files(results, companion_folder(path))


def _serializable(page: ScrapedPage) -> dict[str, object]:
    data = _page_export_data(page)
    for key in ("links", "internal_links", "external_links", "images", "documents", "videos"):
        data[key] = "\n".join(data[key])
    return data


def _export_csv(results: list[ScrapedPage], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for page in results:
            writer.writerow(_serializable(page))


def _export_json(results: list[ScrapedPage], path: Path) -> None:
    source_sites = _source_sites(results)
    with path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "source_site": ", ".join(source_sites) if source_sites else "",
                "source_sites": source_sites,
                "page_count": len(results),
                "totals": _export_totals(results),
                "pages": [_page_export_data(page) for page in results],
            },
            file,
            indent=2,
            ensure_ascii=False,
        )


def _export_txt(results: list[ScrapedPage], path: Path) -> None:
    with path.open("w", encoding="utf-8") as file:
        totals = _export_totals(results)
        source_sites = _source_sites(results)
        file.write("DOS Scraper Export\n")
        file.write(f"Source Site: {', '.join(source_sites) if source_sites else 'Unknown'}\n")
        file.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        file.write(f"Pages: {totals['pages']}\n")
        file.write(f"Links: {totals['links']}\n")
        file.write(f"Images: {totals['images']}\n")
        file.write(f"Documents: {totals['documents']}\n")
        file.write(f"Videos: {totals['videos']}\n")
        file.write(f"Errors: {totals['errors']}\n")
        file.write("\n" + "=" * 96 + "\n\n")
        for page in results:
            data = _page_export_data(page)
            file.write(page_file_text(page))
            file.write("\n\nCounts:\n")
            file.write(f"  Links: {data['link_count']} ({data['internal_link_count']} internal, {data['external_link_count']} external)\n")
            file.write(f"  Images: {data['image_count']}\n")
            file.write(f"  Documents: {data['document_count']}\n")
            file.write(f"  Videos: {data['video_count']}\n")
            file.write(f"  Text Length: {data['text_length']} chars / {data['word_count']} words\n")
            file.write("\nLinks:\n")
            file.write("\n".join(data["links"]))
            file.write("\n\nImages:\n")
            file.write("\n".join(data["images"]))
            file.write("\n\nDocuments:\n")
            file.write("\n".join(data["documents"]))
            file.write("\n\nVideos:\n")
            file.write("\n".join(data["videos"]))
            file.write("\n\n" + "=" * 96 + "\n\n")


def _export_html(results: list[ScrapedPage], path: Path) -> None:
    totals = _export_totals(results)
    source_sites = _source_sites(results)
    source_label = ", ".join(source_sites) if source_sites else "Unknown site"
    rows = []
    detail_sections = []
    for index, page in enumerate(results, start=1):
        data = _page_export_data(page)
        search_text = " ".join(
            [
                str(data["url"]),
                str(data["title"]),
                str(data["description"]),
                str(data["keywords"]),
                str(data["content_type"]),
                str(data["error"]),
                str(data["text"])[:4000],
            ]
        )
        rows.append(
            f'<tr data-search="{html.escape(search_text.lower(), quote=True)}">'
            f'<td><a href="#page-{index}">{index}</a></td>'
            f'<td><a href="{html.escape(page.url, quote=True)}">{html.escape(page.url)}</a></td>'
            f"<td>{html.escape(page.title)}</td>"
            f"<td>{page.status_code or ''}</td>"
            f"<td>{html.escape(page.content_type)}</td>"
            f"<td>{data['link_count']}</td>"
            f"<td>{data['image_count']}</td>"
            f"<td>{data['document_count']}</td>"
            f"<td>{data['video_count']}</td>"
            f"<td>{data['word_count']}</td>"
            f"<td>{html.escape(page.error)}</td>"
            "</tr>"
        )
        detail_sections.append(_html_page_detail(index, page, data))
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>DOS Scraper Export - {html.escape(source_label)}</title>
  <style>
    :root {{ color-scheme: dark; --bg:#101214; --panel:#181b1f; --line:#323842; --text:#f4f4f4; --muted:#aab2bd; --blue:#3d7eff; --ok:#4cd964; --warn:#ffd60a; --bad:#ff453a; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--text); }}
    header {{ position: sticky; top: 0; z-index: 2; background: #0c0e10; border-bottom: 1px solid var(--line); padding: 14px 18px; }}
    h1 {{ margin: 0 0 8px; font-size: 20px; }}
    h2 {{ margin: 24px 0 10px; font-size: 16px; }}
    h3 {{ margin: 0 0 8px; font-size: 15px; }}
    a {{ color: #8ab4ff; }}
    main {{ padding: 18px; }}
    input {{ width: 100%; max-width: 720px; background: #0b0d0f; color: var(--text); border: 1px solid var(--line); padding: 7px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    th, td {{ border: 1px solid var(--line); padding: 6px 7px; vertical-align: top; }}
    th {{ background: #20242a; text-align: left; position: sticky; top: 89px; }}
    tr:nth-child(even) td {{ background: #14171b; }}
    .cards {{ display: grid; grid-template-columns: repeat(6, minmax(110px, 1fr)); gap: 8px; margin-top: 12px; }}
    .card, .page {{ background: var(--panel); border: 1px solid var(--line); padding: 10px; }}
    .card strong {{ display: block; font-size: 18px; }}
    .card span, .muted {{ color: var(--muted); }}
    .page {{ margin: 12px 0; }}
    .meta {{ display: grid; grid-template-columns: 160px 1fr; gap: 4px 10px; font-size: 12px; }}
    .label {{ color: var(--muted); }}
    details {{ margin-top: 8px; border-top: 1px solid var(--line); padding-top: 8px; }}
    summary {{ cursor: pointer; color: #cfe1ff; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #0b0d0f; border: 1px solid var(--line); padding: 8px; max-height: 360px; overflow: auto; }}
    ul {{ margin: 6px 0 0 18px; padding: 0; max-height: 320px; overflow: auto; }}
    .error {{ color: var(--bad); }}
    .ok {{ color: var(--ok); }}
  </style>
</head>
<body>
  <header>
    <h1>DOS Scraper Export</h1>
    <div><strong>Source Site:</strong> {html.escape(source_label)}</div>
    <div class="muted">Generated {html.escape(datetime.now().isoformat(timespec="seconds"))}</div>
    <div class="cards">
      <div class="card"><strong>{totals['pages']}</strong><span>Pages</span></div>
      <div class="card"><strong>{totals['links']}</strong><span>Links</span></div>
      <div class="card"><strong>{totals['images']}</strong><span>Images</span></div>
      <div class="card"><strong>{totals['documents']}</strong><span>Documents</span></div>
      <div class="card"><strong>{totals['videos']}</strong><span>Videos</span></div>
      <div class="card"><strong>{totals['errors']}</strong><span>Errors</span></div>
    </div>
  </header>
  <main>
  <input id="filter" type="search" placeholder="Filter URLs, titles, metadata, text, errors">
  <h2>Pages</h2>
  <table>
    <thead><tr><th>#</th><th>URL</th><th>Title</th><th>Status</th><th>Content Type</th><th>Links</th><th>Images</th><th>Docs</th><th>Videos</th><th>Words</th><th>Error</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <h2>Page Details</h2>
  {''.join(detail_sections)}
  </main>
  <script>
    const filter = document.getElementById('filter');
    filter.addEventListener('input', () => {{
      const q = filter.value.trim().toLowerCase();
      document.querySelectorAll('tbody tr').forEach(row => {{
        row.style.display = !q || row.dataset.search.includes(q) ? '' : 'none';
      }});
    }});
  </script>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def _page_export_data(page: ScrapedPage) -> dict[str, object]:
    parsed = urlparse(page.url)
    internal_links, external_links = _split_links(page)
    documents = _matching_extensions(page.links, DOCUMENT_EXTENSIONS)
    videos = _matching_extensions(page.links + page.images, VIDEO_EXTENSIONS)
    words = page.text.split()
    return {
        "source_site": parsed.netloc,
        "url": page.url,
        "domain": parsed.netloc,
        "path": parsed.path or "/",
        "title": page.title,
        "status_code": page.status_code,
        "content_type": page.content_type,
        "description": page.description,
        "keywords": page.keywords,
        "canonical_url": page.canonical_url,
        "text_length": len(page.text),
        "word_count": len(words),
        "html_length": len(page.raw_html),
        "link_count": len(page.links),
        "internal_link_count": len(internal_links),
        "external_link_count": len(external_links),
        "image_count": len(page.images),
        "document_count": len(documents),
        "video_count": len(videos),
        "links": page.links,
        "internal_links": internal_links,
        "external_links": external_links,
        "images": page.images,
        "documents": documents,
        "videos": videos,
        "text": page.text,
        "error": page.error,
        "scraped_at": page.scraped_at,
    }


def _export_totals(results: list[ScrapedPage]) -> dict[str, int]:
    return {
        "pages": len(results),
        "links": sum(len(page.links) for page in results),
        "images": sum(len(page.images) for page in results),
        "documents": sum(len(_matching_extensions(page.links, DOCUMENT_EXTENSIONS)) for page in results),
        "videos": sum(len(_matching_extensions(page.links + page.images, VIDEO_EXTENSIONS)) for page in results),
        "errors": sum(1 for page in results if page.error),
    }


def _source_sites(results: list[ScrapedPage]) -> list[str]:
    sites = []
    seen = set()
    for page in results:
        domain = urlparse(page.url).netloc
        if domain and domain not in seen:
            seen.add(domain)
            sites.append(domain)
    return sites


def _split_links(page: ScrapedPage) -> tuple[list[str], list[str]]:
    page_domain = urlparse(page.url).netloc.lower()
    internal: list[str] = []
    external: list[str] = []
    for link in page.links:
        link_domain = urlparse(link).netloc.lower()
        if not link_domain or link_domain == page_domain:
            internal.append(link)
        else:
            external.append(link)
    return internal, external


def _matching_extensions(values: list[str], extensions: tuple[str, ...]) -> list[str]:
    return sorted({value for value in values if urlparse(value).path.lower().endswith(extensions)})


def _html_page_detail(index: int, page: ScrapedPage, data: dict[str, object]) -> str:
    return f"""
    <section class="page" id="page-{index}">
      <h3>{index}. {html.escape(str(data['title']) or str(data['url']))}</h3>
      <div class="meta">
        <div class="label">URL</div><div><a href="{html.escape(str(data['url']), quote=True)}">{html.escape(str(data['url']))}</a></div>
        <div class="label">Domain</div><div>{html.escape(str(data['domain']))}</div>
        <div class="label">Path</div><div>{html.escape(str(data['path']))}</div>
        <div class="label">Status</div><div>{html.escape(str(data['status_code'] or ''))}</div>
        <div class="label">Content Type</div><div>{html.escape(str(data['content_type']))}</div>
        <div class="label">Canonical URL</div><div>{html.escape(str(data['canonical_url']))}</div>
        <div class="label">Description</div><div>{html.escape(str(data['description']))}</div>
        <div class="label">Keywords</div><div>{html.escape(str(data['keywords']))}</div>
        <div class="label">Size</div><div>{data['text_length']} text chars / {data['word_count']} words / {data['html_length']} HTML chars</div>
        <div class="label">Counts</div><div>{data['link_count']} links, {data['image_count']} images, {data['document_count']} documents, {data['video_count']} videos</div>
        <div class="label">Scraped</div><div>{html.escape(str(data['scraped_at']))}</div>
        <div class="label">Error</div><div class="{'error' if page.error else 'ok'}">{html.escape(page.error or 'None')}</div>
      </div>
      {_html_details_list("Internal Links", data["internal_links"])}
      {_html_details_list("External Links", data["external_links"])}
      {_html_details_list("Images", data["images"])}
      {_html_details_list("Documents", data["documents"])}
      {_html_details_list("Videos", data["videos"])}
      <details>
        <summary>Extracted Text</summary>
        <pre>{html.escape(str(data["text"]))}</pre>
      </details>
    </section>
    """


def _html_details_list(title: str, values: object) -> str:
    items = list(values) if isinstance(values, list) else []
    if not items:
        body = '<div class="muted">None found.</div>'
    else:
        body = "<ul>" + "".join(f'<li><a href="{html.escape(value, quote=True)}">{html.escape(value)}</a></li>' for value in items) + "</ul>"
    return f"<details><summary>{html.escape(title)} ({len(items)})</summary>{body}</details>"


def companion_folder(path: Path) -> Path:
    return path.with_suffix("").with_name(f"{path.stem}_files")


def _export_page_files(results: list[ScrapedPage], folder: Path) -> None:
    pages_folder = folder / "pages"
    source_folder = folder / "source-html"
    links_folder = folder / "links"
    images_folder = folder / "images"
    documents_folder = folder / "documents"
    videos_folder = folder / "videos"
    pages_folder.mkdir(parents=True, exist_ok=True)
    source_folder.mkdir(parents=True, exist_ok=True)
    links_folder.mkdir(parents=True, exist_ok=True)
    images_folder.mkdir(parents=True, exist_ok=True)
    documents_folder.mkdir(parents=True, exist_ok=True)
    videos_folder.mkdir(parents=True, exist_ok=True)

    index_rows = ["# Scraped Files", ""]
    for index, page in enumerate(results, start=1):
        name = f"{index:04d}-{safe_name(page)}"
        page_path = pages_folder / f"{name}.txt"
        source_path = source_folder / f"{name}.html"
        links_path = links_folder / f"{name}-links.txt"
        images_path = images_folder / f"{name}-images.txt"
        documents_path = documents_folder / f"{name}-documents.txt"
        videos_path = videos_folder / f"{name}-videos.txt"
        data = _page_export_data(page)

        page_path.write_text(page_file_text(page), encoding="utf-8")
        if page.raw_html:
            source_path.write_text(page.raw_html, encoding="utf-8")
        links_path.write_text("\n".join(page.links), encoding="utf-8")
        images_path.write_text("\n".join(page.images), encoding="utf-8")
        documents_path.write_text("\n".join(data["documents"]), encoding="utf-8")
        videos_path.write_text("\n".join(data["videos"]), encoding="utf-8")

        index_rows.append(f"{index}. {page.title or page.url}")
        index_rows.append(f"   Source Site: {urlparse(page.url).netloc or 'Unknown'}")
        index_rows.append(f"   URL: {page.url}")
        index_rows.append(f"   Text: {page_path.relative_to(folder)}")
        if page.raw_html:
            index_rows.append(f"   Source HTML: {source_path.relative_to(folder)}")
        index_rows.append(f"   Links: {links_path.relative_to(folder)}")
        index_rows.append(f"   Images: {images_path.relative_to(folder)}")
        index_rows.append(f"   Documents: {documents_path.relative_to(folder)}")
        index_rows.append(f"   Videos: {videos_path.relative_to(folder)}")
        index_rows.append("")

    (folder / "index.txt").write_text("\n".join(index_rows), encoding="utf-8")


def page_file_text(page: ScrapedPage) -> str:
    lines = [
        f"Source Site: {urlparse(page.url).netloc or 'Unknown'}",
        f"URL: {page.url}",
        f"Title: {page.title}",
        f"Status: {page.status_code or ''}",
        f"Content-Type: {page.content_type}",
        f"Description: {page.description}",
        f"Keywords: {page.keywords}",
        f"Canonical URL: {page.canonical_url}",
        f"Scraped At: {page.scraped_at}",
    ]
    if page.error:
        lines.append(f"Error: {page.error}")
    lines.extend(["", "Text:", page.text])
    return "\n".join(lines)


def safe_name(page: ScrapedPage) -> str:
    parsed = urlparse(page.url)
    source = page.title or parsed.path.strip("/") or parsed.netloc or "page"
    source = re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip("-._")
    return (source or "page")[:80]
