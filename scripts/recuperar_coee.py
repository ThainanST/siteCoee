#!/usr/bin/env python3
"""Recupera uma cópia histórica do site do COEE a partir do Internet Archive."""

from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import mimetypes
import posixpath
import re
import sys
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = ROOT / "arquivo-coee"
RAW_ROOT = ARCHIVE_ROOT / "raw"
DOCS_ROOT = ROOT / "docs"
MANIFEST_PATH = ARCHIVE_ROOT / "manifesto.json"
REFERENCE_TIMESTAMP = "20240727095454"
START_URL = "https://coee.ufop.br/"
WAYBACK_HOST = "web.archive.org"
USER_AGENT = "COEE-Historical-Copy/1.0 (+https://github.com/ThainanST/siteCoee)"
TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 2
REQUEST_DELAY_SECONDS = 0.2
DEFAULT_MAX_DOWNLOADS = 20

ARCHIVE_RE = re.compile(r"^/web/(\d{1,14})(?:[a-z]+_)?/(https?://.*)$", re.I)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
CSS_IMPORT_RE = re.compile(r"""@import\s+(['"])(.*?)\1""", re.I)
SCRIPT_ROOT_URL_RE = re.compile(
    r"""(?P<quote>["'])(?P<url>/(?:profiles|sites|misc|modules)/[^"'\s\\]*)(?P=quote)""",
    re.I,
)
WAYBACK_TOOLBAR_RE = re.compile(
    r"<!--\s*BEGIN WAYBACK TOOLBAR INSERT\s*-->.*?"
    r"<!--\s*END WAYBACK TOOLBAR INSERT\s*-->",
    re.I | re.S,
)
ADMIN_PATH_RE = re.compile(r"^/(?:user|admin|node/add|system|batch)(?:/|$)", re.I)
DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".zip", ".rar",
}
ASSET_EXTENSIONS = {
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3",
}


@dataclass
class WorkItem:
    url: str
    found_on: str
    kind: str
    requested_capture: str = REFERENCE_TIMESTAMP


def canonical_url(value: str, base: str | None = None) -> str | None:
    """Resolve ordinary and Wayback-rewritten links to their original URL."""
    value = html.unescape(value.strip())
    if not value or value.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    if value.startswith("/web/") and re.match(r"^/web/\d{1,14}(?:[a-z]+_)?/", value, re.I):
        value = f"https://{WAYBACK_HOST}{value}"
    elif base:
        value = urljoin(base, value)
    parts = urlsplit(value)
    if parts.netloc.lower() == WAYBACK_HOST:
        match = ARCHIVE_RE.match(parts.path)
        if match:
            value = match.group(2)
            if parts.query:
                value += ("&" if "?" in value else "?") + parts.query
            parts = urlsplit(value)
        else:
            non_http = re.match(r"^/web/\d{1,14}/(mailto|tel):(.+)$", parts.path, re.I)
            if non_http:
                return f"{non_http.group(1).lower()}:{non_http.group(2)}"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~%")
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def is_coee_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "coee.ufop.br" or host.endswith(".coee.ufop.br")


def output_path(url: str, kind: str, content_type: str = "") -> str:
    """Return a deterministic, collision-resistant path within docs/."""
    parts = urlsplit(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    decoded_path = unquote(parts.path).strip("/")
    leaf = decoded_path.rsplit("/", 1)[-1] if decoded_path else "inicio"
    suffix = Path(leaf).suffix.lower()
    stem = Path(leaf).stem if suffix else leaf
    normalized = unicodedata.normalize("NFKD", stem)
    slug = "".join(
        char.lower() if char.isalnum() else "-"
        for char in normalized
        if not unicodedata.combining(char)
    )
    slug = re.sub(r"-+", "-", slug).strip("-") or "arquivo"
    if kind == "page":
        if url == START_URL:
            return "index.html"
        return f"paginas/{slug}-{digest}.html"
    if not suffix:
        if kind == "css":
            suffix = ".css"
        elif kind == "document" and content_type:
            suffix = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ".bin"
        else:
            suffix = ".bin"
    return f"recursos/{slug}-{digest}{suffix}"


def raw_path(url: str, kind: str) -> Path:
    suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
    if not suffix:
        suffix = ".html" if kind == "page" else ".css" if kind == "css" else ".bin"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return RAW_ROOT / f"{digest}{suffix}"


def classify_url(url: str, relation: str = "anchor") -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix == ".css" or relation == "css":
        return "css"
    if relation in {"asset", "script", "image"} or suffix in ASSET_EXTENSIONS:
        return "asset"
    if suffix in DOCUMENT_EXTENSIONS:
        return "document"
    return "page"


def wayback_url(url: str, timestamp: str = REFERENCE_TIMESTAMP) -> str:
    return f"https://web.archive.org/web/{timestamp}/{url}"


def effective_timestamp(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() == WAYBACK_HOST:
        match = re.match(r"^/web/(\d{1,14})", parts.path)
        if match:
            return match.group(1)
    return ""


def valid_payload(data: bytes, content_type: str, kind: str, url: str) -> tuple[bool, str]:
    if not data:
        return False, "resposta vazia"
    path_suffix = Path(urlsplit(url).path).suffix.lower()
    mime = content_type.split(";", 1)[0].strip().lower()
    if path_suffix == ".pdf" or mime == "application/pdf" or data.startswith(b"%PDF-"):
        if not data.startswith(b"%PDF-"):
            return False, "assinatura PDF ausente"
        return True, ""
    if path_suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}:
        signatures = {
            ".png": b"\x89PNG\r\n\x1a\n",
            ".jpg": b"\xff\xd8\xff",
            ".jpeg": b"\xff\xd8\xff",
            ".gif": (b"GIF87a", b"GIF89a"),
            ".webp": b"RIFF",
            ".ico": b"\x00\x00\x01\x00",
        }
        signature = signatures[path_suffix]
        if not (data.startswith(signature) if isinstance(signature, bytes) else any(data.startswith(item) for item in signature)):
            return False, "assinatura de imagem inválida"
    sample = data[:8192].lower()
    looks_html = bool(re.match(
        rb"\s*(?:<!doctype\s+html\b|<html\b|<!--.*?-->\s*<html\b)",
        sample,
        re.S,
    ))
    if b"this url has been excluded" in sample or b"wayback machine has not archived" in sample:
        return False, "mensagem do Wayback em vez do conteúdo arquivado"
    if looks_html and (
        b"<title>wayback machine</title>" in sample
        or b"window.webcomponentloaderconfig" in sample
        and b"wayback.replay.core.replaycontext" in sample
    ):
        return False, "página de erro do Wayback em vez do conteúdo arquivado"
    if kind == "page":
        if mime not in {"text/html", "application/xhtml+xml", "text/plain", ""} or not looks_html:
            return False, f"página não retornou HTML válido: {mime}"
    elif kind == "css":
        if looks_html or mime not in {"text/css", "text/plain", "application/octet-stream", ""}:
            return False, f"CSS retornou tipo de conteúdo inesperado: {mime}"
    elif looks_html or mime in {"text/html", "application/xhtml+xml"}:
        return False, f"recurso não retornou o arquivo esperado: {mime}"
    return True, ""


def fetch_archive(url: str, timestamp: str) -> tuple[bytes, str, str]:
    """Fetch exact-or-nearest archive content; return bytes, MIME and final URL."""
    raw_url = f"https://{WAYBACK_HOST}/web/{timestamp}id_/{url}"
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            request = Request(raw_url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
                return body, response.headers.get("Content-Type", ""), response.geturl()
        except HTTPError as exc:
            last_error = exc
            if exc.code in (404, 410):
                break
            if exc.code not in (408, 425, 429) and not 500 <= exc.code <= 599:
                break
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(2**attempt)
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(2**attempt)

    # A normal replay can work when the raw ("id_") endpoint is temporarily throttled.
    replay_url = wayback_url(url, timestamp)
    try:
        request = Request(replay_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
            return body, response.headers.get("Content-Type", ""), response.geturl()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        last_error = exc
        raise last_error


class LinkExtractor(HTMLParser):
    def __init__(self, page_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.base_url = page_url
        self.links: list[tuple[str, str]] = []
        self.in_style = False
        self.in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "base" and attrs_dict.get("href"):
            resolved = canonical_url(attrs_dict["href"] or "", self.page_url)
            if resolved:
                self.base_url = resolved
        rel = set((attrs_dict.get("rel") or "").lower().split())
        if tag == "a" and attrs_dict.get("href"):
            self._add(attrs_dict["href"], "anchor")
        if tag == "link" and attrs_dict.get("href"):
            self._add(attrs_dict["href"], "css" if "stylesheet" in rel else "asset")
        if tag in {"img", "script", "iframe", "source", "video", "audio", "embed", "input"}:
            source_attr = "poster" if tag == "video" else "src"
            if attrs_dict.get(source_attr):
                kind = "script" if tag == "script" else "image" if tag == "img" else "asset"
                self._add(attrs_dict[source_attr], kind)
        if attrs_dict.get("srcset"):
            for candidate in attrs_dict["srcset"].split(","):
                pieces = candidate.strip().split()
                if pieces:
                    self._add(pieces[0], "image")
        if attrs_dict.get("style"):
            self._extract_css(attrs_dict["style"])
        if tag == "style":
            self.in_style = True
        if tag == "script":
            self.in_script = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.in_style = False
        if tag == "script":
            self.in_script = False

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self._extract_css(data)
        if self.in_script:
            for match in SCRIPT_ROOT_URL_RE.finditer(data):
                self._add(match.group("url"), "script")

    def _add(self, value: str, relation: str) -> None:
        url = canonical_url(value, self.base_url)
        if url:
            self.links.append((url, relation))

    def _extract_css(self, css: str) -> None:
        for match in CSS_URL_RE.finditer(css):
            self._add(match.group(2), "asset")
        for match in CSS_IMPORT_RE.finditer(css):
            self._add(match.group(2), "css")


def extract_links(data: bytes, page_url: str) -> list[tuple[str, str]]:
    parser = LinkExtractor(page_url)
    parser.feed(clean_archive_markup(data.decode("utf-8", "replace")))
    return parser.links


def rewrite_css(css: str, source_url: str, source_path: str, local_map: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        original = match.group(2).strip()
        target = canonical_url(original, source_url)
        if not target:
            return match.group(0)
        return f"url({match.group(1)}{relative_target(source_path, target, local_map)}{match.group(1)})"

    rewritten = CSS_IMPORT_RE.sub(
        lambda match: f'@import "{relative_target(source_path, canonical_url(match.group(2), source_url) or "", local_map)}"',
        css,
    )
    return CSS_URL_RE.sub(replace, rewritten)


def relative_target(source_path: str, target_url: str, local_map: dict[str, str]) -> str:
    if urlsplit(target_url).scheme in {"mailto", "tel"}:
        return target_url
    target_without_fragment, fragment = urldefrag(target_url)
    target_path = local_map.get(target_without_fragment)
    fragment = f"#{fragment}" if fragment else ""
    if target_path:
        relative = posixpath.relpath(target_path, posixpath.dirname(source_path) or ".")
        return quote(relative, safe="/.-_~") + fragment
    if target_without_fragment:
        return wayback_url(target_without_fragment) + fragment
    return target_url


class LinkRewriter(HTMLParser):
    URL_ATTRS = {"href", "src", "poster", "action", "data-src"}

    def __init__(self, source_url: str, source_path: str, local_map: dict[str, str]):
        super().__init__(convert_charrefs=False)
        self.source_url = source_url
        self.source_path = source_path
        self.local_map = local_map
        self.output: list[str] = []
        self.pending_anchors: list[bool] = []
        self.in_style = False
        self.in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_out: list[tuple[str, str | None]] = []
        pending = False
        for key, value in attrs:
            if value is not None and key.lower() in self.URL_ATTRS:
                target = canonical_url(value, self.source_url)
                if target:
                    fragment = urlsplit(value).fragment
                    target_with_fragment = f"{target}#{fragment}" if fragment else target
                    replacement = relative_target(self.source_path, target_with_fragment, self.local_map)
                    pending = (
                        key.lower() == "href"
                        and urlsplit(target).scheme in {"http", "https"}
                        and target not in self.local_map
                    )
                    attrs_out.append((key, replacement))
                    continue
            if value is not None and key.lower() == "srcset":
                attrs_out.append((key, rewrite_srcset(value, self.source_url, self.source_path, self.local_map)))
                continue
            if value is not None and key.lower() == "style":
                attrs_out.append((key, rewrite_css(value, self.source_url, self.source_path, self.local_map)))
                continue
            attrs_out.append((key, value))
        if tag == "a" and pending:
            attrs_out.append(("title", "Captura local pendente; URL do Wayback precisa ser verificada"))
            attrs_out.append(("data-archive-pending", "true"))
        if tag == "style":
            self.in_style = True
        if tag == "script":
            self.in_script = True
        self.output.append(self.get_starttag_text() if not attrs_out else format_starttag(tag, attrs_out))
        if tag == "a":
            self.pending_anchors.append(pending)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.output[-1] = self.output[-1][:-1] + " />"
        if tag == "a" and self.pending_anchors:
            self.pending_anchors.pop()

    def handle_endtag(self, tag: str) -> None:
        self.output.append(f"</{tag}>")
        if tag == "style":
            self.in_style = False
        if tag == "script":
            self.in_script = False
        if tag == "a" and self.pending_anchors:
            if self.pending_anchors.pop():
                self.output.append(' <small class="archive-pending-label">[Wayback: verificar captura]</small>')

    def handle_data(self, data: str) -> None:
        if self.in_style:
            data = rewrite_css(data, self.source_url, self.source_path, self.local_map)
        if self.in_script:
            def replace_script_path(match: re.Match[str]) -> str:
                target = canonical_url(match.group("url"), self.source_url)
                if not target:
                    return match.group(0)
                replacement = relative_target(self.source_path, target, self.local_map)
                quote_char = match.group("quote")
                return f"{quote_char}{replacement}{quote_char}"

            data = SCRIPT_ROOT_URL_RE.sub(replace_script_path, data)
        self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.output.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.output.append(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        self.output.append(f"<![{data}]>")


def format_starttag(tag: str, attrs: list[tuple[str, str | None]]) -> str:
    rendered = []
    for key, value in attrs:
        if value is None:
            rendered.append(key)
        else:
            rendered.append(f'{key}="{html.escape(value, quote=True)}"')
    suffix = f" {' '.join(rendered)}" if rendered else ""
    return f"<{tag}{suffix}>"


def rewrite_srcset(value: str, source_url: str, source_path: str, local_map: dict[str, str]) -> str:
    rewritten = []
    for candidate in value.split(","):
        pieces = candidate.strip().split()
        if pieces:
            target = canonical_url(pieces[0], source_url)
            if target:
                pieces[0] = relative_target(source_path, target, local_map)
            rewritten.append(" ".join(pieces))
    return ", ".join(rewritten)


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"reference_timestamp": REFERENCE_TIMESTAMP, "entries": []}
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Não foi possível ler {MANIFEST_PATH}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("entries"), list):
        raise RuntimeError(f"Manifesto inválido: {MANIFEST_PATH}")
    manifest["entries"] = [
        entry for entry in manifest["entries"]
        if not is_archive_ui_entry(entry)
    ]
    return manifest


def is_archive_ui_entry(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    parts = urlsplit(entry.get("original_url", ""))
    host = (parts.hostname or "").lower()
    if entry.get("kind") == "external" and host in {
        "web.archive.org", "web-static.archive.org"
    }:
        return True
    if entry.get("kind") == "external" and host == "archive.org" and parts.path.startswith("/includes/donate.php"):
        return True
    return host == "coee.ufop.br" and re.match(r"^/web(?:/(\d{1,14}|\*).*)?/?$", parts.path) is not None


def save_manifest(manifest: dict) -> None:
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = MANIFEST_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(MANIFEST_PATH)


def load_inventory() -> list[WorkItem]:
    paths = [ROOT / "COEE-inventario-parcial.json", ARCHIVE_ROOT / "COEE-inventario-parcial.json"]
    path = next((candidate for candidate in paths if candidate.exists()), None)
    if path is None:
        return []
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Aviso: inventário parcial ignorado: {exc}", file=sys.stderr)
        return []
    candidates = source if isinstance(source, list) else source.get("items", source.get("entries", []))
    items: list[WorkItem] = []
    if not isinstance(candidates, list):
        return items
    for candidate in candidates:
        if isinstance(candidate, str):
            value, timestamp = candidate, REFERENCE_TIMESTAMP
        elif isinstance(candidate, dict):
            value = candidate.get("file_url") or candidate.get("original") or candidate.get("url") or candidate.get("original_url")
            timestamp = str(candidate.get("timestamp") or candidate.get("capture") or REFERENCE_TIMESTAMP)
        else:
            continue
        url = canonical_url(str(value or ""))
        if url and is_coee_url(url):
            items.append(WorkItem(url, "inventário parcial", classify_url(url), timestamp))
    return items


def parse_mime(data: bytes, content_type: str) -> str:
    if content_type:
        return content_type
    if data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return "text/html"
    return ""


def entry_by_url(manifest: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for entry in manifest["entries"]:
        if isinstance(entry, dict) and entry.get("original_url"):
            result[entry["original_url"]] = entry
    return result


def add_or_update_entry(manifest: dict, item: WorkItem) -> dict:
    entries = entry_by_url(manifest)
    entry = entries.get(item.url)
    if entry is None:
        entry = {
            "original_url": item.url,
            "found_on": item.found_on,
            "requested_capture": item.requested_capture,
            "effective_capture": "",
            "local_file": "",
            "raw_file": "",
            "status": "pendente",
            "error": "",
            "kind": item.kind,
        }
        manifest["entries"].append(entry)
    elif (not entry.get("found_on") or entry.get("status") == "origem_invalida") and item.found_on:
        entry["found_on"] = item.found_on
    content_type = entry.get("content_type", "").split(";", 1)[0].strip().lower()
    if (
        is_coee_url(item.url)
        and not Path(urlsplit(item.url).path).suffix
        and content_type in {"text/html", "application/xhtml+xml"}
    ):
        item.kind = "page"
    entry["kind"] = item.kind
    return entry


def is_ignored_url(url: str) -> bool:
    parts = urlsplit(url)
    if ADMIN_PATH_RE.search(parts.path):
        return True
    if parts.path.lower().endswith((".php", ".asp", ".aspx")):
        return True
    return False


def document_relevant(url: str) -> bool:
    parts = urlsplit(url)
    suffix = Path(parts.path).suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        return True
    path = parts.path.lower()
    return any(
        token in path
        for token in (
            "/sites/default/files/", "resolu", "estag", "tcc", "formul",
            "calend", "ata", "manual", "document", "arquivo", "download",
            "portaria", "norma",
        )
    )


def external_relevant(url: str, relation: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if relation in {"css", "script", "asset", "image"} or document_relevant(url):
        return True
    return host in {
        "drive.google.com", "docs.google.com", "dropbox.com",
        "www.dropbox.com", "onedrive.live.com", "1drv.ms",
        "box.com", "www.box.com",
    }


def link_priority(link: tuple[str, str]) -> int:
    url, relation = link
    if relation != "anchor":
        return 2
    path = unquote(urlsplit(url).path).lower()
    if any(term in path for term in ("conclusão", "colegiado", "estágio", "ementas", "resolu", "formul")):
        return 0
    return 1


def collect_and_fetch(manifest: dict, offline: bool, max_downloads: int = DEFAULT_MAX_DOWNLOADS) -> None:
    queue: deque[WorkItem] = deque(
        [WorkItem(START_URL, "", "page", REFERENCE_TIMESTAMP)]
    )
    for entry in manifest["entries"]:
        if entry.get("status") in {"pendente", "cache_ausente_offline"} and entry.get("kind") != "external":
            queue.append(WorkItem(
                entry["original_url"], entry.get("found_on", ""),
                entry.get("kind", "page"), entry.get("requested_capture") or REFERENCE_TIMESTAMP,
            ))
    queue.extend(load_inventory())
    queued: set[str] = set()
    downloads = 0
    while queue:
        item = queue.popleft()
        if item.url in queued or is_ignored_url(item.url):
            continue
        queued.add(item.url)
        entry = add_or_update_entry(manifest, item)
        output = output_path(item.url, item.kind, entry.get("content_type", ""))
        previous_raw = ROOT / entry["raw_file"] if entry.get("raw_file") else None
        raw = previous_raw if previous_raw and previous_raw.exists() else raw_path(item.url, item.kind)
        entry["local_file"] = f"docs/{output}"
        entry["raw_file"] = raw.relative_to(ROOT).as_posix()
        raw_valid = False
        raw_error = ""
        if raw.exists():
            try:
                payload = raw.read_bytes()
                raw_valid, raw_error = valid_payload(payload, entry.get("content_type", "") or mimetypes.guess_type(raw.name)[0] or "", item.kind, item.url)
            except OSError as exc:
                raw_error = f"não foi possível ler arquivo bruto: {exc}"
                raw_valid = False
        if raw_valid:
            entry["status"] = "recuperado"
            entry["error"] = ""
            payload = raw.read_bytes()
            content_type = entry.get("content_type", "") or mimetypes.guess_type(raw.name)[0] or ""
        elif offline:
            if raw_error:
                entry["status"] = "erro_validacao"
                entry["error"] = raw_error
            elif entry.get("status") not in {
                "falha_temporaria", "captura_indisponivel", "erro_validacao"
            }:
                entry["status"] = "cache_ausente_offline"
                entry["error"] = "nenhuma resposta bruta válida disponível no cache local"
            save_manifest(manifest)
            continue
        elif downloads >= max_downloads:
            if entry.get("status") not in {"falha_temporaria", "captura_indisponivel", "erro_validacao"}:
                entry["status"] = "pendente"
                entry["error"] = "limite de downloads desta execução atingido"
            save_manifest(manifest)
            continue
        else:
            downloads += 1
            print(f"[{downloads}/{max_downloads}] {item.url}", flush=True)
            try:
                time.sleep(REQUEST_DELAY_SECONDS)
                payload, content_type, final_url = fetch_archive(item.url, item.requested_capture)
                content_type = parse_mime(payload, content_type)
                valid, reason = valid_payload(payload, content_type, item.kind, item.url)
                if not valid:
                    entry["status"] = "erro_validacao"
                    entry["error"] = reason
                    entry["effective_capture"] = effective_timestamp(final_url)
                    save_manifest(manifest)
                    continue
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(payload)
                entry["status"] = "recuperado"
                entry["error"] = ""
                entry["effective_capture"] = effective_timestamp(final_url)
                entry["content_type"] = content_type
                output = output_path(item.url, item.kind, content_type)
                entry["local_file"] = f"docs/{output}"
            except HTTPError as exc:
                entry["status"] = "captura_indisponivel" if exc.code in (404, 410) else "falha_temporaria"
                entry["error"] = f"HTTP {exc.code}: {exc.reason}"
                save_manifest(manifest)
                continue
            except (URLError, TimeoutError, OSError) as exc:
                entry["status"] = "falha_temporaria"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                save_manifest(manifest)
                continue

        if item.kind == "page":
            for linked_url, relation in sorted(extract_links(payload, item.url), key=link_priority):
                if is_coee_url(linked_url):
                    if is_ignored_url(linked_url):
                        continue
                    child_kind = classify_url(linked_url, relation)
                    queue.append(WorkItem(linked_url, item.url, child_kind, item.requested_capture))
                elif (urlsplit(linked_url).hostname or "").lower().endswith(".ufop.br") or (
                    urlsplit(linked_url).hostname or ""
                ).lower() == "ufop.br":
                    if document_relevant(linked_url):
                        external_kind = classify_url(linked_url, relation)
                        if external_kind == "page":
                            external_kind = "document"
                        queue.append(WorkItem(linked_url, item.url, external_kind, item.requested_capture))
                    else:
                        if external_relevant(linked_url, relation):
                            external_entry = add_or_update_entry(
                                manifest,
                                WorkItem(linked_url, item.url, "external", item.requested_capture),
                            )
                            external_entry["status"] = "conferir_externamente"
                            external_entry["error"] = "link externo relevante; captura não solicitada"
                elif urlsplit(linked_url).scheme in {"http", "https"}:
                    if external_relevant(linked_url, relation):
                        external_entry = add_or_update_entry(
                            manifest,
                            WorkItem(linked_url, item.url, "external", item.requested_capture),
                        )
                        external_entry["status"] = "conferir_externamente"
                        external_entry["error"] = "domínio externo; verificar relevância e captura manualmente"
        elif item.kind == "css":
            css_links = []
            text = payload.decode("utf-8", "replace")
            for match in CSS_URL_RE.finditer(text):
                target = canonical_url(match.group(2), item.url)
                if target:
                    css_links.append(target)
            for match in CSS_IMPORT_RE.finditer(text):
                target = canonical_url(match.group(2), item.url)
                if target:
                    css_links.append(target)
            queue.extend(
                WorkItem(target, item.url, classify_url(target, "css" if target.lower().endswith(".css") else "asset"), item.requested_capture)
                for target in css_links
                if is_coee_url(target)
            )
        save_manifest(manifest)
    invalid_sources = {
        entry["original_url"] for entry in manifest["entries"]
        if entry.get("kind") == "page"
        and entry.get("status") == "erro_validacao"
        and "página de erro do Wayback" in entry.get("error", "")
    }
    for entry in manifest["entries"]:
        if entry.get("kind") == "external" and entry.get("found_on") in invalid_sources:
            entry["status"] = "origem_invalida"
            entry["error"] = "link extraído de uma página de erro do Wayback; origem precisa ser verificada"
    manifest.pop("crawl_limit", None)
    save_manifest(manifest)


def clean_archive_markup(text: str) -> str:
    text = WAYBACK_TOOLBAR_RE.sub("", text)
    text = re.sub(
        r"<script\b[^>]*>.*?</script\s*>",
        lambda match: ""
        if re.search(
            r"web-static\.archive\.org|web\.archive\.org/_static|archive_analytics|__wm\.",
            match.group(0),
            re.I,
        )
        else match.group(0),
        text,
        flags=re.I | re.S,
    )
    text = re.sub(
        r'<link\b(?=[^>]*href=["\']https?://web-static\.archive\.org/)[^>]*>',
        "",
        text,
        flags=re.I,
    )
    return text


def build_site(manifest: dict) -> None:
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    (DOCS_ROOT / ".nojekyll").write_text("", encoding="utf-8")
    entries = []
    for entry in manifest["entries"]:
        if entry.get("status") != "recuperado" or not entry.get("raw_file"):
            continue
        raw = ROOT / Path(entry["raw_file"])
        try:
            payload = raw.read_bytes()
        except OSError as exc:
            entry["status"] = "falha_arquivo_local"
            entry["error"] = f"não foi possível ler arquivo bruto: {exc}"
            continue
        valid, reason = valid_payload(payload, entry.get("content_type", ""), entry.get("kind", "asset"), entry["original_url"])
        if not valid:
            entry["status"] = "erro_validacao"
            entry["error"] = reason
            continue
        entries.append(entry)
    local_map = {
        entry["original_url"]: entry["local_file"].removeprefix("docs/").replace("\\", "/")
        for entry in entries if entry.get("local_file")
    }
    for entry in entries:
        raw = ROOT / Path(entry["raw_file"])
        output_relative = entry["local_file"].removeprefix("docs/")
        destination = DOCS_ROOT / Path(output_relative)
        try:
            payload = raw.read_bytes()
        except OSError as exc:
            entry["status"] = "falha_arquivo_local"
            entry["error"] = f"não foi possível ler arquivo bruto: {exc}"
            continue
        url = entry["original_url"]
        kind = entry.get("kind", "asset")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if kind == "page":
            text = payload.decode("utf-8", "replace")
            text = clean_archive_markup(text)
            parser = LinkRewriter(url, output_relative, local_map)
            parser.feed(text)
            rendered = "".join(parser.output)
            banner = (
                '<aside class="archive-notice">Cópia histórica do site do Colegiado de '
                'Engenharia Elétrica da UFOP (COEE), baseada em capturas do Internet Archive. '
                '<a href="https://web.archive.org/web/20240727095454/https://coee.ufop.br/">'
                "Consultar captura de referência</a>.</aside>"
            )
            rendered = re.sub(r"(<body\b[^>]*>)", r"\1" + banner, rendered, count=1, flags=re.I)
            style = (
                "<style>.archive-notice{margin:0;padding:10px 16px;background:#fff3cd;"
                "color:#333;font:14px/1.5 sans-serif}.archive-notice a{font-weight:bold}"
                ".archive-pending-label{color:#8a4b08;font-size:.85em}</style>"
            )
            rendered = re.sub(r"</head\s*>", style + "</head>", rendered, count=1, flags=re.I)
            destination.write_text(rendered, encoding="utf-8")
        elif kind == "css":
            text = payload.decode("utf-8", "replace")
            destination.write_text(rewrite_css(text, url, output_relative, local_map), encoding="utf-8")
        else:
            destination.write_bytes(payload)


class SubpathPreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS_ROOT), **kwargs)

    def translate_path(self, path: str) -> str:
        request_path = urlsplit(path).path
        prefix = "/siteCoee"
        if request_path == prefix:
            request_path = prefix + "/"
        if not request_path.startswith(prefix + "/"):
            return str(DOCS_ROOT / "__not_found__")
        self.path = request_path[len(prefix):] + (("?" + urlsplit(path).query) if urlsplit(path).query else "")
        return super().translate_path(self.path)


def serve_preview(port: int) -> None:
    if not (DOCS_ROOT / "index.html").exists():
        raise RuntimeError("docs/index.html não existe; gere a cópia antes da prévia")
    server = ThreadingHTTPServer(("127.0.0.1", port), SubpathPreviewHandler)
    print(f"Prévia: http://localhost:{port}/siteCoee/")
    print("Pressione Ctrl+C para encerrar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPrévia encerrada.")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="não acessar a rede; gerar docs/ somente com respostas brutas já salvas",
    )
    parser.add_argument(
        "--serve-preview",
        action="store_true",
        help="iniciar servidor local em /siteCoee/ após gerar docs/",
    )
    parser.add_argument("--port", type=int, default=8000, help="porta da prévia (padrão: 8000)")
    parser.add_argument("--max-downloads", type=int, default=DEFAULT_MAX_DOWNLOADS, help="máximo de downloads por execução (padrão: 20)")
    args = parser.parse_args(argv)
    if args.max_downloads < 1:
        parser.error("--max-downloads deve ser maior que zero")
    try:
        manifest = load_manifest()
        manifest.setdefault("reference_timestamp", REFERENCE_TIMESTAMP)
        manifest.setdefault("entries", [])
        try:
            collect_and_fetch(manifest, offline=args.offline, max_downloads=args.max_downloads)
        except KeyboardInterrupt:
            print("\nRecuperação interrompida; gerando a cópia com o cache disponível.", file=sys.stderr)
            build_site(manifest)
            save_manifest(manifest)
            return 130
        build_site(manifest)
        save_manifest(manifest)
    except (OSError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    statuses: dict[str, int] = {}
    for entry in manifest["entries"]:
        status = entry.get("status", "desconhecido")
        statuses[status] = statuses.get(status, 0) + 1
    print(f"Site gerado em: {DOCS_ROOT}")
    print(f"Manifesto: {MANIFEST_PATH}")
    print("Situação: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    if args.serve_preview:
        serve_preview(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
