from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


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
            "text": self.text,
            "links": self.links,
            "images": self.images,
            "error": self.error,
            "scraped_at": self.scraped_at,
        }
