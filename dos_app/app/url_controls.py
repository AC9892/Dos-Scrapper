from __future__ import annotations

import posixpath
import re
from fnmatch import fnmatch
from urllib.parse import parse_qsl, urlencode, urlparse

from dos_app.app.models import ScrapeSettings


RESOURCE_EXTENSIONS = {
    "Images": {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp"},
    "Videos": {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpd", ".m3u8", ".webm"},
    "Audio": {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"},
    "Documents": {".csv", ".doc", ".docx", ".pdf", ".ppt", ".pptx", ".txt", ".xls", ".xlsx"},
    "Archives": {".7z", ".gz", ".iso", ".rar", ".tar", ".torrent", ".zip"},
    "Scripts": {".js", ".json"},
    "Stylesheets": {".css"},
    "Fonts": {".eot", ".otf", ".ttf", ".woff", ".woff2"},
}


def normalize_for_crawl(url: str, ignored_query_params: list[str] | None = None) -> str:
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"

    path = re.sub(r"/+", "/", parsed.path or "/")
    path = posixpath.normpath(path)
    if parsed.path.endswith("/") and not path.endswith("/"):
        path = f"{path}/"
    if path == ".":
        path = "/"

    ignored = {param.strip().lower() for param in ignored_query_params or [] if param.strip()}
    query_pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in ignored]
    query = urlencode(query_pairs, doseq=True)
    return parsed._replace(scheme=scheme, netloc=netloc, path=path, query=query, fragment="").geturl()


def crawl_boundary_allowed(start_url: str, url: str, boundary: str) -> bool:
    boundary = boundary or "Exact Host"
    start = urlparse(start_url)
    target = urlparse(url)
    start_host = (start.hostname or "").lower()
    target_host = (target.hostname or "").lower()
    if boundary == "Any Domain":
        return True
    if boundary == "Allow External Domains":
        return True
    if boundary == "Exact Host":
        return start_host == target_host
    start_domain = registrable_domain(start_host)
    target_domain = registrable_domain(target_host)
    if boundary == "Same Domain + Subdomains":
        return bool(start_domain and target_domain == start_domain)
    return start_domain == target_domain


def registrable_domain(hostname: str) -> str:
    parts = [part for part in hostname.split(".") if part]
    if len(parts) <= 2:
        return hostname
    return ".".join(parts[-2:])


def resource_type_for_url(url: str, content_type: str = "") -> str:
    content_type = content_type.lower()
    if "text/html" in content_type:
        return "HTML"
    if content_type.startswith("image/"):
        return "Images"
    if content_type.startswith("video/") or "dash+xml" in content_type or "mpegurl" in content_type:
        return "Videos"
    if content_type.startswith("audio/"):
        return "Audio"
    if "javascript" in content_type or "json" in content_type:
        return "Scripts"
    if "css" in content_type:
        return "Stylesheets"
    if "font" in content_type:
        return "Fonts"
    if any(token in content_type for token in ("pdf", "msword", "officedocument", "zip", "x-rar", "x-7z")):
        return "Documents"

    path = urlparse(url).path.lower()
    extension = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    for resource_type, extensions in RESOURCE_EXTENSIONS.items():
        if extension in extensions:
            return resource_type
    return "HTML" if not extension else "Other"


def resource_type_allowed(settings: ScrapeSettings, url: str, content_type: str = "") -> tuple[bool, str]:
    resource_type = resource_type_for_url(url, content_type)
    if resource_type not in settings.allowed_resource_types:
        return False, f"resource type disabled: {resource_type}"
    return True, ""


def filter_decision(settings: ScrapeSettings, url: str) -> tuple[bool, str]:
    rules = [rule for rule in settings.url_filters if rule.get("enabled", True) and str(rule.get("rule", "")).strip()]
    if not rules:
        if settings.url_filter_mode in {"Allowlist", "Blocklist + Allowlist"}:
            return False, "No allowlist rules configured"
        return True, ""

    block_matches = [rule for rule in rules if str(rule.get("action", "Block")).lower() == "block" and rule_matches(rule, url)]
    if block_matches and settings.url_filter_mode in {"Blocklist", "Blocklist + Allowlist"}:
        rule = block_matches[0]
        return False, f"Matched rule: {rule.get('type')} {rule.get('rule')}"

    allow_rules = [rule for rule in rules if str(rule.get("action", "Block")).lower() == "allow"]
    if settings.url_filter_mode in {"Allowlist", "Blocklist + Allowlist"} and not allow_rules:
        return False, "No allowlist rules configured"
    if settings.url_filter_mode in {"Allowlist", "Blocklist + Allowlist"} and allow_rules:
        allow_matches = [rule for rule in allow_rules if rule_matches(rule, url)]
        if not allow_matches:
            return False, "No allowlist rule matched"
    return True, ""


def rule_matches(rule: dict[str, object], url: str) -> bool:
    rule_type = str(rule.get("type", "Prefix"))
    value = str(rule.get("rule", "")).strip()
    parsed = urlparse(url)
    if not value:
        return False
    if rule_type == "Prefix":
        return url.startswith(value)
    if rule_type == "Domain":
        return (parsed.hostname or "").lower() == value.lower().lstrip("@")
    if rule_type == "Path Contains":
        return value in parsed.path
    if rule_type == "Exact URL":
        return url == value
    if rule_type == "File Extension":
        extension = value.lower() if value.startswith(".") else f".{value.lower()}"
        return parsed.path.lower().endswith(extension)
    if rule_type == "Wildcard":
        return fnmatch(url, value)
    if rule_type == "Regex":
        return bool(re.search(value, url))
    return False


def validate_filter_rule(rule_type: str, value: str) -> str:
    value = value.strip()
    if not value:
        return "Rule cannot be blank."
    if rule_type == "Regex":
        try:
            re.compile(value)
        except re.error as exc:
            return f"Invalid regex: {exc}"
    if rule_type == "Domain" and "/" in value:
        return "Domain rules should contain only a hostname, not a URL path."
    return ""
