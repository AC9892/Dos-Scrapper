from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from dos_scraper.app.models import ScrapeSettings


APP_DIR = Path.home() / ".dos_scraper"
HISTORY_FILE = APP_DIR / "recent_jobs.json"


def load_recent_jobs(limit: int = 20) -> list[dict[str, object]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return data[:limit]


def save_recent_job(settings: ScrapeSettings) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    jobs = load_recent_jobs(limit=50)
    entry = asdict(settings)
    entry["output_folder"] = str(settings.output_folder)
    jobs = [job for job in jobs if job.get("url") != settings.url or job.get("mode") != settings.mode]
    jobs.insert(0, entry)
    HISTORY_FILE.write_text(json.dumps(jobs[:20], indent=2), encoding="utf-8")
