#!/usr/bin/env python3
"""Explicitly approved, narrow PDF-copy repair; never modifies the source PDF.

Only a traditional-xref, flat root /Pages dictionary with two consecutive,
byte-identical numeric /MediaBox arrays is supported. The second entry becomes
equal-length whitespace. Every resulting object/page is then parsed strictly.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import sys

MAX_BYTES = 150 * 1024 * 1024
MAX_OBJECT_BYTES = 1024 * 1024
MAX_OBJECTS = 20000
MAX_PAGES = 1000
WHITESPACE = rb"[\x00\t\n\f\r ]"
NUMBER = rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
BOX = rb"\[" + WHITESPACE + rb"*" + NUMBER + (WHITESPACE + rb"+" + NUMBER) * 3 + WHITESPACE + rb"*\]"
# CNKI can append this bounded, inert bibliographic footer after %%EOF.
# Preserve it byte-for-byte; arbitrary trailing payload remains unsupported.
CNKI_FOOTER = (rb"WebFastLoad\xef\xbb\xbf<FileProperty>"
               rb"(?:<Doi\s*/>|<Doi>[A-Za-z0-9._/;():-]{0,256}</Doi>)"
               rb"<FileName>[A-Za-z0-9_]{1,128}</FileName>"
               rb"<TableName>[A-Za-z0-9_]{1,64}</TableName>"
               rb"<Type>[A-Za-z0-9_]{1,32}</Type></FileProperty>")


class RepairError(RuntimeError):
    pass


class CaptureWarnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def reject_signature(value, seen=None):
    """Inspect already parsed direct containers, without resolving references."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        if "/ByteRange" in value or "/SigFlags" in value or value.get("/Type") == "/Sig" or value.get("/FT") == "/Sig":
            raise RepairError("Digitally signed/signature-bearing PDFs are not supported")
        for child in value.values():
            reject_signature(child, seen)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_signature(child, seen)


def final_xref_offset(data):
    tail = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*(?:" + CNKI_FOOTER + rb")?\s*$", data)
    if not tail:
        raise RepairError("Unsupported EOF/trailing data; only a bounded CNKI footer is allowed")
    offset = int(tail.group(1))
    if data[offset:offset + 4] != b"xref":
        raise RepairError("Only a traditional final xref table is supported")
    return offset


def traditional_reader(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise RepairError("Encrypted PDFs are not supported")
    if reader.trailer.get("/Prev") is not None or reader.trailer.get("/XRefStm") is not None:
        raise RepairError("Incremental or hybrid-xref PDFs are not supported")
    if reader.xref_objStm:
        raise RepairError("Compressed object streams are not supported")
    final_xref_offset(data)
    return reader


def validate_references(value, xref, seen=None):
    """Reject dangling indirect resources without dereferencing or looping cycles."""
    from pypdf.generic import IndirectObject
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, IndirectObject):
        offset = xref.get(value.generation, {}).get(value.idnum)
        if value.idnum == 0 or not isinstance(offset, int) or offset <= 0:
            raise RepairError("Dangling/free indirect object reference")
    elif isinstance(value, dict):
        for child in value.values():
            validate_references(child, xref, seen)
    elif isinstance(value, (list, tuple)):
        for child in value:
            validate_references(child, xref, seen)


def strict_validate_all(data, repair_span, expected_pages):
    """Strict parsing plus non-overlapping xref-object spans prevents stream edits."""
    from pypdf.generic import IndirectObject, read_object
    reader = traditional_reader(data)
    reject_signature(reader.trailer)
    validate_references(reader.trailer, reader.xref)
    offsets = []
    for generation, entries in reader.xref.items():
        for object_id, offset in entries.items():
            if object_id == 0:
                continue
            if not isinstance(offset, int) or not 0 < offset < len(data):
                raise RepairError("Invalid xref object offset")
            offsets.append((offset, object_id, generation))
    if not offsets or len(offsets) > MAX_OBJECTS:
        raise RepairError("Unsupported xref object count")
    offsets.sort()
    if len({offset for offset, _, _ in offsets}) != len(offsets):
        raise RepairError("Overlapping xref object offsets")
    spans = []
    for offset, object_id, generation in offsets:
        stream = reader.stream
        stream.seek(offset)
        found_id, found_generation = reader.read_object_header(stream)
        if (found_id, found_generation) != (object_id, generation):
            raise RepairError("Object header does not agree with its xref entry")
        value = read_object(stream, reader)
        end = stream.tell()
        reject_signature(value)
        validate_references(value, reader.xref)
        # The strict parser consumes dictionary/stream bytes; endobj follows.
        ending = re.match(WHITESPACE + rb"*endobj\b", data[end:end + 64])
        if not ending:
            raise RepairError("Unsupported object termination")
        end += ending.end()
        spans.append((offset, end))
    for (_, previous_end), (next_start, _) in zip(spans, spans[1:]):
        if previous_end > next_start:
            raise RepairError("Object overlap: repair could lie inside a stream/object")
    # Xref membership alone is insufficient: an undeclared outer stream could
    # contain a fake, xref-declared Pages object. Reject any physical objects or
    # payload outside declared spans. Comments must end before the next span.
    xref_offset = final_xref_offset(data)
    if spans[-1][1] > xref_offset:
        raise RepairError("Object overlaps the final xref table")
    gaps = [data[:spans[0][0]]]
    gaps.extend(data[previous_end:next_start]
                for (_, previous_end), (next_start, _) in zip(spans, spans[1:]))
    gaps.append(data[spans[-1][1]:xref_offset])
    inert_gap = rb"(?:" + WHITESPACE + rb"|%[^\r\n]*(?:\r\n|\r|\n))*"
    if any(re.fullmatch(inert_gap, gap) is None for gap in gaps):
        raise RepairError("Undeclared physical object/payload outside xref spans")
    containing = [(start, end) for start, end in spans if start <= repair_span[0] < repair_span[1] <= end]
    if len(containing) != 1:
        raise RepairError("Repair span is not inside exactly one declared object")
    pages = list(reader.pages)
    if len(pages) != expected_pages:
        raise RepairError("Page count changed or disagrees with the flat page tree")
    root_pages = reader.trailer["/Root"]["/Pages"]
    kids = root_pages["/Kids"]
    if len(kids) != expected_pages:
        raise RepairError("Only a flat page tree is supported")
    for reference in kids:
        if not isinstance(reference, IndirectObject) or reference.get_object().get("/Type") != "/Page":
            raise RepairError("Only direct leaf-page Kids are supported")
    for page in pages:
        box = page.mediabox
        if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
            raise RepairError("Invalid page MediaBox")
        contents = page.get_contents()
        if contents is not None:
            contents.get_data()
            _ = contents.operations
    return len(pages)


def prepare_repaired_bytes(data):
    """Pure in-memory validation/repair; callers need approval before writing."""
    from pypdf.generic import IndirectObject
    if not 0 < len(data) <= MAX_BYTES or not data.startswith(b"%PDF-"):
        raise RepairError("Input is not a bounded, nonempty PDF")
    if re.search(rb"/(?:ByteRange|SigFlags)\b|/(?:Type|FT)\s*/Sig\b", data):
        raise RepairError("Digitally signed/signature-bearing PDFs are not supported")
    capture = CaptureWarnings()
    logger = logging.getLogger("pypdf")
    logger.addHandler(capture)
    try:
        reader = traditional_reader(data)
        catalog = reader.trailer["/Root"]
        if catalog.get("/Type") != "/Catalog":
            raise RepairError("Invalid root catalog")
        reference = catalog.raw_get("/Pages")
        if not isinstance(reference, IndirectObject):
            raise RepairError("Root Pages must be a directly addressable indirect object")
        offset = reader.xref.get(reference.generation, {}).get(reference.idnum)
        if not isinstance(offset, int) or not 0 < offset < len(data):
            raise RepairError("Root Pages object has no supported xref offset")
        ws = WHITESPACE + rb"*"
        pattern = (
            str(reference.idnum).encode("ascii") + WHITESPACE + rb"+" + str(reference.generation).encode("ascii")
            + WHITESPACE + rb"+obj\b" + ws + rb"<<" + ws + rb"/Type" + ws + rb"/Pages" + ws
            + rb"/Count" + WHITESPACE + rb"+(?P<count>\d+)" + ws
            + rb"/Kids" + ws + rb"\[(?P<kids>(?:" + ws + rb"\d+" + WHITESPACE + rb"+\d+" + WHITESPACE + rb"+R\b)+" + ws + rb")\]" + ws
            + rb"/MediaBox" + ws + rb"(?P<first>" + BOX + rb")" + ws
            + rb"(?P<duplicate>/MediaBox" + ws + rb"(?P<second>" + BOX + rb"))" + ws
            + rb">>" + ws + rb"endobj\b"
        )
        match = re.match(pattern, data[offset:offset + MAX_OBJECT_BYTES])
        if not match:
            raise RepairError("Root Pages does not match the supported simple consecutive-MediaBox dictionary")
        if match.group("first") != match.group("second"):
            raise RepairError("Repeated MediaBox values are not byte-identical")
        numbers = re.findall(NUMBER, match.group("first"))
        if not all(math.isfinite(float(value)) for value in numbers):
            raise RepairError("Non-finite MediaBox coordinates")
        if float(numbers[2]) <= float(numbers[0]) or float(numbers[3]) <= float(numbers[1]):
            raise RepairError("MediaBox dimensions must be positive")
        count = int(match.group("count"))
        kids = re.findall(rb"(\d+)\s+(\d+)\s+R", match.group("kids"))
        if not 0 < count <= MAX_PAGES or count != len(kids) or len(set(kids)) != count:
            raise RepairError("Unsupported flat page count or repeated Kids")
        start = offset + match.start("duplicate")
        end = offset + match.end("duplicate")
        repaired = data[:start] + b" " * (end - start) + data[end:]
        pages = strict_validate_all(repaired, (start, end), count)
        if capture.messages:
            raise RepairError("Parser warnings remain; no other structural recovery is permitted")
        return repaired, {"pages": pages, "size_unchanged": len(data) == len(repaired),
                          "repairs": [{"object_number": reference.idnum, "generation": reference.generation,
                                       "offset": start, "length": end - start, "key": "/MediaBox",
                                       "identical_array": match.group("first").decode("ascii")}],
                          "strict_validation_passed": True}
    except RepairError:
        raise
    except Exception as exc:
        raise RepairError("Strict validation failed; unsupported PDF structure (%s)" % type(exc).__name__) from exc
    finally:
        logger.removeHandler(capture)


def repair_pdf(input_path, output_path, *, approved=False):
    if not approved:
        raise RepairError("Explicit --approved is required before creating a repaired copy")
    source = Path(input_path).expanduser()
    destination = Path(output_path).expanduser()
    if source.is_symlink() or destination.is_symlink():
        raise RepairError("Symlink input/output paths are not accepted")
    source = source.resolve(strict=True)
    if source == destination.resolve(strict=False):
        raise RepairError("Output must differ from the preserved original")
    if destination.exists():
        raise RepairError("Output already exists; no file will be overwritten")
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir() or not source.is_file():
        raise RepairError("Input must be a regular file and output parent must exist")
    destination = parent / destination.name
    if not 0 < source.stat().st_size <= MAX_BYTES:
        raise RepairError("Input exceeds the PDF size limit or is empty")
    original = source.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    repaired, report = prepare_repaired_bytes(original)
    repaired_sha = hashlib.sha256(repaired).hexdigest()
    # Exclusive creation also rejects an existing file/symlink introduced meanwhile.
    with destination.open("xb") as stream:
        stream.write(repaired)
        stream.flush()
        os.fsync(stream.fileno())
    original_after = hashlib.sha256(source.read_bytes()).hexdigest()
    output_after = hashlib.sha256(destination.read_bytes()).hexdigest()
    if original_after != original_sha or output_after != repaired_sha:
        raise RepairError("Post-write hash mismatch; inspect both files manually (neither is overwritten or deleted)")
    return dict(report, status="repaired_copy", original_path=str(source), repaired_path=str(destination),
                original_sha256=original_sha, original_sha256_after=original_after,
                repaired_sha256=repaired_sha, original_preserved=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    try:
        result = repair_pdf(args.input, args.output, approved=args.approved)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (RepairError, OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
