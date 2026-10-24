"""Synthetic-byte tests for explicitly approved, non-overwriting PDF-copy repair."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "ntu-paper-batch-download" / "scripts" / "repair_identical_mediabox.py"
SPEC = importlib.util.spec_from_file_location("pdf_repair", SCRIPT)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)

PAGES = b"<< /Type /Pages /Count 1 /Kids [3 0 R] /MediaBox[0 0 612 792] /MediaBox[0 0 612 792] >>"


def make_pdf(pages=PAGES, extra_catalog=b"", extra_page=b"", content=b"q Q", fake_pages_in_stream=False,
             outer_stream_free=False, resources=b"<< >>"):
    if fake_pages_in_stream:
        content = b"1 0 obj\n" + PAGES + b"\nendobj\n"
    objects = [pages, b"<< /Type /Catalog /Pages 1 0 R " + extra_catalog + b" >>",
               b"<< /Type /Page /Parent 1 0 R /Resources " + resources + b" "
               + (b"" if outer_stream_free else b"/Contents 4 0 R ") + extra_page + b" >>",
               b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    if fake_pages_in_stream:
        offsets[1] = data.index(b"1 0 obj\n", offsets[4])
    xref = len(data)
    data += b"xref\n0 5\n0000000000 65535 f \n"
    data += b"".join(b"0000000000 00000 f \n" if outer_stream_free and index == 4
                     else ("%010d 00000 n \n" % offset).encode()
                     for index, offset in enumerate(offsets[1:], 1))
    data += b"trailer\n<< /Size 5 /Root 2 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    return data


class PdfRepairTests(unittest.TestCase):
    def test_identical_duplicate_repaired_with_exactly_bounded_changes(self):
        from pypdf import PdfReader
        original = make_pdf()
        fixed, report = repair.prepare_repaired_bytes(original)
        self.assertEqual(len(fixed), len(original))
        self.assertEqual(report["pages"], 1)
        self.assertTrue(report["strict_validation_passed"])
        span = report["repairs"][0]
        start, end = span["offset"], span["offset"] + span["length"]
        self.assertEqual(fixed[:start], original[:start])
        self.assertEqual(fixed[end:], original[end:])
        self.assertEqual(fixed[start:end], b" " * (end - start))
        reader = PdfReader(io.BytesIO(fixed), strict=True)
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(list(reader.pages[0].mediabox), [0, 0, 612, 792])
        self.assertEqual(reader.pages[0].get_contents().get_data(), b"q Q")

    def test_conflicting_mediabox_values_rejected(self):
        bad = PAGES.replace(b"/MediaBox[0 0 612 792] >>", b"/MediaBox[0 0 600 792] >>")
        with self.assertRaisesRegex(repair.RepairError, "not byte-identical"):
            repair.prepare_repaired_bytes(make_pdf(pages=bad))

    def test_bounded_cnki_footer_is_preserved_unchanged(self):
        footer = (b"WebFastLoad\xef\xbb\xbf<FileProperty><Doi /><FileName>EXAMPLE001</FileName>"
                  b"<TableName>CJFDTotal</TableName><Type>JOURNAL</Type></FileProperty>")
        fixed, _ = repair.prepare_repaired_bytes(make_pdf() + footer)
        self.assertTrue(fixed.endswith(footer))

    def test_unknown_trailing_payload_rejected(self):
        for suffix in (b"hidden payload", b"WebFastLoad<evil/>"):
            with self.subTest(suffix=suffix), self.assertRaises(repair.RepairError):
                repair.prepare_repaired_bytes(make_pdf() + suffix)

    def test_equivalent_but_different_literal_arrays_rejected(self):
        bad = PAGES.replace(b"/MediaBox[0 0 612 792] >>", b"/MediaBox[0.0 0 612 792] >>")
        with self.assertRaisesRegex(repair.RepairError, "not byte-identical"):
            repair.prepare_repaired_bytes(make_pdf(pages=bad))

    def test_other_duplicate_keys_rejected(self):
        for options in ({"pages": PAGES.replace(b"/Count 1", b"/Count 1 /Count 1")},
                        {"extra_page": b"/Rotate 0 /Rotate 0"}):
            with self.subTest(options=options), self.assertRaises(repair.RepairError):
                repair.prepare_repaired_bytes(make_pdf(**options))

    def test_signature_markers_rejected(self):
        for marker in (b"/ByteRange [0 1 2 3]", b"/SigFlags 3", b"/FT /Sig", b"/#42yteRange [0 1 2 3]"):
            with self.subTest(marker=marker), self.assertRaises(repair.RepairError):
                repair.prepare_repaired_bytes(make_pdf(extra_catalog=marker))

    def test_fake_pages_object_inside_stream_is_rejected(self):
        with self.assertRaises(repair.RepairError):
            repair.prepare_repaired_bytes(make_pdf(fake_pages_in_stream=True))

    def test_fake_pages_inside_undeclared_free_stream_rejected(self):
        with self.assertRaises(repair.RepairError):
            repair.prepare_repaired_bytes(make_pdf(fake_pages_in_stream=True, outer_stream_free=True))

    def test_unrelated_structure_damage_rejected(self):
        broken = make_pdf().replace(b"/Contents 4 0 R", b"/Contents 9 0 R")
        with self.assertRaises(repair.RepairError):
            repair.prepare_repaired_bytes(broken)

    def test_dangling_font_resource_rejected(self):
        broken = make_pdf(resources=b"<< /Font << /F1 99 0 R >> >>",
                          content=b"BT /F1 12 Tf (hello) Tj ET")
        with self.assertRaisesRegex(repair.RepairError, "Dangling/free"):
            repair.prepare_repaired_bytes(broken)

    def test_no_duplicate_and_non_pdf_are_rejected(self):
        normal = PAGES.replace(b" /MediaBox[0 0 612 792] >>", b" >>")
        for data in (b"<html>not pdf</html>", make_pdf(pages=normal)):
            with self.subTest(data=data[:40]), self.assertRaises(repair.RepairError):
                repair.prepare_repaired_bytes(data)

    def test_incremental_marker_rejected(self):
        data = make_pdf().replace(b"/Size 5 /Root", b"/Prev 0 /Size 5 /Root")
        with self.assertRaises(repair.RepairError):
            repair.prepare_repaired_bytes(data)

    def test_file_repair_requires_approval_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "original.pdf", Path(directory) / "repaired.pdf"
            original = make_pdf()
            source.write_bytes(original)
            with self.assertRaisesRegex(repair.RepairError, "approved"):
                repair.repair_pdf(source, output)
            self.assertFalse(output.exists())
            report = repair.repair_pdf(source, output, approved=True)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(report["original_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(report["original_sha256"], report["original_sha256_after"])
            self.assertNotEqual(report["original_sha256"], report["repaired_sha256"])
            self.assertEqual(report["repaired_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())

    def test_existing_and_inplace_outputs_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "original.pdf", Path(directory) / "existing.pdf"
            source.write_bytes(make_pdf())
            output.write_bytes(b"must remain")
            for target in (source, output):
                with self.subTest(target=target), self.assertRaises(repair.RepairError):
                    repair.repair_pdf(source, target, approved=True)
            self.assertEqual(source.read_bytes(), make_pdf())
            self.assertEqual(output.read_bytes(), b"must remain")


if __name__ == "__main__":
    unittest.main()
