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
             mock.patch.object(batch, "bounded_items", return_value=batch.CandidateRows()):
            batch.find_matches(" DOI: https://doi.org/10.1234/ABC%2FDEF ", " A\u200b Title ")
        self.assertEqual(query.call_args_list[:2], [mock.call("10.1234/abc/def"), mock.call("a title")])
        self.assertLessEqual(query.call_count, 4)

    def test_conflicting_doi_is_not_title_duplicate(self):
        row = {"key": "OTHER001", "data": {"title": METADATA["title"], "DOI": "10.5678/other"}}
        with mock.patch.object(batch, "query_items", return_value=[row]), \
             mock.patch.object(batch, "bounded_items", return_value=batch.CandidateRows()):
            result = batch.find_matches(METADATA["DOI"], METADATA["title"])
        self.assertEqual(result, [])
        self.assertEqual(result.conflicts[0]["key"], "OTHER001")

    def test_invisible_title_found_by_bounded_inventory_fallback(self):
        row = {"key": "OTHER001", "data": {"title": "光\u200b电姿态检测测试"}}
        with mock.patch.object(batch, "query_items", return_value=[]), \
             mock.patch.object(batch, "bounded_items", return_value=batch.CandidateRows([row])):
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
            "find_matches": mock.Mock(return_value=batch.MatchResults(existing or [],
                coverage={"complete": True, "warning": "Synthetic complete coverage"})),
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
            for name in ("synthetic.pdf", "张三 - 2025 - 光电姿态检测.pdf"):
                with self.subTest(name=name):
                    path = Path(directory) / name
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

    def test_chinese_year_formats_without_doi(self):
        base = dict(METADATA, DOI="")
        for date in ("2025年", "2025年03期", "2025年10月23日", "2025-10-23"):
            with self.subTest(date=date):
                self.assertIsNone(batch.identity_review_reason(dict(base, date=date), base))
                self.assertIsNone(batch.identity_review_reason(base, dict(base, date=date)))
        self.assertIsNotNone(batch.identity_review_reason(dict(base, date="20250年"), base))
        self.assertIsNotNone(batch.identity_review_reason(dict(base, date="12025年"), base))

    def test_chinese_year_no_doi_full_save(self):
        for date in ("2025年", "2025年03期"):
            with self.subTest(date=date):
                context, patched = self.save_context()
                metadata = dict(METADATA, DOI="", date=date)
                parent = dict(PARENT, data=dict(metadata, collections=["COLL0001"]))
                patched["load_metadata"].return_value = metadata
                patched["observe_created"].return_value = parent
                patched["get_json"].return_value = parent
                with context:
                    result = batch.save(args())
                self.assertEqual(result["status"], "saved_complete")
                self.assertEqual(patched["request"].call_count, 2)

    def test_western_middle_names_and_real_initials_compatible(self):
        def metadata(first):
            return dict(METADATA, DOI="", creators=[{"creatorType": "author", "firstName": first, "lastName": "Smith"}])
        for left, right in (("John A.", "John"), ("John Alan", "John A."),
                            ("John", "J."), ("J. A.", "John Alan"), ("J", "J.")):
            with self.subTest(left=left, right=right):
                self.assertIsNone(batch.identity_review_reason(metadata(left), metadata(right)))
                self.assertIsNone(batch.identity_review_reason(metadata(right), metadata(left)))

    def test_two_full_given_names_do_not_collapse_to_initial(self):
        def metadata(first):
            return dict(METADATA, DOI="", creators=[{"creatorType": "author", "firstName": first, "lastName": "Smith"}])
        for left, right in (("John", "James"), ("John A.", "James A."), ("Anne-Marie", "Anne-Claire")):
            with self.subTest(left=left, right=right):
                self.assertIn("first author", batch.identity_review_reason(metadata(left), metadata(right)))

    def test_conflicting_middle_names_need_identity_review(self):
        def metadata(first):
            return dict(METADATA, DOI="", creators=[{"creatorType": "author", "firstName": first, "lastName": "Smith"}])
        for left, right in (("John Alan", "John Brian"), ("John A.", "John B."), ("J. A.", "John B.")):
            with self.subTest(left=left, right=right):
                self.assertIn("first author", batch.identity_review_reason(metadata(left), metadata(right)))
                self.assertIn("first author", batch.identity_review_reason(metadata(right), metadata(left)))

    def test_exact_doi_wins_over_same_title_other_version(self):
        conflict = {"key": "OTHER001", "data": dict(METADATA, DOI="10.1234/other")}
        with mock.patch.object(batch, "query_items", return_value=[conflict, PARENT]), \
             mock.patch.object(batch, "bounded_items", side_effect=AssertionError("Unneeded fallback")):
            result = batch.find_matches(METADATA["DOI"], METADATA["title"])
        self.assertEqual([r["key"] for r in result], ["PARENT01"])
        self.assertEqual(result.conflicts, [])

    def test_doi_conflict_returns_structured_review_no_write(self):
        context, patched = self.save_context()
        conflict = {"key": "OTHER001", "data": dict(METADATA, DOI="10.1234/other")}
        patched["find_matches"].return_value = batch.MatchResults([], conflicts=[conflict], coverage={"complete": True})
        with context:
            result = batch.save(args("--duplicate-search-reviewed"))
        self.assertEqual(result["status"], "needs_identity_review")
        self.assertEqual(result["candidate_item_key"], "OTHER001")
        patched["request"].assert_not_called()
        patched["get_children"].assert_not_called()

    def test_query_is_bounded_even_for_large_library(self):
        page1 = [{"key": str(i)} for i in range(100)]
        page2 = [{"key": str(i)} for i in range(100, 200)]
        with mock.patch.object(batch, "get_json", side_effect=[page1, page2]) as get:
            result = batch.query_items("broad keyword")
        self.assertEqual(len(result), 200)
        self.assertTrue(result.truncated)
        self.assertEqual(get.call_count, 2)

    def test_new_paper_save_never_calls_full_item_pagination(self):
        actual_find = batch.find_matches
        context, patched = self.save_context()
        with context, mock.patch.object(batch, "find_matches", actual_find), \
             mock.patch.object(batch, "query_items", return_value=batch.CandidateRows()), \
             mock.patch.object(batch, "bounded_items", return_value=batch.CandidateRows()), \
             mock.patch.object(batch, "paginated", side_effect=AssertionError("No full scan allowed")):
            result = batch.save(args())
        self.assertEqual(result["status"], "saved_complete")
        self.assertTrue(result["duplicate_check"]["complete"])
        self.assertEqual(patched["request"].call_count, 2)

    def test_truncated_negative_search_requires_duplicate_review(self):
        actual_find = batch.find_matches
        context, patched = self.save_context()
        recent = batch.CandidateRows([{"key": "K%07d" % i, "data": {"title": "Other"}}
                                      for i in range(50)], truncated=True)
        with context, mock.patch.object(batch, "find_matches", actual_find), \
             mock.patch.object(batch, "query_items", return_value=batch.CandidateRows()), \
             mock.patch.object(batch, "bounded_items", return_value=recent):
            result = batch.save(args())
        self.assertEqual(result["status"], "needs_duplicate_review")
        self.assertFalse(result["duplicate_check"]["complete"])
        self.assertIn("older/unindexed", result["coverage_warning"])
        patched["request"].assert_not_called()

    def test_explicit_duplicate_review_retains_warning(self):
        context, patched = self.save_context()
        patched["find_matches"].return_value = batch.MatchResults([], coverage={
            "complete": False, "warning": "Bounded coverage may omit older records"})
        with context:
            result = batch.save(args("--duplicate-search-reviewed"))
        self.assertEqual(result["status"], "saved_complete")
        self.assertTrue(result["duplicate_search_reviewed"])
        self.assertIn("omit older", result["coverage_warning"])

    def test_duplicate_fallback_checks_collection_and_recent(self):
        with mock.patch.object(batch, "query_items", return_value=batch.CandidateRows()), \
             mock.patch.object(batch, "bounded_items", return_value=batch.CandidateRows()) as bounded:
            result = batch.find_matches(METADATA["DOI"], METADATA["title"], collection_key="COLL0001")
        self.assertEqual(bounded.call_count, 2)
        self.assertIn("/collections/COLL0001/items/top", bounded.call_args_list[0].args[0])
        self.assertEqual(bounded.call_args_list[1].kwargs["page_size"], 50)
        self.assertTrue(result.coverage["complete"])

    def test_observe_parent_polls_only_bounded_recent_rows(self):
        with mock.patch.object(batch, "find_matches", side_effect=AssertionError("No duplicate scan while polling")), \
             mock.patch.object(batch, "paginated", side_effect=AssertionError("No full scan while polling")), \
             mock.patch.object(batch, "bounded_items", side_effect=[[], [], [PARENT], [PARENT]]) as bounded, \
             mock.patch.object(batch.time, "sleep"):
            result = batch.observe_created(METADATA["DOI"], METADATA["title"], collection_key="COLL0001")
        self.assertEqual(result["key"], "PARENT01")
        self.assertEqual(bounded.call_count, 4)
        for call in bounded.call_args_list:
            self.assertEqual(call.kwargs["max_pages"], 1)
            self.assertEqual(call.kwargs["page_size"], 50)
            self.assertEqual(call.args[1]["sort"], "dateAdded")

    def test_observe_does_not_select_old_conflicting_doi(self):
        conflict = dict(PARENT, data=dict(METADATA, DOI="10.1234/old"))
        with mock.patch.object(batch, "bounded_items", return_value=[conflict]):
            self.assertIsNone(batch.observe_created(METADATA["DOI"], METADATA["title"], 0,
                                                   collection_key="COLL0001"))

    def test_probe_resolves_collection_key_and_path(self):
        with mock.patch.object(batch, "request", return_value=(200, b"Zotero is running")), \
             mock.patch.object(batch, "selected_target", return_value=TARGET), \
             mock.patch.object(batch, "paginated", return_value=[COLLECTION]):
            result = batch.probe()
        self.assertTrue(result["connector_running"])
        self.assertTrue(result["collection_resolvable"])
        self.assertEqual(result["collection_key"], "COLL0001")
        self.assertEqual(result["collection_path"], ["test"])
        self.assertIsNone(result["target_warning"])

    def test_probe_noncollection_targets_return_diagnostic(self):
        for target in (dict(TARGET, id=None, name="My Library"), dict(TARGET, libraryID=2)):
            with self.subTest(target=target["id"]), \
                 mock.patch.object(batch, "request", return_value=(200, b"Zotero is running")), \
                 mock.patch.object(batch, "selected_target", return_value=target):
                result = batch.probe()
            self.assertTrue(result["connector_running"])
            self.assertFalse(result["collection_resolvable"])
            self.assertIsNone(result["collection_key"])
            self.assertTrue(result["target_warning"])

    def test_probe_ambiguous_collection_preserves_path(self):
        with mock.patch.object(batch, "request", return_value=(200, b"Zotero is running")), \
             mock.patch.object(batch, "selected_target", return_value=TARGET), \
             mock.patch.object(batch, "paginated", return_value=[COLLECTION, dict(COLLECTION, key="COLL0002")]):
            result = batch.probe()
        self.assertFalse(result["collection_resolvable"])
        self.assertEqual(result["collection_path"], ["test"])
        self.assertIn("ambiguous", result["target_warning"])

    def test_probe_local_api_failure_preserves_connector_status(self):
        with mock.patch.object(batch, "request", return_value=(200, b"Zotero is running")), \
             mock.patch.object(batch, "selected_target", return_value=TARGET), \
             mock.patch.object(batch, "paginated", side_effect=batch.ZoteroError("Local API unavailable")):
            result = batch.probe()
        self.assertEqual(result["connector_status"], 200)
        self.assertFalse(result["collection_resolvable"])
        self.assertIn("unavailable", result["target_warning"])

    def test_probe_connector_failure_returns_json_diagnostic(self):
        with mock.patch.object(batch, "request", side_effect=batch.ZoteroError("Local request failed")):
            result = batch.probe()
        self.assertFalse(result["connector_running"])
        self.assertIsNone(result["connector_status"])
        self.assertTrue(result["target_warning"])

    def test_missing_url_opt_in_requires_nonempty_extra(self):
        for extra in (None, "", "   ", 42):
            with self.subTest(extra=extra):
                context, patched = self.save_context()
                patched["load_metadata"].return_value = dict(METADATA, url="", extra=extra)
                with context, self.assertRaisesRegex(batch.ZoteroError, "extra provenance"):
                    batch.save(args("--allow-missing-url"))
                patched["request"].assert_not_called()

    def test_missing_url_opt_in_dry_run(self):
        context, patched = self.save_context()
        patched["load_metadata"].return_value = dict(METADATA, DOI="", url="", extra="CNKI; filename=EXAMPLE; 示例大学")
        with context:
            result = batch.save(args("--allow-missing-url", "--dry-run"))
        self.assertEqual(result["status"], "dry_run")
        patched["request"].assert_not_called()

    def test_missing_url_protocol_placeholder_never_enters_item_url(self):
        context, patched = self.save_context()
        metadata = dict(METADATA, DOI="", url="", extra="CNKI; filename=EXAMPLE; 示例大学")
        parent = dict(PARENT, data=dict(metadata, collections=["COLL0001"]))
        patched["load_metadata"].return_value = metadata
        patched["observe_created"].return_value = parent
        patched["get_json"].return_value = parent
        with context:
            result = batch.save(args("--allow-missing-url"))
        self.assertEqual(result["status"], "saved_complete")
        payload = json.loads(patched["request"].call_args_list[0].kwargs["body"])
        attachment = json.loads(patched["request"].call_args_list[1].kwargs["headers"]["X-Metadata"])
        self.assertEqual(payload["uri"], batch.LOCAL_IMPORT_URI)
        self.assertEqual(attachment["url"], batch.LOCAL_IMPORT_URI)
        self.assertEqual(payload["items"][0]["url"], "")
        self.assertEqual(payload["items"][0]["attachments"], [])

    def test_missing_url_flag_never_bypasses_signed_metadata_url(self):
        arguments = args("--allow-missing-url")
        arguments.metadata_json = json.dumps(dict(METADATA, url="https://example.org/article?v=SECRET", extra="CNKI; example"))
        with self.assertRaisesRegex(batch.ZoteroError, "signed/authenticated"):
            batch.save(arguments)

    def test_missing_url_flag_never_bypasses_signed_cli_url(self):
        context, patched = self.save_context()
        patched["load_metadata"].return_value = dict(METADATA, url="", extra="CNKI; example")
        with context, self.assertRaisesRegex(batch.ZoteroError, "signed/authenticated"):
            batch.save(args("--allow-missing-url", "--source-url", "https://example.org/a?token=SECRET"))
        patched["request"].assert_not_called()

    def test_pdf_identity_uses_full_text_beyond_preview(self):
        text = "intro " * 150 + "10.1234/example"
        info = dict(PDF, first_pages_text=text, first_page_text=text[:500], has_extractable_text=True)
        result = batch.check_pdf_identity(info, METADATA)
        self.assertTrue(result["pdf_text_matched"])
        self.assertTrue(result["doi_text_matched"])
        self.assertEqual(len(result["first_page_snippet"]), 500)
        self.assertNotIn("10.1234/example", result["first_page_snippet"])
        self.assertFalse(result["text_match_is_identity_proof"])

    def test_pdf_identity_doi_prefix_is_not_exact_match(self):
        info = dict(PDF, first_pages_text="10.1234/example-longer", first_page_text="10.1234/example-longer")
        self.assertFalse(batch.check_pdf_identity(info, METADATA)["doi_text_matched"])
        info["first_pages_text"] = "https://doi.org/10.1234/example"
        self.assertTrue(batch.check_pdf_identity(info, METADATA)["doi_text_matched"])

    def test_pdf_title_match_and_missing_text_are_advisory(self):
        info = dict(PDF, first_pages_text="Header 光\u200b电姿态检测测试 other", first_page_text="Header 光电姿态检测测试 other")
        self.assertTrue(batch.check_pdf_identity(info, METADATA)["title_text_matched"])
        self.assertFalse(batch.check_pdf_identity(PDF, METADATA)["pdf_text_matched"])
        arguments = args()
        arguments.pdf_identity_confirmed = False
        with mock.patch.object(batch, "validate_pdf", return_value=info), self.assertRaises(batch.ZoteroError):
            batch.save(arguments)

    def test_inspect_pdf_summary_excludes_bytes_and_full_text(self):
        info = dict(PDF, first_pages_text="x " * 300 + METADATA["title"], first_page_text="x " * 250,
                    has_extractable_text=True, text_extraction_failures=0)
        with mock.patch.object(batch, "validate_pdf", return_value=info):
            result = batch.inspect_pdf("synthetic.pdf", title=METADATA["title"])
        self.assertEqual(result["pdf_pages"], 1)
        self.assertEqual(result["pdf_size"], len(PDF_BYTES))
        self.assertEqual(result["sha256"], PDF["sha256"])
        self.assertTrue(result["pdf_text_matched"])
        self.assertTrue(result["identity_confirmation_required"])
        self.assertNotIn("bytes", result)
        self.assertNotIn("first_pages_text", result)

    def test_inspect_pdf_cli_parser_and_output(self):
        parsed = batch.build_parser().parse_args(["inspect-pdf", "--pdf", "synthetic.pdf", "--doi", METADATA["DOI"]])
        self.assertEqual(parsed.command, "inspect-pdf")
        output = io.StringIO()
        expected = {"pdf_text_matched": True, "pdf_pages": 1}
        with mock.patch.object(batch.sys, "argv", ["batch", "inspect-pdf", "--pdf", "synthetic.pdf"]), \
             mock.patch.object(batch.sys, "stdout", output), \
             mock.patch.object(batch, "inspect_pdf", return_value=expected):
            batch.main()
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_pdf_text_extraction_partial_failure_keeps_parsing(self):
        from pypdf import PdfWriter
        from pypdf._page import PageObject
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.pdf"
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with path.open("wb") as stream:
                writer.write(stream)
            with mock.patch.object(PageObject, "extract_text", side_effect=[ValueError("font problem"), "  Second\n page text  "]) as extract:
                info = batch.validate_pdf(path)
        self.assertEqual(info["pages"], 3)
        self.assertEqual(info["first_pages_text"], "Second page text")
        self.assertTrue(info["has_extractable_text"])
        self.assertEqual(info["text_extraction_failures"], 1)
        self.assertEqual(extract.call_count, 2)

    def test_dry_run_and_save_report_pdf_text_assistance(self):
        for flags in (("--dry-run",), ()):
            with self.subTest(flags=flags):
                context, patched = self.save_context()
                patched["validate_pdf"].return_value = dict(PDF, first_pages_text=METADATA["title"],
                    first_page_text=METADATA["title"], has_extractable_text=True)
                with context:
                    result = batch.save(args(*flags))
                self.assertTrue(result["pdf_text_matched"])
                self.assertEqual(result["first_page_snippet"], METADATA["title"])

    def test_truncated_positive_search_requires_duplicate_review(self):
        context, patched = self.save_context()
        patched["find_matches"].return_value = batch.MatchResults([PARENT], coverage={
            "complete": False, "truncated_sources": ["canonical_query_1"]})
        with context, mock.patch.object(batch, "verify_parent") as verify:
            result = batch.save(args())
        self.assertEqual(result["status"], "needs_duplicate_review")
        self.assertEqual(result["candidate_item_keys"], [PARENT["key"]])
        verify.assert_not_called()
        patched["request"].assert_not_called()

    def test_truncated_positive_search_explicit_review_can_reuse_candidate(self):
        context, patched = self.save_context()
        patched["find_matches"].return_value = batch.MatchResults([PARENT], coverage={
            "complete": False, "truncated_sources": ["canonical_query_1"]})
        with context:
            result = batch.save(args("--duplicate-search-reviewed"))
        self.assertEqual(result["status"], "duplicate_complete")
        self.assertTrue(result["duplicate_search_reviewed"])
        patched["request"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
