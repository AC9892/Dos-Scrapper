# DOS Scraper

DOS Scraper is a Windows-friendly desktop internet scraper built with Python, PySide6, Requests, and BeautifulSoup. It is a native GUI app, not a web app. The app is designed for collecting page text, links, images, metadata, and local offline snapshots from websites you are allowed to access.

## Authorized Use Disclaimer

DOS Scraper is provided for educational, research, archival, development, and authorized testing purposes only.

Users are responsible for ensuring they have permission to access, scrape, crawl, download, or otherwise interact with any website, server, or service they target. Users are also responsible for complying with applicable laws, website terms of service, `robots.txt` policies, rate limits, and other restrictions imposed by the target service.

The developers and contributors of DOS Scraper are not responsible for misuse of the software, unauthorized scraping, service disruption, excessive network traffic, data loss, account restrictions, legal consequences, or violations of third-party terms or policies.

Features such as bandwidth controls, request limits, crawl boundaries, URL filtering, and `robots.txt` support are provided to help users operate the scraper responsibly. They do not grant permission to access or collect content that the user is otherwise unauthorized to access.

**Only use DOS Scraper against websites and resources that you own, have permission to access, or are otherwise legally permitted to scrape.**

**The developer is not responsible for any scraping activity performed in violation of this requirement or for any misuse of DOS Scraper by its users.**

## What It Does
- Scrapes single pages, crawls websites, scans page neighborhoods, and imports sitemap URLs.
- Extracts titles, descriptions, keywords, canonical URLs, page text, links, images, status codes, content types, redirect counts, response timing, retry counts, and resource metadata.
- Shows live progress, logs, skipped URL reasons, and a searchable results table.
- Exports scrape data as CSV, JSON, TXT, HTML, or a local website folder.
- Saves recent scrape jobs locally so previous settings can be reused.
- Supports optional Playwright rendering for JavaScript-heavy pages.
- Respects `robots.txt` by default and includes a robots checker.
- Handles common errors without crashing the GUI.

## What It Does Not Do

- It does not bypass CAPTCHA, Cloudflare challenges, login walls, paywalls, or access controls.
- It does not guarantee perfect offline copies of every modern website. Some sites depend on live APIs, streaming manifests, authentication, or browser-side scripts.
- It does not ignore website permissions automatically. Use it only where you have permission and follow the target site's terms and robots policy.

## Main Features

### Desktop GUI

The app includes:

- Large URL input box.
- Start, Stop, and Pause controls.
- Scan Type dropdown.
- Recent job dropdown.
- Live log panel.
- Progress bar.
- Results preview table.
- Details panel for selected results.
- Output folder picker.
- Export format selector.
- Auto-export option.

### Scan Types

`Single Page`

Scrapes only the exact URL entered. It does not follow links.

`Whole Website`

Starts from a base URL and crawls reachable internal pages. It stays within the configured crawl boundary and respects max pages, crawl depth, delay, timeout, duplicate detection, filters, and robots settings.

`Page and Beyond`

Starts from one specific page and crawls links discovered from that page and subsequent pages. This is useful when you want to scan one section of a site instead of the whole domain.

Options that matter most for this mode:

- `Restrict to starting path`: keeps crawled URLs under the starting URL path.
- `Only crawl links discovered from start page`: limits crawl expansion around the entered page.
- `Stay on same domain`: prevents leaving the starting domain.

`Sitemap`

Loads sitemap URLs from a provided sitemap URL, from `robots.txt`, or from `/sitemap.xml`. The app then lets you choose which sitemap URLs to scrape before the job starts.

### Crawl Controls

Available crawl settings include:

- Max pages.
- Crawl depth.
- Delay between requests.
- Timeout.
- User-agent.
- Same-domain toggle.
- Starting-path restriction.
- Direct media/file URL skipping.
- `robots.txt` enforcement.
- Optional Playwright rendering.
- Crawl boundary mode.

### Networking Controls

The app includes controls for:

- Bandwidth limit.
- Max file size.
- Requests per second.
- Retry failed requests.
- Retry delay.
- Exponential backoff.
- Max redirects.
- Concurrent request settings.
- Per-domain connection settings.

These settings help keep scans controlled and reduce the chance of freezing or overloading a target site.

### URL Filtering

The Filters panel supports:

- Blocklist mode.
- Allowlist mode.
- Blocklist + Allowlist mode.
- Regex/domain/path-style filter rules.
- Enabled/disabled filter rows.
- Import/export filter JSON.
- Reset filters.
- Ignored query parameters, such as tracking parameters.
- Resource type toggles for HTML, images, videos, audio, documents, archives, scripts, stylesheets, fonts, and other files.

Skipped URLs are logged with reasons such as:

- Duplicate URL.
- Outside crawl boundary.
- Outside starting path.
- Outside crawl depth.
- Direct media/file URL.
- Blocked by `robots.txt`.
- Filtered URL.
- Disabled resource type.

### Robots.txt Support

`Respect robots.txt` is enabled by default. The app can also check and summarize a site's `robots.txt` rules from the GUI.

If `robots.txt` is missing, standard crawler behavior treats that as no robots restrictions. If a rule blocks the configured user-agent, DOS Scraper skips the URL and logs the reason.

### Playwright Mode

Playwright rendering is optional and intended for JavaScript-heavy pages. Keep it off for faster normal scans. Enable it when Requests and BeautifulSoup cannot see the content because the page is rendered by JavaScript.

Playwright requires extra setup:

```powershell
pip install playwright
playwright install chromium
```

## Export Formats

DOS Scraper can export scrape results as:

- `CSV`: spreadsheet-friendly result rows.
- `JSON`: detailed structured data.
- `TXT`: readable text report with page sections.
- `HTML`: readable report for browser viewing.
- `Local Website`: offline website-style export with rewritten links and downloaded assets where available.

Export filenames include a website/page identifier so results are easier to recognize later.

## Local Website Export

The Local Website export creates a folder under the selected output folder. The folder name is based on the website host and page title.

Example structure:

```text
exports/
  example.com - Example Page/
    OPEN_THIS.html
    index.html
    scraped-pages.html
    media.html
    pages/
    assets/
```

Files:

- `OPEN_THIS.html`: quick launcher for the saved local website.
- `index.html`: first saved page or local redirect/index page.
- `scraped-pages.html`: list of scraped pages included in the export.
- `media.html`: media library for discovered/downloaded media records.
- `pages/`: additional saved HTML pages.
- `assets/`: downloaded CSS, images, scripts, fonts, and direct media assets where available.

The exporter rewrites internal links between scraped pages to local files. Links that were not included in the export are disabled and annotated with their original URL.

The local export also blocks live network fetch/XHR/WebSocket calls inside the offline copy so a saved page does not quietly depend on the internet.

### Media Notes

The exporter attempts to preserve direct media URLs and downloadable media assets when they are available in the page HTML. External embeds, streaming manifests, protected media, and media loaded only through private APIs may not become fully offline.

The media library includes local download links for saved files and original links for remote direct media when available.

### Not-Found Page Handling

The app detects common 404/not-found pages and skips them during local website export. It also skips downloaded HTML assets so a site's error page does not get saved as an image/script/style asset by mistake.

## Recent Jobs

Recent scrape jobs are stored locally at:

```text
C:\Users\{User}\.dos_app/recent_jobs.json
```

This file is runtime data and should not be committed to GitHub.

## Setup

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the app:

```powershell
python .\run.py
```

Or run the package directly:

```powershell
python -m dos_app
```

On Windows, you can also use:

```powershell
.\run.bat
```

The batch file tries to find a usable Python interpreter, installs requirements if needed, and launches the app.

## Requirements

Required:

- Python 3.10 or newer recommended.
- PySide6.
- Requests.
- BeautifulSoup4.
- lxml.

Optional:

- Playwright, for JavaScript-rendered pages.
- PyInstaller, for building a Windows executable.

## Faster Scans

To speed up safe, permitted scans:

- Start with a conservative delay and increase throughput only when appropriate for the target
- Keep Playwright off unless the page needs JavaScript rendering.
- Keep direct media/file skipping enabled for normal page discovery.
- Lower max pages while testing.
- Lower crawl depth when you only need nearby pages.
- Use sitemap mode when the site has a clean sitemap.
- Use filters to avoid irrelevant paths.
- Use requests-per-second and bandwidth controls instead of letting scans run completely unbounded.

## Build A Windows EXE

Install PyInstaller:

```powershell
pip install pyinstaller
```

Build:

```powershell
pyinstaller --name DOS-Scraper --windowed --onefile .\run.py
```

The output will be created under `dist/`.

If you package Playwright mode, test that separately. Playwright browser binaries must be installed or bundled for the target machine.

## GitHub Upload Notes

Commit these:

```text
.gitattributes
.gitignore
README.md
requirements.txt
run.py
run.bat
dos_app/
exports/.gitkeep
```

Do not commit generated/local data:

```text
.venv/
__pycache__/
exports/*
backups/
.agents/
.codex/
build/
dist/
*.exe
*.msi
*.spec
```

The included `.gitignore` already excludes those files and folders while keeping `exports/.gitkeep` so the export folder exists in a fresh clone.

## Project Layout

```text
dos_app/
  __main__.py
  app/
    exporters.py
    history.py
    local_mirror.py
    main.py
    models.py
    networking.py
    parser.py
    scraper.py
    url_controls.py
  assets/
    app_logo.ico
    app_logo.png
exports/
  .gitkeep
requirements.txt
run.py
run.bat
```

## Responsible Use

Use DOS Scraper only on websites and resources you are authorized to scrape.

For larger crawls:

- Respect the target site's policies and `robots.txt`.
- Use reasonable request and concurrency limits.
- Configure bandwidth limits where appropriate.
- Avoid unnecessarily downloading large resources.
- Use crawl boundaries and URL filters to prevent unintended crawling.
- Stop or reduce a crawl if it is negatively affecting the target service.

`robots.txt` enforcement is enabled by default and should generally remain enabled unless you have authorization and a specific reason to change it.
