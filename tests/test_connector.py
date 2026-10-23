"""Offline tests only: synthetic metadata/PDFs, all Zotero IO is mocked."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "ntu-paper-batch-download" / "scripts" / "zotero_connector_batch.py"
SPEC = importlib.util.spec_from_file_location("batch", SCRIPT)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)

METADATA = {"itemType": "journalArticle", "title": "光电姿态检测测试", "date": "2025",
            "creators": [{"creatorType": "author", "lastName": "张", "firstName": "三"}],
            "DOI": "10.1234/example", "url": "https://example.org/article"}
TARGET = {"libraryID": 1, "libraryName": "My Library", "libraryEditable": True,
          "filesEditable": True, "editable": True, "id": 9, "name": "test",
          "targets": [{"id": "L1", "name": "My Library", "level": 0},
                      {"id": "C9", "name": "test", "level": 1}]}
COLLECTION = {"key": "COLL0001", "data": {"name": "test", "parentCollection": False}}
PARENT = {"key": "PARENT01", "data": dict(METADATA, collections=["COLL0001"])}
PDF_BYTES = b"%PDF-synthetic-mocked-only"
PDF = {"bytes": PDF_BYTES, "sha256": hashlib.sha256(PDF_BYTES).hexdigest(),
       "pages": 1, "size": len(PDF_BYTES)}
CHILD = {"key": "CHILD001", "data": {"itemType": "attachment", "parentItem": "PARENT01",
         "linkMode": "imported_file", "contentType": "application/pdf"}}


def args(*extra):
    return batch.build_parser().parse_args(["save", "--metadata-json", json.dumps(METADATA),
        "--expect-collection", "test", "--pdf", "synthetic.pdf", "--pdf-identity-confirmed", *extra])


class OfflineTests(unittest.TestCase):
    def setUp(self):
        # Any forgotten network mock fails immediately; no live Zotero is touched.
        self.network = mock.patch.object(batch, "request", side_effect=AssertionError("Unexpected network IO"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_unicode_title_normalization(self):
        self.assertEqual(batch.normalize_title("  ＴＥＳＴ\u200b\ufeff 光电\u202e\n检测 "), "test 光电 检测")

    def test_title_only_identity_needs_author_and_year(self):
        actual = dict(METADATA)
        actual.pop("DOI")
        self.assertIsNone(batch.identity_review_reason(METADATA, actual))
        self.assertIsNotNone(batch.identity_review_reason(METADATA, dict(actual, date="2024")))
        self.assertIsNotNone(batch.identity_review_reason(METADATA, dict(actual, creators=[])))
        other = dict(actual, creators=[{"creatorType": "author", "lastName": "Someone Else"}])
        self.assertIsNotNone(batch.identity_review_reason(METADATA, other))

    def test_shared_doi_is_stronger_than_title_formatting(self):
        actual = dict(METADATA, title="Different formatting", date="2024")
        self.assertIsNone(batch.identity_review_reason(METADATA, actual))

    def test_doi_normalization_and_canonical_search(self):
        with mock.patch.object(batch, "query_items", return_value=[]) as query, \
             mock.patch.object(batch, "paginated", return_value=[]):
            batch.find_matches(" DOI: https://doi.org/10.1234/ABC%2FDEF ", " A\u200b Title ")
        self.assertEqual(query.call_args_list, [mock.call("10.1234/abc/def"), mock.call("a title")])

    def test_conflicting_doi_is_not_title_duplicate(self):
        row = {"key": "OTHER001", "data": {"title": METADATA["title"], "DOI": "10.5678/other"}}
        with mock.patch.object(batch, "query_items", return_value=[row]):
            with self.assertRaisesRegex(batch.ZoteroError, "conflicting DOI"):
                batch.find_matches(METADATA["DOI"], METADATA["title"])

    def test_invisible_title_found_by_bounded_inventory_fallback(self):
        row = {"key": "OTHER001", "data": {"title": "光\u200b电姿态检测测试"}}
        with mock.patch.object(batch, "query_items", return_value=[]), \
             mock.patch.object(batch, "paginated", return_value=[row]):
            self.assertEqual(batch.find_matches(title=METADATA["title"])[0]["key"], "OTHER001")

    def test_pagination_reads_second_page(self):
        rows = [{"key": str(i)} for i in range(100)]
        with mock.patch.object(batch, "get_json", side_effect=[rows, [{"key": "last"}]]) as get:
            self.assertEqual(len(batch.paginated("/api/users/0/items/top")), 101)
        self.assertIn("start=100", get.call_args_list[1].args[0])

    def test_pagination_repeated_page_fails_closed(self):
        rows = [{"key": str(i)} for i in range(100)]
        with mock.patch.object(batch, "get_json", side_effect=[rows, rows]):
            with self.assertRaisesRegex(batch.ZoteroError, "repeated a key"):
                batch.paginated("/api/users/0/items/top")

    def test_garbled_metadata_rejected(self):
        for bad in ("????", "title\ufffd", "title\x01"):
            with self.subTest(bad=bad), self.assertRaises(batch.ZoteroError):
                batch.load_metadata(inline=json.dumps(dict(METADATA, title=bad)))

    def test_bom_utf8_metadata_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(METADATA, ensure_ascii=False), encoding="utf-8-sig")
            self.assertEqual(batch.load_metadata(path=str(path))["title"], METADATA["title"])

    def test_embedded_remote_attachments_rejected(self):
        data = dict(METADATA, attachments=[{"url": "https://example.org/remote.pdf"}])
        with self.assertRaisesRegex(batch.ZoteroError, "Embedded attachments"):
            batch.load_metadata(inline=json.dumps(data))

    def test_signed_urls_and_url_credentials_rejected(self):
        for url in ("https://example.org/pdf?token=SECRET", "https://user:SECRET@example.org/a",
                    "file:///private/example.pdf", "https://example.org/pdf?invoice=SECRET",
                    "https://example.org/pdf?SAMLResponse=SECRET", "https://example.org/pdf?RelayState=SECRET",
                    "https://example.org/pdf?HMAC=SECRET", "https://example.org/pdf?api_key=SECRET",
                    "https://example.org/pdf?access_key=SECRET", "https://example.org/pdf?key=SECRET",
                    "https://example.org/pdf?auth=SECRET", "https://example.org/pdf?sig=SECRET",
                    "https://example.org/pdf?v=SECRET", "https://example.org/pdf#access_token=SECRET"):
            with self.subTest(url=url), self.assertRaises(batch.ZoteroError):
                batch.stable_url(url)

    def test_real_synthetic_pdf_parses_and_html_does_not(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with path.open("wb") as stream:
                writer.write(stream)
            self.assertEqual(batch.validate_pdf(path)["pages"], 1)
            path.write_bytes(b"<!DOCTYPE html><html>login</html>")
            with self.assertRaisesRegex(batch.ZoteroError, "not a PDF"):
                batch.validate_pdf(path)
            path.write_bytes(b"%PDF-1.7\ntruncated")
            with self.assertRaisesRegex(batch.ZoteroError, "parser rejected"):
                batch.validate_pdf(path)

    def test_group_target_rejected(self):
        group = dict(TARGET, libraryID=2)
        with self.assertRaisesRegex(batch.ZoteroError, "personal library"):
            batch.target_path(group)

    def test_exact_collection_key_and_path(self):
        with mock.patch.object(batch, "selected_target", return_value=TARGET), \
             mock.patch.object(batch, "paginated", return_value=[COLLECTION]):
            target, key = batch.resolve_target(args("--expect-collection-key", "COLL0001"), True)
            self.assertEqual(key, "COLL0001")
            with self.assertRaisesRegex(batch.ZoteroError, "Collection key"):
                batch.resolve_target(args("--expect-collection-key", "OTHER001"), True)

    def test_ambiguous_collection_names_fail_closed(self):
        duplicate = dict(COLLECTION, key="COLL0002")
        with mock.patch.object(batch, "selected_target", return_value=TARGET), \
             mock.patch.object(batch, "paginated", return_value=[COLLECTION, duplicate]):
            with self.assertRaisesRegex(batch.ZoteroError, "ambiguous"):
                batch.resolve_target(args(), True)

    def test_files_not_editable_prevents_save(self):
        with mock.patch.object(batch, "selected_target", return_value=dict(TARGET, filesEditable=False)):
            with self.assertRaisesRegex(batch.ZoteroError, "stored attachments"):
                batch.resolve_target(args(), True)

    def test_target_change_detected(self):
        with mock.patch.object(batch, "selected_target", return_value=dict(TARGET, id=10)):
            with self.assertRaisesRegex(batch.ZoteroError, "target changed"):
                batch.assert_same_target(TARGET, True)

    def test_missing_identity_confirmation_blocks_before_io(self):
        arguments = args()
        arguments.pdf_identity_confirmed = False
        with self.assertRaisesRegex(batch.ZoteroError, "first-page"):
            batch.save(arguments)

    def test_allow_duplicate_is_explicitly_disabled(self):
        with self.assertRaisesRegex(batch.ZoteroError, "disabled"):
            batch.save(args("--allow-duplicate"))

    def save_context(self, existing=None, children=None, request_status=201):
        patches = {
            "load_metadata": mock.Mock(return_value=METADATA),
            "validate_pdf": mock.Mock(return_value=PDF),
            "resolve_target": mock.Mock(return_value=(TARGET, "COLL0001")),
            "find_matches": mock.Mock(return_value=existing or []),
            "selected_target": mock.Mock(return_value=TARGET),
            "observe_created": mock.Mock(return_value=PARENT),
            "get_json": mock.Mock(return_value=PARENT),
            "get_children": mock.Mock(return_value=[CHILD] if children is None else children),
            "attachment_bytes": mock.Mock(return_value=PDF_BYTES),
            "request": mock.Mock(return_value=(request_status, b"")),
        }
        return mock.patch.multiple(batch, **patches), patches

    def test_dry_run_does_no_connector_writes(self):
        context, patched = self.save_context()
        with context:
            result = batch.save(args("--dry-run"))
        self.assertEqual(result["status"], "dry_run")
        patched["request"].assert_not_called()

    def test_duplicate_missing_pdf_needs_attachment_no_second_parent(self):
        context, patched = self.save_context(existing=[PARENT], children=[])
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "needs_attachment")
        patched["request"].assert_not_called()

    def test_title_collision_does_not_offer_wrong_parent_repair(self):
        other = {"key": "OTHER001", "data": dict(PARENT["data"], DOI="", date="2024")}
        context, patched = self.save_context(existing=[other], children=[])
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "needs_identity_review")
        patched["request"].assert_not_called()
        patched["get_children"].assert_not_called()

    def test_missing_canonical_url_blocks_without_write(self):
        context, patched = self.save_context()
        patched["load_metadata"].return_value = dict(METADATA, url="")
        with context, self.assertRaisesRegex(batch.ZoteroError, "canonical source URL"):
            batch.save(args("--dry-run"))
        patched["request"].assert_not_called()

    def test_duplicate_outside_collection_is_not_complete(self):
        outside = {"key": "PARENT01", "data": dict(PARENT["data"], collections=[])}
        context, patched = self.save_context(existing=[outside])
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "needs_collection")
        patched["request"].assert_not_called()

    def test_duplicate_hash_match_is_complete_without_write(self):
        context, patched = self.save_context(existing=[PARENT])
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "duplicate_complete")
        patched["request"].assert_not_called()

    def test_ambiguous_duplicates_block_no_write(self):
        context, patched = self.save_context(existing=[PARENT, dict(PARENT, key="PARENT02")])
        with context, self.assertRaisesRegex(batch.ZoteroError, "Ambiguous duplicate"):
            batch.save(args())
        patched["request"].assert_not_called()

    def test_save_checks_stored_file_and_ascii_header(self):
        context, patched = self.save_context()
        with context:
            result = batch.save(args("--attachment-title", "全文 PDF"))
        self.assertEqual(result["status"], "saved_complete")
        self.assertTrue(result["pdf_verified"])
        self.assertEqual(patched["request"].call_count, 2)
        metadata_header = patched["request"].call_args_list[1].kwargs["headers"]["X-Metadata"]
        metadata_header.encode("ascii")  # Regression: old helper failed on Chinese headers.
        self.assertEqual(json.loads(metadata_header)["title"], "全文 PDF")

    def test_no_stored_attachment_is_partial_not_saved(self):
        context, patched = self.save_context(children=[])
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "partial")

    def test_wrong_stored_pdf_bytes_is_partial(self):
        context, patched = self.save_context()
        patched["attachment_bytes"].return_value = b"%PDF-wrong-article"
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "partial")

    def test_attachment_200_not_success(self):
        context, patched = self.save_context()
        patched["request"].side_effect = [(201, b""), (200, b"Library files are not editable")]
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stage"], "saveAttachment")

    def test_timeout_after_parent_write_is_uncertain_no_retry(self):
        context, patched = self.save_context()
        patched["request"].side_effect = batch.ZoteroError("Local request timed out")
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["retry_writes_automatically"])
        self.assertEqual(patched["request"].call_count, 1)

    def test_attachment_timeout_preserves_parent_key(self):
        context, patched = self.save_context()
        patched["request"].side_effect = [(201, b""), batch.ZoteroError("Local request timed out")]
        with context:
            result = batch.save(args())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["item_key"], "PARENT01")
        self.assertEqual(patched["request"].call_count, 2)

    def test_notes_and_linked_files_do_not_count_as_stored_pdf(self):
        rows = [CHILD, {"key": "NOTE0001", "data": {"itemType": "note"}},
                {"key": "LINK0001", "data": dict(CHILD["data"], linkMode="linked_file")}]
        self.assertEqual([r["key"] for r in batch.stored_pdfs(rows)], ["CHILD001"])

    def test_remote_attachment_url_not_followed(self):
        with mock.patch.object(batch, "request", return_value=(200, b"https://example.org/private.pdf")):
            with self.assertRaisesRegex(batch.ZoteroError, "not a local stored file"):
                batch.attachment_bytes("CHILD001")

    def test_plaintext_local_attachment_url_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.pdf"
            path.write_bytes(PDF_BYTES)
            with mock.patch.object(batch, "request", return_value=(200, path.as_uri().encode("utf-8"))):
                self.assertEqual(batch.attachment_bytes("CHILD001"), PDF_BYTES)

    def test_nested_collection_path_maps_to_exact_key(self):
        nested = dict(TARGET, id=10, name="nested", targets=TARGET["targets"] + [
            {"id": "C10", "name": "nested", "level": 2}])
        child = {"key": "COLL0002", "data": {"name": "nested", "parentCollection": "COLL0001"}}
        arguments = args()
        arguments.expect_collection = "nested"
        arguments.expect_collection_key = "COLL0002"
        with mock.patch.object(batch, "selected_target", return_value=nested), \
             mock.patch.object(batch, "paginated", return_value=[COLLECTION, child]):
            self.assertEqual(batch.resolve_target(arguments, True)[1], "COLL0002")


if __name__ == "__main__":
    unittest.main()
