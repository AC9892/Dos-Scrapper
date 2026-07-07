from __future__ import annotations

from bs4 import BeautifulSoup
from urllib.parse import urljoin

from dos_app.app.models import ScrapedPage


def parse_html(url: str, html: str, status_code: int | None = None, content_type: str = "") -> ScrapedPage:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description = _meta_content(soup, "description")
    keywords = _meta_content(soup, "keywords")

    canonical = ""
    canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical_tag and canonical_tag.get("href"):
        canonical = urljoin(url, canonical_tag["href"])

    links = sorted(
        {
            urljoin(url, anchor.get("href"))
            for anchor in soup.find_all("a", href=True)
            if anchor.get("href") and not anchor.get("href").startswith(("mailto:", "tel:", "javascript:"))
        }
    )
    images = sorted(
        {
            urljoin(url, image.get("src"))
            for image in soup.find_all("img", src=True)
            if image.get("src")
        }
    )
    text = "\n".join(line for line in soup.get_text("\n", strip=True).splitlines() if line.strip())

    return ScrapedPage(
        url=url,
        title=title,
        status_code=status_code,
        content_type=content_type,
        description=description,
        keywords=keywords,
        canonical_url=canonical,
        text=text,
        links=links,
        images=images,
    )


def _meta_content(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": f"og:{name}"})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""
