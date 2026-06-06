"""Tests for crawler_engine.bid_writer — ID generation and BidWriter logic."""

import json
from unittest.mock import Mock, patch, MagicMock

from rag.svr.crawler_engine.bid_writer import (
    gen_bid_id,
    gen_file_id,
    BidWriter,
    _MAX_POSITIVE_INT63,
)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

class TestGenBidId:
    def test_deterministic(self):
        id1 = gen_bid_id("https://example.com/article/123")
        id2 = gen_bid_id("https://example.com/article/123")
        assert id1 == id2

    def test_different_urls(self):
        id1 = gen_bid_id("https://example.com/a")
        id2 = gen_bid_id("https://example.com/b")
        assert id1 != id2

    def test_positive(self):
        bid = gen_bid_id("https://example.com/test")
        assert bid > 0

    def test_within_bigint_range(self):
        bid = gen_bid_id("https://example.com/very/long/url/with/many/segments")
        assert bid <= _MAX_POSITIVE_INT63
        assert bid > 0

    def test_chinese_url(self):
        bid = gen_bid_id("https://example.com/招标公告/123")
        assert bid > 0

    def test_similar_strings(self):
        id1 = gen_bid_id("article-123")
        id2 = gen_bid_id("article-124")
        assert id1 != id2


class TestGenFileId:
    def test_deterministic(self):
        fid1 = gen_file_id("https://example.com/file.pdf", 12345, 0)
        fid2 = gen_file_id("https://example.com/file.pdf", 12345, 0)
        assert fid1 == fid2

    def test_different_index(self):
        fid1 = gen_file_id("https://example.com/file.pdf", 12345, 0)
        fid2 = gen_file_id("https://example.com/file.pdf", 12345, 1)
        assert fid1 != fid2

    def test_different_project(self):
        fid1 = gen_file_id("https://example.com/file.pdf", 111, 0)
        fid2 = gen_file_id("https://example.com/file.pdf", 222, 0)
        assert fid1 != fid2

    def test_positive(self):
        fid = gen_file_id("https://example.com/file.pdf", 12345, 0)
        assert fid > 0


# ---------------------------------------------------------------------------
# BidWriter._resolve_id
# ---------------------------------------------------------------------------

class TestBidWriterResolveId:
    def test_from_url_field(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({"url": "https://example.com/item/456"})
        assert bid is not None
        assert bid > 0

    def test_from_href_field(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({"href": "https://example.com/item/789"})
        assert bid is not None

    def test_from_explicit_url_arg(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({"title": "No URL"}, url="https://example.com/explicit")
        assert bid is not None

    def test_url_takes_priority_over_id(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({"url": "https://example.com/url", "id": 99999})
        # URL-based hash, not 99999
        assert bid != 99999

    def test_from_numeric_id(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({"id": 12345})
        assert bid == 12345

    def test_from_string_id(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({"id": "ABC-123"})
        assert bid is not None
        assert bid > 0

    def test_fallback_to_title_date(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({"title": "Some Title", "date": "2024-01-01"})
        assert bid is not None
        assert bid > 0

    def test_deterministic(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        item = {"url": "https://example.com/deterministic"}
        bid1 = writer._resolve_id(item)
        bid2 = writer._resolve_id(item)
        assert bid1 == bid2

    def test_no_identifiable_info(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        bid = writer._resolve_id({})
        assert bid is None


# ---------------------------------------------------------------------------
# BidWriter._extract_structure
# ---------------------------------------------------------------------------

class TestBidWriterExtractStructure:
    def test_project_name(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        fields = writer._extract_structure({"projectName": "测试项目"})
        assert fields["project_name"] == "测试项目"

    def test_project_numbers(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        fields = writer._extract_structure({"projectNumber": ["PN-001", "PN-002"]})
        numbers = json.loads(fields["project_numbers"])
        assert "PN-001" in numbers

    def test_budget_and_bid_money(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        fields = writer._extract_structure({
            "budgetMoney": ["100万"],
            "bidMoney": ["95万"],
        })
        assert "100万" in fields["budget_money"]
        assert "95万" in fields["bid_money"]

    def test_collect_url(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        fields = writer._extract_structure({"url": "https://example.com/item"})
        assert fields["collect_url"] == "https://example.com/item"

    def test_empty_item(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        fields = writer._extract_structure({})
        assert fields == {}


# ---------------------------------------------------------------------------
# BidWriter.write_project (with mocked service)
# ---------------------------------------------------------------------------

class TestBidWriterWriteProject:
    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_write_project_new(self, MockService):
        MockService.upsert_project.return_value = (True, MagicMock())
        writer = BidWriter(kb_id="kb1", tenant_id="t1")

        ok = writer.write_project(12345, {
            "title": "测试招标",
            "date": "2024-06-15",
            "url": "https://example.com/item",
        }, site_id="test_site")

        assert ok is True
        assert writer.stats["projects_new"] == 1
        assert writer.stats["projects_failed"] == 0

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_write_project_update(self, MockService):
        MockService.upsert_project.return_value = (False, MagicMock())
        writer = BidWriter(kb_id="kb1", tenant_id="t1")

        ok = writer.write_project(12345, {
            "title": "Updated Title",
            "url": "https://example.com/item",
        }, site_id="test_site")

        assert ok is True
        assert writer.stats["projects_updated"] == 1

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_write_project_failure(self, MockService):
        MockService.upsert_project.side_effect = Exception("DB error")
        writer = BidWriter(kb_id="kb1", tenant_id="t1")

        ok = writer.write_project(12345, {
            "title": "Test",
        }, site_id="test_site")

        assert ok is False
        assert writer.stats["projects_failed"] == 1

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_raw_json_contains_site_id(self, MockService):
        MockService.upsert_project.return_value = (True, MagicMock())
        writer = BidWriter(kb_id="kb1", tenant_id="t1")

        writer.write_project(12345, {
            "title": "Test",
            "url": "https://example.com/item",
        }, site_id="my_site")

        call_args = MockService.upsert_project.call_args[0][0]
        raw_json = json.loads(call_args["raw_json"])
        assert raw_json["crawler_site_id"] == "my_site"
        assert raw_json["crawler_url"] == "https://example.com/item"
        assert call_args["source_type"] == "crawler"


# ---------------------------------------------------------------------------
# BidWriter write_all integration (mocked)
# ---------------------------------------------------------------------------

class TestBidWriterWriteAll:
    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    @patch("rag.svr.crawler_engine.bid_writer.BidProjectDetailService")
    @patch("rag.svr.crawler_engine.bid_writer.BidProjectStructureService")
    @patch("rag.svr.crawler_engine.bid_writer.BidProjectFileService")
    def test_write_all_success(self, MockFileSvc, MockStructSvc, MockDetailSvc, MockProjSvc):
        MockProjSvc.upsert_project.return_value = (True, MagicMock())

        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        project_id = writer.write_all({
            "title": "Full Item",
            "url": "https://example.com/item/1",
            "date": "2024-06-15",
            "content": "# Content\n\nBody text",
            "partAName": "采购人",
        }, site_id="test_site")

        assert project_id is not None
        assert writer.stats["projects_new"] == 1
        assert writer.stats["details_written"] == 1
        assert writer.stats["structures_written"] == 1

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_write_all_no_identifiable_id(self, MockService):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        project_id = writer.write_all({}, site_id="test_site")

        assert project_id is None
        assert writer.stats["projects_failed"] == 1
        # Service should not be called
        MockService.upsert_project.assert_not_called()

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_write_all_project_failure_stops(self, MockService):
        MockService.upsert_project.side_effect = Exception("DB error")
        writer = BidWriter(kb_id="kb1", tenant_id="t1")

        project_id = writer.write_all({
            "url": "https://example.com/item",
            "title": "Test",
        }, site_id="test_site")

        assert project_id is None

    def test_reset_stats(self):
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        writer._stats["projects_new"] = 10
        writer._stats["projects_failed"] = 3
        writer.reset_stats()
        assert writer.stats["projects_new"] == 0
        assert writer.stats["projects_failed"] == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestBidWriterEdgeCases:
    def test_id_collision_same_url(self):
        """Same URL should produce same ID — this is correct for upsert."""
        id1 = gen_bid_id("https://example.com/same-url")
        id2 = gen_bid_id("https://example.com/same-url")
        assert id1 == id2

    def test_long_url(self):
        url = "https://example.com/" + "a" * 500
        bid = gen_bid_id(url)
        assert bid > 0
        assert bid <= _MAX_POSITIVE_INT63

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_empty_content_item(self, MockService):
        MockService.upsert_project.return_value = (True, MagicMock())
        writer = BidWriter(kb_id="kb1", tenant_id="t1")

        # Should not crash with empty content
        project_id = writer.write_all({
            "url": "https://example.com/empty",
            "title": "Empty Content Item",
        }, site_id="test_site")

        assert project_id is not None

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_large_content(self, MockService):
        MockService.upsert_project.return_value = (True, MagicMock())
        writer = BidWriter(kb_id="kb1", tenant_id="t1")
        large_text = "A" * 100000  # 100KB

        project_id = writer.write_all({
            "url": "https://example.com/large",
            "title": "Large Item",
            "content": large_text,
        }, site_id="test_site")

        assert project_id is not None

    @patch("rag.svr.crawler_engine.bid_writer.BidProjectFileService")
    @patch("rag.svr.crawler_engine.bid_writer.BidProjectService")
    def test_attachments_without_url(self, MockProjSvc, MockFileSvc):
        MockProjSvc.upsert_project.return_value = (True, MagicMock())
        writer = BidWriter(kb_id="kb1", tenant_id="t1")

        project_id = writer.write_all({
            "url": "https://example.com/item",
            "title": "Item with Bad Attachments",
            "attachments": [
                {"name": "file.pdf"},  # no URL
                {"name": "doc.doc", "url": ""},  # empty URL
            ],
        }, site_id="test_site")

        assert project_id is not None
        # Files without URLs should be skipped
        written = writer.stats["files_written"]
        assert written == 0
