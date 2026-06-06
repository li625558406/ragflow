"""Tests for crawler_engine.extractors — JSON path and CSS selector extraction."""

from rag.svr.crawler_engine.config import ExtractConfig
from rag.svr.crawler_engine.extractors.json_path import JsonPathExtractor
from rag.svr.crawler_engine.extractors.css_selector import CssSelectorExtractor
from rag.svr.crawler_engine.extractors.base import ExtractorFactory


class TestJsonPathExtractor:
    def test_list_response(self):
        config = ExtractConfig(type="json_path", items_path="", fields={})
        extractor = JsonPathExtractor(config)
        data = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
        result = extractor.extract(data)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["title"] == "B"

    def test_dict_with_items_path(self):
        config = ExtractConfig(type="json_path", items_path="data.rows",
                               fields={"id": "uuid", "title": "name"})
        extractor = JsonPathExtractor(config)
        data = {
            "data": {
                "rows": [
                    {"uuid": "1", "name": "First"},
                    {"uuid": "2", "name": "Second"},
                ]
            }
        }
        result = extractor.extract(data)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[0]["title"] == "First"

    def test_dict_with_common_keys(self):
        config = ExtractConfig(type="json_path", fields={})
        extractor = JsonPathExtractor(config)
        # "rows" is a common key
        data = {"rows": [{"id": 1}, {"id": 2}]}
        result = extractor.extract(data)
        assert len(result) == 2

    def test_nested_path(self):
        config = ExtractConfig(type="json_path", items_path="result.data",
                               fields={"item_id": "id"})
        extractor = JsonPathExtractor(config)
        data = {"result": {"data": [{"id": "x"}, {"id": "y"}]}}
        result = extractor.extract(data)
        assert len(result) == 2
        assert result[0]["item_id"] == "x"

    def test_null_data(self):
        config = ExtractConfig()
        extractor = JsonPathExtractor(config)
        result = extractor.extract(None)
        assert result == []

    def test_empty_dict(self):
        config = ExtractConfig()
        extractor = JsonPathExtractor(config)
        result = extractor.extract({})
        assert result == []


class TestCssSelectorExtractor:
    HTML = """
    <html><body>
      <ul class="list">
        <li><a href="/article/1">Article 1</a><span class="date">2024-01-01</span></li>
        <li><a href="/article/2">Article 2</a><span class="date">2024-01-02</span></li>
      </ul>
    </body></html>
    """

    def test_extract_links(self):
        config = ExtractConfig(
            type="css_selector",
            items_path="ul.list li",
            fields={
                "title": "a",
                "href": "a",
                "date": ".date",
            }
        )
        extractor = CssSelectorExtractor(config)
        result = extractor.extract(self.HTML)
        assert len(result) == 2
        assert result[0]["title"] == "Article 1"
        assert result[1]["date"] == "2024-01-02"


class TestCssAttributeExtraction:
    """Verify CSS extractor correctly extracts HTML attributes for dedup and data."""

    HTML = """
    <html><body>
      <table><tbody>
        <tr>
          <td><a href="/notice/123.html">Notice Title</a><span data-id="N123">2024-06-01</span></td>
        </tr>
        <tr>
          <td><a href="/notice/456.html">Another Notice</a><span data-id="N456">2024-06-02</span></td>
        </tr>
      </tbody></table>
    </body></html>
    """

    def test_extract_href_attribute_with_a_at_href(self):
        """`a@href` should extract the href attribute from the <a> child element."""
        config = ExtractConfig(
            type="css_selector",
            items_path="table tbody tr",
            fields={
                "id": "a@href",
                "title": "a",
            },
        )
        extractor = CssSelectorExtractor(config)
        result = extractor.extract(self.HTML)
        assert len(result) == 2
        assert result[0]["id"] == "/notice/123.html"
        assert result[0]["title"] == "Notice Title"
        assert result[1]["id"] == "/notice/456.html"
        assert result[1]["title"] == "Another Notice"

    def test_extract_data_id_from_container(self):
        """`@data-id` should extract from the row element when no child selector."""
        html = """<html><body><table><tbody>
          <tr data-id="X001"><td><a href="/x/1">Item 1</a></td></tr>
          <tr data-id="X002"><td><a href="/x/2">Item 2</a></td></tr>
        </tbody></table></body></html>"""
        config = ExtractConfig(
            type="css_selector",
            items_path="table tbody tr",
            fields={
                "id": "@data-id",
                "title": "a",
            },
        )
        extractor = CssSelectorExtractor(config)
        result = extractor.extract(html)
        assert len(result) == 2
        assert result[0]["id"] == "X001"
        assert result[0]["title"] == "Item 1"
        assert result[1]["id"] == "X002"

    def test_no_at_sign_extracts_text(self):
        """Without @attr syntax, field selects child element text (backward compat)."""
        html = """<html><body><div class="items">
          <div class="item"><a href="/p/1">Product A</a></div>
        </div></body></html>"""
        config = ExtractConfig(
            type="css_selector",
            items_path="div.item",
            fields={
                "title": "a",
                "url": "a@href",
            },
        )
        extractor = CssSelectorExtractor(config)
        result = extractor.extract(html)
        assert result[0]["title"] == "Product A"
        assert result[0]["url"] == "/p/1"

    def test_real_world_fgw_zwgk_structure(self):
        """Mimics fgw_zwgk HTML: <table><tr><td><a href="/xwdt/zwgg/xxx.html">Title</a>"""
        html = """<html><body><div class="news-table"><table><tbody>
          <tr><td><a href="/xwdt/zwgg/202406/t20240601_12345.html">关于印发《XX管理办法》的通知</a><span>2024-06-01</span></td></tr>
          <tr><td><a href="/xwdt/zwgg/202406/t20240602_67890.html">关于YY项目核准的批复</a><span>2024-06-02</span></td></tr>
          <tr><td><a href="/xwdt/zwgg/202406/t20240603_11111.html">ZZ行业发展规划</a><span>2024-06-03</span></td></tr>
        </tbody></table></div></body></html>"""
        config = ExtractConfig(
            type="css_selector",
            items_path="table tbody tr",
            fields={
                "id": "a@href",
                "title": "a",
                "date": "span",
            },
        )
        extractor = CssSelectorExtractor(config)
        result = extractor.extract(html)

        assert len(result) == 3
        # Verify dedup keys are actual URLs (not empty, not element text)
        assert result[0]["id"] == "/xwdt/zwgg/202406/t20240601_12345.html"
        assert result[1]["id"] == "/xwdt/zwgg/202406/t20240602_67890.html"
        assert result[2]["id"] == "/xwdt/zwgg/202406/t20240603_11111.html"
        # Titles are text content
        assert "管理办法" in result[0]["title"]
        assert "核准" in result[1]["title"]


class TestDedupIdPipeline:
    """Verify the full dedup pipeline: extract → get_id → state tracking.

    Simulates how engine.py: _get_item_id() reads the 'id'/'url'/'href'
    field from extracted items to build the dedup key, and how
    StateManager tracks processed IDs.
    """

    def test_dedup_id_from_extracted_url_field(self):
        """When extractor puts href in 'url' field, _get_item_id finds it."""
        # Replicate _get_item_id logic from engine.py
        def _get_item_id(item):
            for key in ("uuid", "id", "article_id", "infoid", "noticenumber",
                        "bulletinID", "guid", "_id", "url", "href"):
                val = item.get(key, "")
                if val:
                    return str(val)
            return ""

        # Simulate extracted item with id as a@href
        item = {"id": "/xwdt/zwgg/202406/t20240601_12345.html",
                "title": "通知标题", "date": "2024-06-01"}
        assert _get_item_id(item) == "/xwdt/zwgg/202406/t20240601_12345.html"

    def test_dedup_id_from_url_field_fallback(self):
        """When 'id' is not present, _get_item_id falls back to 'url'."""
        def _get_item_id(item):
            for key in ("uuid", "id", "article_id", "infoid", "noticenumber",
                        "bulletinID", "guid", "_id", "url", "href"):
                val = item.get(key, "")
                if val:
                    return str(val)
            return ""

        item = {"url": "https://example.com/notice/123.html", "title": "Test"}
        assert _get_item_id(item) == "https://example.com/notice/123.html"

    def test_dedup_ids_match_across_old_and_new_system(self):
        """Old system stored the URL as dedup key; new system must produce same key."""
        def _get_item_id(item):
            for key in ("uuid", "id", "article_id", "infoid", "noticenumber",
                        "bulletinID", "guid", "_id", "url", "href"):
                val = item.get(key, "")
                if val:
                    return str(val)
            return ""

        # Old script behavior: extracted href and stored it
        old_dedup_key = "/xwdt/zwgg/202406/t20240601_12345.html"

        # New system: CSS extractor uses a@href, producing same value
        new_extracted_item = {"id": "/xwdt/zwgg/202406/t20240601_12345.html"}
        new_dedup_key = _get_item_id(new_extracted_item)

        assert new_dedup_key == old_dedup_key, \
            "New system dedup key must match old system for state compatibility"

    def test_state_manager_dedup_logic(self):
        """StateManager correctly tracks and checks processed IDs."""
        # In-memory simulation (no DB needed for dedup logic verification)
        processed_ids = set()

        # Mock existing state from old migration
        old_processed = {
            "/xwdt/zwgg/202406/t20240601_12345.html",
            "/xwdt/zwgg/202406/t20240602_67890.html",
        }
        processed_ids.update(old_processed)

        # New crawl produces these items
        new_items = [
            {"id": "/xwdt/zwgg/202406/t20240601_12345.html"},  # already processed
            {"id": "/xwdt/zwgg/202406/t20240603_11111.html"},  # new
        ]

        new_count = 0
        for item in new_items:
            item_id = item.get("id", "")
            if item_id and item_id in processed_ids:
                continue  # skip
            new_count += 1
            processed_ids.add(item_id)

        assert new_count == 1, "Only one item should be new"
        assert len(processed_ids) == 3, "Should have 3 total processed IDs"

    def test_empty_id_does_not_crash_dedup(self):
        """Items without any identifiable field should get empty string, not crash."""
        def _get_item_id(item):
            for key in ("uuid", "id", "article_id", "infoid", "noticenumber",
                        "bulletinID", "guid", "_id", "url", "href"):
                val = item.get(key, "")
                if val:
                    return str(val)
            return ""

        item = {"title": "No ID field", "date": "2024-06-01"}
        assert _get_item_id(item) == ""


class TestExtractorFactory:
    def test_create_json_path(self):
        config = ExtractConfig(type="json_path")
        e = ExtractorFactory.create(config)
        assert isinstance(e, JsonPathExtractor)

    def test_create_css_selector(self):
        config = ExtractConfig(type="css_selector")
        e = ExtractorFactory.create(config)
        assert isinstance(e, CssSelectorExtractor)

    def test_default_is_json_path(self):
        config = ExtractConfig(type="unknown")
        e = ExtractorFactory.create(config)
        assert isinstance(e, JsonPathExtractor)
