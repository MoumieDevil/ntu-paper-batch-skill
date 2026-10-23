#!/usr/bin/env python3
"""Conservative, non-atomic imports into a selected personal Zotero collection.

Python 3.9+. Reads/writes only Zotero's loopback connector, never publisher sites.
PDF saves require pypdf and prior human/agent confirmation of document identity.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE_URL = "http://127.0.0.1:23119"
API_ROOT = "/api/users/0"
API_HEADERS = {"Zotero-API-Version": "3"}
CONNECTOR_HEADERS = {
    "Content-Type": "application/json",
    "X-Zotero-Connector-API-Version": "3",
}
MAX_PDF_BYTES = 150 * 1024 * 1024
PAGE_SIZE = 100
MAX_PAGES = 100
SUCCESS = {"dry_run", "saved_complete", "saved_metadata", "duplicate_complete"}


class ZoteroError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ZoteroError("Unexpected redirect from local Zotero; no redirect followed")


def request(path, *, method="GET", body=None, headers=None, timeout=15):
    if not path.startswith(("/api/", "/connector/")):
        raise ZoteroError("Only local Zotero API/connector paths are allowed")
    req = urllib.request.Request(BASE_URL + path, data=body, method=method,
                                 headers=headers or {})
    # Ignore proxy environment variables: local metadata/PDF bytes stay local.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        # Bodies can contain URLs/tokens; do not echo them to logs.
        raise ZoteroError("Local Zotero returned HTTP %s" % exc.code) from exc
    except OSError as exc:
        raise ZoteroError("Local Zotero request failed or timed out") from exc


def parse_json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ZoteroError("Invalid JSON returned by local Zotero") from exc


def get_json(path):
    status, body = request(path, headers=API_HEADERS)
    if status != 200:
        raise ZoteroError("Unexpected local API status: %s" % status)
    return parse_json(body)


def selected_target():
    status, body = request("/connector/getSelectedCollection", method="POST",
                           body=b"{}", headers=CONNECTOR_HEADERS)
    if status != 200:
        raise ZoteroError("Unexpected selected-target status: %s" % status)
    target = parse_json(body)
    if not isinstance(target, dict):
        raise ZoteroError("Invalid selected target")
    return target


def paginated(path, params=None):
    rows, seen = [], set()
    for page in range(MAX_PAGES):
        query = dict(params or {}, limit=PAGE_SIZE, start=page * PAGE_SIZE)
        batch = get_json(path + "?" + urllib.parse.urlencode(query))
        if not isinstance(batch, list):
            raise ZoteroError("Expected an API list, not a partial/error response")
        for row in batch:
            if not isinstance(row, dict) or not row.get("key"):
                raise ZoteroError("Invalid item/collection in API list")
            if row["key"] in seen:
                raise ZoteroError("Pagination repeated a key; retry the read after Zotero settles")
            seen.add(row["key"])
            rows.append(row)
        if len(batch) < PAGE_SIZE:
            return rows
    raise ZoteroError("Read pagination limit exceeded; duplicate check incomplete")


def normalize_title(value):
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = "".join(c for c in value if unicodedata.category(c) != "Cf")
    return re.sub(r"\s+", " ", value).strip()


def normalize_doi(value):
    value = (value or "").strip().casefold()
    value = re.sub(r"^doi\s*:\s*", "", value)
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return urllib.parse.unquote(value).strip()


def query_items(term):
    return paginated(API_ROOT + "/items/top", {"q": term, "qmode": "everything"})


def find_matches(doi="", title=""):
    expected_doi, expected_title = normalize_doi(doi), normalize_title(title)
    if not expected_doi and not expected_title:
        raise ZoteroError("Provide a DOI or title for duplicate checking")
    candidates = {}
    for term in dict.fromkeys(t for t in (expected_doi, expected_title) if t):
        for row in query_items(term):
            candidates[row["key"]] = row
    def classify(rows):
        matches, conflicts = [], []
        for row in rows:
            data = row.get("data", {})
            if data.get("deleted"):
                continue
            doi_match = expected_doi and normalize_doi(data.get("DOI", "")) == expected_doi
            title_match = expected_title and normalize_title(data.get("title", "")) == expected_title
            other_doi = normalize_doi(data.get("DOI", ""))
            if title_match and expected_doi and other_doi and expected_doi != other_doi:
                conflicts.append(row)
            elif doi_match or title_match:
                matches.append(row)
        return matches, conflicts

    matches, conflicts = classify(candidates.values())
    if not matches and not conflicts:
        # Search indexing can miss invisible characters or DOI URL prefixes.
        # A bounded read-only inventory fallback avoids blind duplicate creation.
        matches, conflicts = classify(paginated(API_ROOT + "/items/top"))
    if conflicts:
        raise ZoteroError("Same-title record has a conflicting DOI; resolve identity manually")
    return matches


def checked_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ZoteroError(label + " must be non-empty text")
    if "\ufffd" in value or "???" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ZoteroError(label + " has replacement/garbled characters; supply UTF-8 metadata")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value):
        raise ZoteroError(label + " contains control characters")
    return value.strip()


def identity_review_reason(expected, actual):
    """Title-only candidates need author/year context before repair or completion."""
    expected_doi = normalize_doi(expected.get("DOI", ""))
    actual_doi = normalize_doi(actual.get("DOI", ""))
    if expected_doi and actual_doi:
        return None if expected_doi == actual_doi else "Conflicting DOI"
    if normalize_title(expected.get("title", "")) != normalize_title(actual.get("title", "")):
        return "Title identity differs"

    def first_author_names(data):
        authors = [c for c in data.get("creators", []) if c.get("creatorType") == "author"]
        if not authors:
            return set()
        creator = authors[0]
        first, last = creator.get("firstName", ""), creator.get("lastName", "")
        return {normalize_title(n).replace(" ", "") for n in
                (creator.get("name", ""), first + last, last + first) if n}

    expected_year = re.search(r"\b(?:19|20)\d{2}\b", expected.get("date", ""))
    actual_year = re.search(r"\b(?:19|20)\d{2}\b", actual.get("date", ""))
    if not expected_year or not actual_year or expected_year.group() != actual_year.group():
        return "Title-only match has missing or different publication year"
    if not first_author_names(expected).intersection(first_author_names(actual)):
        return "Title-only match has missing or different first author"
    return None


def stable_url(value):
    if not value:
        return ""
    checked_text(value, "URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ZoteroError("Use a public HTTP(S) record URL without credentials")
    sensitive = re.compile(r"token|signature|password|session|authorization|invoice|nonce|ticket|credential|saml|relaystate|hmac|^(?:access_key|api_key|auth|key|sig|v)$", re.I)
    if any(sensitive.search(k) for k, _ in urllib.parse.parse_qsl(parsed.query)):
        raise ZoteroError("Do not store signed/authenticated download URLs; use the canonical record URL")
    if any(sensitive.search(k) for k, _ in urllib.parse.parse_qsl(parsed.fragment)):
        raise ZoteroError("Do not store authentication data in URL fragments")
    return value


def load_metadata(path=None, inline=None, from_stdin=False):
    try:
        if inline is not None:
            item = json.loads(inline)
        elif from_stdin:
            raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
            item = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
        else:
            item = json.loads(Path(path).expanduser().read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ZoteroError("Metadata must be one readable UTF-8 JSON object") from exc
    if not isinstance(item, dict):
        raise ZoteroError("Metadata must be one JSON object")
    item = dict(item)
    item["title"] = checked_text(item.get("title"), "title")
    allowed_types = {"journalArticle", "conferencePaper", "thesis", "preprint", "report", "book", "bookSection"}
    if item.get("itemType") not in allowed_types:
        raise ZoteroError("Unsupported scholarly itemType")
    if any(item.get(k) for k in ("attachments", "notes", "collections", "relations")):
        raise ZoteroError("Embedded attachments/notes/collections/relations are not accepted")
    for key in ("attachments", "notes", "collections", "relations", "id", "key", "version"):
        item.pop(key, None)
    creators = item.get("creators")
    if not isinstance(creators, list) or not creators:
        raise ZoteroError("Provide verified creators; do not create incomplete metadata silently")
    for creator in creators:
        if not isinstance(creator, dict) or not creator.get("creatorType"):
            raise ZoteroError("Invalid creator")
        checked_text(creator.get("lastName") or creator.get("name"), "creator name")
        if creator.get("firstName"):
            checked_text(creator["firstName"], "creator firstName")
    checked_text(item.get("date"), "date")
    if item.get("DOI"):
        checked_text(item["DOI"], "DOI")
        item["DOI"] = normalize_doi(item["DOI"])
        if not re.fullmatch(r"10\.\d{4,9}/\S+", item["DOI"]):
            raise ZoteroError("Invalid DOI syntax")
    if item.get("url"):
        item["url"] = stable_url(item["url"])
    for key, value in item.items():
        if isinstance(value, str) and value:
            checked_text(value, key)
    return item


def validate_pdf(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_PDF_BYTES:
        raise ZoteroError("PDF missing, empty, or exceeds the 150 MiB safety limit")
    data = path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise ZoteroError("Downloaded file is not a PDF (possibly HTML/login response)")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ZoteroError("PDF validation requires pypdf; install the packaged requirements first") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) < 1:
            raise ValueError("encrypted or empty")
        # Resolve page objects too; a header and trailer alone are not enough.
        for page in reader.pages:
            _ = page.mediabox
        pages = len(reader.pages)
    except Exception as exc:
        raise ZoteroError("PDF parser rejected the file or it is encrypted") from exc
    return {"bytes": data, "sha256": hashlib.sha256(data).hexdigest(), "pages": pages, "size": len(data)}


def target_path(target):
    targets = target.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ZoteroError("Connector omitted target hierarchy; cannot verify personal library")
    roots = [r for r in targets if r.get("level") == 0]
    if not roots or roots[0].get("id") != "L%s" % target.get("libraryID"):
        raise ZoteroError("Only the personal library is supported; group/unknown target rejected")
    # Zotero Libraries.getAll() puts the personal library first. See API notes.
    wanted, stack = "C%s" % target.get("id"), []
    for row in targets:
        level = row.get("level")
        if not isinstance(level, int) or level < 0 or level > len(stack):
            raise ZoteroError("Invalid Connector target hierarchy")
        stack = stack[:level] + [row.get("name")]
        if row.get("id") == wanted:
            if not stack or stack[0] != roots[0].get("name"):
                raise ZoteroError("Selected collection is outside the personal library")
            return stack[1:]
    raise ZoteroError("Select an ordinary collection, not a library root or saved search")


def collection_path(row, by_key):
    names, visited = [], set()
    while row:
        key = row.get("key")
        if key in visited:
            raise ZoteroError("Collection hierarchy cycle")
        visited.add(key)
        names.insert(0, row.get("data", {}).get("name"))
        parent = row.get("data", {}).get("parentCollection")
        if parent and parent not in by_key:
            raise ZoteroError("Incomplete collection hierarchy")
        row = by_key.get(parent)
    return names


def resolve_target(args, has_pdf):
    target = selected_target()
    if target.get("name") != args.expect_collection or not target.get("id"):
        raise ZoteroError("Selected Zotero collection does not match --expect-collection")
    if target.get("editable") is not True or target.get("libraryEditable") is not True:
        raise ZoteroError("Selected Zotero target is not editable")
    if has_pdf and target.get("filesEditable") is not True:
        raise ZoteroError("Selected Zotero target does not permit stored attachments")
    if args.expect_library_id is not None and target.get("libraryID") != args.expect_library_id:
        raise ZoteroError("Selected library ID does not match --expect-library-id")
    path = target_path(target)
    collections = paginated(API_ROOT + "/collections")
    by_key = {r["key"]: r for r in collections}
    matches = [r for r in collections if collection_path(r, by_key) == path]
    if len(matches) != 1:
        raise ZoteroError("Selected collection path is missing or ambiguous in personal library")
    key = matches[0]["key"]
    if args.expect_collection_key and key != args.expect_collection_key:
        raise ZoteroError("Collection key does not match --expect-collection-key")
    return target, key


def assert_same_target(expected, has_pdf):
    current = selected_target()
    fields = ("libraryID", "id", "name", "editable", "libraryEditable")
    if any(current.get(k) != expected.get(k) for k in fields):
        raise ZoteroError("Zotero target changed; do not switch collections during an import")
    if has_pdf and current.get("filesEditable") is not True:
        raise ZoteroError("Stored-file permission changed during import")


def get_children(key):
    return paginated(API_ROOT + "/items/%s/children" % urllib.parse.quote(key, safe=""))


def stored_pdfs(children):
    return [r for r in children if r.get("data", {}).get("itemType") == "attachment"
            and r["data"].get("linkMode") in ("imported_file", "imported_url")
            and r["data"].get("contentType") == "application/pdf"]


def attachment_bytes(key):
    # This endpoint returns text/plain (not a JSON string).
    status, body = request(API_ROOT + "/items/%s/file/view/url" % urllib.parse.quote(key, safe=""),
                           headers=API_HEADERS)
    if status != 200:
        raise ZoteroError("Stored attachment URL is unavailable")
    value = body.decode("utf-8").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ZoteroError("Attachment is not a local stored file; no remote URL followed")
    decoded = urllib.request.url2pathname(parsed.path)
    if decoded.startswith(("\\\\", "//")):
        raise ZoteroError("Network attachment paths are not followed")
    path = Path(decoded)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_PDF_BYTES:
        raise ZoteroError("Stored attachment is missing, empty, or too large")
    return path.read_bytes()


def verify_parent(row, collection_key, expected_pdf=None):
    result = {"item_key": row["key"], "collection_key": collection_key,
              "in_collection": collection_key in row.get("data", {}).get("collections", [])}
    pdfs = [r for r in stored_pdfs(get_children(row["key"]))
            if r.get("data", {}).get("parentItem") == row["key"]]
    result["pdf_attachment_keys"] = [r["key"] for r in pdfs]
    result["pdf_verified"] = False
    if expected_pdf:
        for attachment in pdfs:
            try:
                raw = attachment_bytes(attachment["key"])
                if hashlib.sha256(raw).hexdigest() == expected_pdf["sha256"]:
                    result.update(pdf_verified=True, attachment_key=attachment["key"],
                                  pdf_size=len(raw), pdf_pages=expected_pdf["pages"])
                    break
            except (ZoteroError, OSError):
                continue
    return result


def observe_created(doi, title, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        matches = find_matches(doi, title)
        if len(matches) > 1:
            raise ZoteroError("Multiple matching parents observed after write; do not retry automatically")
        if matches:
            return matches[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.4)


def save(args):
    if args.allow_duplicate:
        raise ZoteroError("--allow-duplicate is disabled; resolve duplicate identity manually")
    if bool(args.pdf) == bool(args.metadata_only):
        raise ZoteroError("Provide --pdf or explicitly accept --metadata-only (not both)")
    if args.pdf and not args.pdf_identity_confirmed:
        raise ZoteroError("Check PDF first-page title/DOI, then pass --pdf-identity-confirmed")
    item = load_metadata(args.metadata, args.metadata_json, args.metadata_stdin)
    source_url = stable_url(args.source_url or item.get("url", ""))
    if not source_url:
        raise ZoteroError("Provide a verified canonical source URL")
    pdf_url = stable_url(args.pdf_url or source_url)
    attachment_title = checked_text(args.attachment_title, "attachment title")
    pdf = validate_pdf(args.pdf) if args.pdf else None
    target, collection_key = resolve_target(args, bool(pdf))
    matches = find_matches(item.get("DOI", ""), item["title"])
    if len(matches) > 1:
        raise ZoteroError("Ambiguous duplicate: multiple matching records; no write performed")
    if matches:
        reason = identity_review_reason(item, matches[0].get("data", {}))
        if reason:
            return {"status": "needs_identity_review", "candidate_item_key": matches[0]["key"],
                    "reason": reason, "writes_performed": False,
                    "action": "Review source, author, year and DOI; do not attach to or duplicate this candidate automatically"}
        report = verify_parent(matches[0], collection_key, pdf)
        if not report["in_collection"]:
            status = "needs_collection"
        elif pdf and not report["pdf_attachment_keys"]:
            status = "needs_attachment"
        elif pdf and report["pdf_verified"]:
            status = "duplicate_complete"
        else:
            status = "duplicate_present"
        return dict(report, status=status, writes_performed=False,
                    action="Reuse the exact existing parent; manually audit/attach/move if needed")
    if args.dry_run:
        return {"status": "dry_run", "collection_key": collection_key,
                "title": item["title"], "pdf_validated": bool(pdf),
                "pdf_pages": pdf["pages"] if pdf else None, "writes_performed": False}

    session_id, connector_id = "ntu-" + uuid.uuid4().hex, "item-" + uuid.uuid4().hex
    item = dict(item, id=connector_id)
    # An empty attachments array prevents implicit URL attachment requests.
    item["attachments"] = []
    payload = json.dumps({"sessionID": session_id, "uri": source_url, "items": [item]},
                         ensure_ascii=False).encode("utf-8")
    assert_same_target(target, bool(pdf))
    stage, parent = "saveItems", None
    try:
        status, _ = request("/connector/saveItems", method="POST", body=payload,
                            headers=CONNECTOR_HEADERS, timeout=60)
        if status != 201:
            raise ZoteroError("saveItems was not confirmed with HTTP 201")
        stage = "observe_parent"
        parent = observe_created(item.get("DOI", ""), item["title"])
        if not parent:
            raise ZoteroError("Accepted parent is not observable yet")
        identity_reason = identity_review_reason(item, parent.get("data", {}))
        if identity_reason:
            raise ZoteroError("Observed parent needs identity review: " + identity_reason)
        if collection_key not in parent.get("data", {}).get("collections", []):
            raise ZoteroError("Parent was saved outside the expected collection")
        assert_same_target(target, bool(pdf))
        if pdf:
            stage = "saveAttachment"
            metadata = json.dumps({"sessionID": session_id, "parentItemID": connector_id,
                                   "title": attachment_title, "url": pdf_url},
                                  ensure_ascii=True, separators=(",", ":"))
            headers = {"Content-Type": "application/pdf", "Content-Length": str(pdf["size"]),
                       "X-Metadata": metadata, "X-Zotero-Connector-API-Version": "3"}
            status, _ = request("/connector/saveAttachment", method="POST", body=pdf["bytes"],
                                headers=headers, timeout=120)
            # Zotero returns 200 (NOT success) when files are not editable.
            if status != 201:
                raise ZoteroError("saveAttachment was not confirmed with HTTP 201")
        stage = "verify"
        assert_same_target(target, bool(pdf))
        parent = get_json(API_ROOT + "/items/" + urllib.parse.quote(parent["key"], safe=""))
        identity_reason = identity_review_reason(item, parent.get("data", {}))
        if identity_reason:
            raise ZoteroError("Saved parent identity changed: " + identity_reason)
        report = verify_parent(parent, collection_key, pdf)
        complete = report["in_collection"] and (not pdf or report["pdf_verified"])
        return dict(report, status=("saved_complete" if pdf else "saved_metadata") if complete else "partial",
                    stage=stage, writes_performed=True, retry_writes_automatically=False)
    except (ZoteroError, OSError, UnicodeError, ValueError) as exc:
        return {"status": "partial" if parent else "uncertain", "stage": stage,
                "item_key": parent.get("key") if parent else None,
                "collection_key": collection_key, "error": str(exc),
                "writes_performed": "possibly" if not parent else True,
                "retry_writes_automatically": False,
                "action": "Inspect Zotero for an existing parent/attachment before any retry; never create a second parent blindly"}


def probe():
    status, body = request("/connector/ping")
    target = selected_target()
    return {"connector_status": status, "connector_running": b"Zotero is running" in body,
            "selected_target": {k: target.get(k) for k in
                                ("name", "id", "libraryID", "libraryName", "editable", "libraryEditable", "filesEditable")}}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="Read connector status and selected target; never change settings")
    find = sub.add_parser("find", help="Find normalized DOI/title matches in personal library")
    find.add_argument("--doi", default="")
    find.add_argument("--title", default="")
    cmd = sub.add_parser("save", help="Import one validated record; no automatic write retries")
    source = cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--metadata")
    source.add_argument("--metadata-json")
    source.add_argument("--metadata-stdin", action="store_true")
    cmd.add_argument("--pdf")
    cmd.add_argument("--metadata-only", action="store_true")
    cmd.add_argument("--pdf-identity-confirmed", action="store_true")
    cmd.add_argument("--source-url", default="")
    cmd.add_argument("--pdf-url", default="")
    cmd.add_argument("--attachment-title", default="Full Text PDF")
    cmd.add_argument("--expect-collection", required=True)
    cmd.add_argument("--expect-collection-key", default="")
    cmd.add_argument("--expect-library-id", type=int)
    cmd.add_argument("--allow-duplicate", action="store_true", help=argparse.SUPPRESS)
    cmd.add_argument("--dry-run", action="store_true")
    return parser


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        sys.stderr.reconfigure(encoding="utf-8", errors="strict")
    args = build_parser().parse_args()
    try:
        if args.command == "probe":
            result = probe()
        elif args.command == "find":
            matches = find_matches(args.doi, args.title)
            result = {"found": bool(matches), "ambiguous": len(matches) > 1,
                      "items": [{"key": r["key"], "title": r.get("data", {}).get("title"),
                                 "doi": r.get("data", {}).get("DOI")} for r in matches]}
        else:
            result = save(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "save" and result["status"] not in SUCCESS:
            raise SystemExit(2)
    except (ZoteroError, OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
