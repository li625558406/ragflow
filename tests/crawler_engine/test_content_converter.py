"""Tests for crawler_engine.content_converter — markdown→HTML and field extraction."""

import json
from datetime import datetime

from rag.svr.crawler_engine.content_converter import (
    markdown_to_html,
    parse_date,
    extract_project_fields,
    extract_detail_fields,
    extract_file_attachments,
)


# ---------------------------------------------------------------------------
# markdown_to_html
# ---------------------------------------------------------------------------

class TestMarkdownToHtml:
    def test_empty(self):
        assert markdown_to_html("") == ""
        assert markdown_to_html(None) == ""

    def test_heading(self):
        html = markdown_to_html("# Title")
        assert "<h1>Title</h1>" in html

        html = markdown_to_html("## Section")
        assert "<h2>Section</h2>" in html

    def test_bold_and_italic(self):
        html = markdown_to_html("This is **bold** and *italic*")
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html

    def test_table(self):
        md = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
        html = markdown_to_html(md)
        assert "<table>" in html
        assert "<tbody>" in html
        assert "Alice" in html
        assert "Bob" in html

    def test_unordered_list(self):
        md = "- Item 1\n- Item 2\n- Item 3"
        html = markdown_to_html(md)
        assert "<ul>" in html
        assert "<li>Item 1</li>" in html
        assert "</ul>" in html

    def test_ordered_list(self):
        md = "1. First\n2. Second\n3. Third"
        html = markdown_to_html(md)
        assert "<ol>" in html
        assert "<li>First</li>" in html

    def test_horizontal_rule(self):
        md = "Above\n---\nBelow"
        html = markdown_to_html(md)
        assert "<hr>" in html

    def test_paragraph(self):
        md = "This is a paragraph."
        html = markdown_to_html(md)
        assert "<p>This is a paragraph.</p>" in html

    def test_link(self):
        md = "Click [here](https://example.com)"
        html = markdown_to_html(md)
        assert '<a href="https://example.com">here</a>' in html

    def test_html_escaping(self):
        md = "Use <script> tag"
        html = markdown_to_html(md)
        assert "&lt;script&gt;" in html
        assert "<script>" not in html

    def test_mixed_content(self):
        md = "# Title\n\nParagraph text with **bold**.\n\n- List item 1\n- List item 2\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        html = markdown_to_html(md)
        assert "<h1>Title</h1>" in html
        assert "<strong>bold</strong>" in html
        assert "<li>List item 1</li>" in html
        assert "<table>" in html

    def test_chinese_content(self):
        md = "# 招标公告\n\n项目名称：**测试项目**\n\n- 采购人：某某单位"
        html = markdown_to_html(md)
        assert "<h1>招标公告</h1>" in html
        assert "<strong>测试项目</strong>" in html
        assert "<li>采购人：某某单位</li>" in html


# ---------------------------------------------------------------------------
# parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_format(self):
        dt = parse_date("2024-06-15 10:30:00")
        assert dt == datetime(2024, 6, 15, 10, 30, 0)

    def test_date_only(self):
        dt = parse_date("2024-06-15")
        assert dt == datetime(2024, 6, 15)

    def test_chinese_format(self):
        dt = parse_date("2024年06月15日")
        assert dt == datetime(2024, 6, 15)

    def test_slash_format(self):
        dt = parse_date("2024/06/15")
        assert dt == datetime(2024, 6, 15)

    def test_none_and_empty(self):
        assert parse_date(None) is None
        assert parse_date("") is None

    def test_datetime_passthrough(self):
        dt = datetime(2024, 1, 1)
        assert parse_date(dt) == dt

    def test_extract_from_text(self):
        dt = parse_date("发布日期: 2024-06-15 其他信息")
        assert dt == datetime(2024, 6, 15)


# ---------------------------------------------------------------------------
# extract_project_fields
# ---------------------------------------------------------------------------

class TestExtractProjectFields:
    def test_basic_fields(self):
        item = {
            "title": "招标公告标题",
            "date": "2024-06-15",
            "content": "公告内容摘要",
        }
        fields = extract_project_fields(item)
        assert fields["title"] == "招标公告标题"
        assert fields["source_type"] == "crawler"

    def test_date_parsing(self):
        item = {"title": "Test", "publishDate": "2024-01-15"}
        fields = extract_project_fields(item)
        assert fields["publish_time"] == datetime(2024, 1, 15)

    def test_json_fields(self):
        item = {
            "title": "Test",
            "industryCodeList": ["G544", "E481"],
            "partANameList": ["某某单位"],
        }
        fields = extract_project_fields(item)
        industry = json.loads(fields["industry_codes"])
        assert "G544" in industry
        part_a = json.loads(fields["part_a_names"])
        assert "某某单位" in part_a

    def test_has_file(self):
        item = {"title": "Test", "hasFile": 1}
        fields = extract_project_fields(item)
        assert fields["has_file"] == 1

    def test_default_title(self):
        item = {"content": "Some content without title"}
        fields = extract_project_fields(item)
        assert fields["title"] == "Untitled"

    def test_source_type_always_crawler(self):
        fields = extract_project_fields({})
        assert fields["source_type"] == "crawler"

    def test_empty_item(self):
        fields = extract_project_fields({})
        assert fields["title"] == "Untitled"
        assert fields["source_type"] == "crawler"
        assert fields["content"] == ""

    def test_first_match_wins(self):
        item = {"date": "2024-01-01", "publishDate": "2024-06-15"}
        fields = extract_project_fields(item)
        # "date" comes before "publishDate" in the field map
        assert fields["publish_time"] == datetime(2024, 1, 1)

    def test_provice_city_county(self):
        item = {
            "title": "Test",
            "proviceCode": "110000",
            "cityCode": "110100",
            "countyCode": "110101",
        }
        fields = extract_project_fields(item)
        assert fields["provice_code"] == "110000"
        assert fields["city_code"] == "110100"
        assert fields["county_code"] == "110101"

    def test_project_money(self):
        item = {"title": "Test", "projectMoney": "500万元"}
        fields = extract_project_fields(item)
        assert fields["project_money"] == "500万元"


# ---------------------------------------------------------------------------
# extract_detail_fields
# ---------------------------------------------------------------------------

class TestExtractDetailFields:
    def test_basic_detail(self):
        item = {
            "content": "# 招标公告\n\n正文内容",
            "partAName": "某某单位",
            "agentName": "代理机构",
        }
        fields = extract_detail_fields(item)
        assert "<h1>招标公告</h1>" in fields["content_html"]
        assert fields["part_a_name"] == "某某单位"
        assert fields["agent_name"] == "代理机构"
        assert fields["fetched_at"] is not None

    def test_html_content_passthrough(self):
        item = {"content": "<div><p>已有HTML</p></div>"}
        fields = extract_detail_fields(item)
        assert "<div><p>已有HTML</p></div>" in fields["content_html"]

    def test_empty_content(self):
        fields = extract_detail_fields({})
        assert fields.get("content_html", "") == ""


# ---------------------------------------------------------------------------
# extract_file_attachments
# ---------------------------------------------------------------------------

class TestExtractFileAttachments:
    def test_attachments_list(self):
        item = {
            "attachments": [
                {"name": "招标文件.pdf", "url": "https://example.com/file.pdf", "size": 1024},
            ]
        }
        files = extract_file_attachments(item)
        assert len(files) == 1
        assert files[0]["file_name"] == "招标文件.pdf"
        assert files[0]["file_url"] == "https://example.com/file.pdf"
        assert files[0]["file_size"] == 1024

    def test_files_list(self):
        item = {
            "files": [
                {"fileName": "附件.doc", "fileUrl": "https://example.com/doc"},
            ]
        }
        files = extract_file_attachments(item)
        assert len(files) == 1
        assert files[0]["file_name"] == "附件.doc"
        assert files[0]["file_url"] == "https://example.com/doc"

    def test_single_file_dict(self):
        item = {"file": {"name": "公告.pdf", "url": "https://example.com/pdf"}}
        files = extract_file_attachments(item)
        assert len(files) == 1
        assert files[0]["file_name"] == "公告.pdf"

    def test_file_url_at_item_level(self):
        item = {"file_url": "https://example.com/download", "file_name": "download.pdf"}
        files = extract_file_attachments(item)
        assert len(files) == 1
        assert files[0]["file_url"] == "https://example.com/download"

    def test_no_files(self):
        files = extract_file_attachments({"title": "No files"})
        assert files == []

    def test_empty_list(self):
        files = extract_file_attachments({"attachments": []})
        assert files == []
