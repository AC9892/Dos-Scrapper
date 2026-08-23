from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ScrapeSettings:
    url: str
    mode: str
    delay_seconds: float = 1.0
    max_pages: int = 25
    crawl_depth: int = 2
    timeout_seconds: float = 15.0
    user_agent: str = "DOS-Scraper/0.1 (+desktop app)"
    respect_robots: bool = True
    stay_on_same_domain: bool = True
    restrict_to_starting_path: bool = False
    only_crawl_start_links: bool = False
    skip_direct_media_files: bool = True
    use_playwright: bool = False
    crawl_boundary: str = "Exact Host"
    url_filter_mode: str = "Blocklist"
    url_filters: list[dict[str, Any]] = field(default_factory=list)
    log_filtered_urls: bool = True
    allowed_resource_types: list[str] = field(
        default_factory=lambda: ["HTML", "Images", "Videos", "Audio", "Documents", "Archives", "Scripts", "Stylesheets", "Fonts", "Other"]
    )
    ignored_query_params: list[str] = field(default_factory=lambda: ["utm_source", "utm_medium", "utm_campaign", "fbclid", "gclid"])
    max_file_size_bytes: int | None = None
    bandwidth_limit_bytes_per_second: int | None = None
    max_concurrent_requests: int = 8
    max_connections_per_domain: int = 4
    requests_per_second: float | None = None
    retry_failed_requests: bool = True
    max_retries: int = 3
    retry_delay_seconds: float = 2.0
    exponential_backoff: bool = True
    max_redirects: int = 10
    selected_sitemap_urls: list[str] = field(default_factory=list)
    output_folder: Path = field(default_factory=lambda: Path.cwd() / "exports")


@dataclass(slots=True)
class ScrapedPage:
    url: str
    title: str = ""
    status_code: int | None = None
    content_type: str = ""
    description: str = ""
    keywords: str = ""
    canonical_url: str = ""
    final_url: str = ""
    resource_type: str = "HTML"
    content_length: int | None = None
    response_time_ms: int | None = None
    crawl_depth: int | None = None
    discovered_from: str = ""
    redirect_count: int = 0
    filter_status: str = ""
    matched_filter: str = ""
    retry_count: int = 0
    last_modified: str = ""
    etag: str = ""
    text: str = ""
    raw_html: str = field(default="", repr=False)
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    error: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def link_count(self) -> int:
        return len(self.links)

    @property
    def image_count(self) -> int:
        return len(self.images)

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "description": self.description,
            "keywords": self.keywords,
            "canonical_url": self.canonical_url,
            "final_url": self.final_url,
            "resource_type": self.resource_type,
            "content_length": self.content_length,
            "response_time_ms": self.response_time_ms,
            "crawl_depth": self.crawl_depth,
            "discovered_from": self.discovered_from,
            "redirect_count": self.redirect_count,
            "filter_status": self.filter_status,
            "matched_filter": self.matched_filter,
            "retry_count": self.retry_count,
            "last_modified": self.last_modified,
            "etag": self.etag,
            "text": self.text,
            "links": self.links,
            "images": self.images,
            "error": self.error,
            "scraped_at": self.scraped_at,
        }
