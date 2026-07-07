# DOS Scraper

A Windows-friendly desktop internet scraper built with Python and PySide6.

## Features

- Desktop GUI, not a web app
- Prefab scan types: Single Page, Whole Website, Page and Beyond, and Sitemap
- Extracts page text, links, images, title, description, keywords, canonical URL, status, and content type
- Live logs and progress
- Results preview table
- Export to CSV, JSON, TXT, HTML, and a local website folder
- Local website export saves HTML pages plus referenced images, CSS, JavaScript, fonts, and media assets where available
- Delay, max pages, crawl depth, timeout, user-agent, output folder, and robots.txt settings
- URL rules preview before starting
- Skip logging for duplicate URLs, outside-domain URLs, outside-path URLs, crawl-depth skips, and robots.txt blocks
- Optional skipping of direct media/file URLs during page scans for faster sitemap and crawl runs
- Robots.txt is respected by default
- Recent jobs saved locally
- Optional Playwright rendering for JavaScript-heavy pages

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python .\run.py
```

For optional Playwright mode:

```powershell
pip install playwright
playwright install chromium
```

## Run

```powershell
python .\run.py
```

## Local Website Export

Choose `Local Website` in the export dropdown after a scrape. The app creates a folder under `exports` named from the website or page, for example:

```text
exports/
  example.com/
    index.html
    scraped-pages.html
    pages/
    assets/
```

Open `index.html` to view the first scraped page locally. Crawled pages are written into `pages/`, and links between scraped pages are rewritten to local files.

## Prefab Scan Types

- `Single Page`: scrapes only the exact entered URL.
- `Whole Website`: crawls reachable links on the same domain, bounded by max pages and crawl depth.
- `Page and Beyond`: starts from a specific page and crawls discovered links. Enable `Restrict to starting path` to keep results under the entered URL path.
- `Sitemap`: loads sitemap URLs and lets you choose which URLs to scan before scraping starts.

## Faster Scans

- Set `Delay` to `0` or `0.10` for sites you are allowed to scan quickly.
- Keep `Use Playwright rendering` off unless the page needs JavaScript rendering.
- Keep `Skip direct media files` on to avoid spending page-scan requests on image, PDF, font, archive, and script URLs.
- Lower `Max pages` and `Crawl depth` when testing a scan.

## Build A Windows EXE

```powershell
pip install pyinstaller
pyinstaller --name DOS-Scraper --windowed --onefile .\run.py
```

If you need Playwright inside a packaged app, build and test that path separately because browser binaries must be bundled or installed on the target machine.
