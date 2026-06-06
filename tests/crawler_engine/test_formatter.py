"""Tests for crawler_engine.formatter — Markdown formatting."""

from rag.svr.crawler_engine.formatter import MarkdownFormatter


class TestMarkdownFormatter:
    def test_default_format(self):
        fmt = MarkdownFormatter()
        item = {
            "title": "Test Article",
            "date": "2024-06-15",
            "content": "This is the article body.",
            "author": "Admin",
        }
        md = fmt.format_item(item)
        assert "# Test Article" in md
        assert "**日期:** 2024-06-15" in md
        assert "author" in md
        assert "Admin" in md
        assert "This is the article body." in md

    def test_template_format(self):
        template = "## {{ title }}\n\n{{ content }}\n\n*{{ date }}*"
        fmt = MarkdownFormatter(template=template)
        item = {"title": "T", "content": "C", "date": "D"}
        md = fmt.format_item(item)
        assert "## T" in md
        assert "C" in md
        assert "*D*" in md

    def test_missing_title(self):
        fmt = MarkdownFormatter()
        item = {"content": "Some content"}
        md = fmt.format_item(item)
        assert "Untitled" in md

    def test_format_batch(self):
        fmt = MarkdownFormatter(template="# {{ title }}")
        items = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
        result = fmt.format_batch(items, separator="---")
        assert "# A" in result
        assert "# B" in result
        assert "# C" in result
        assert "---" in result

    def test_pipe_escaping(self):
        fmt = MarkdownFormatter()
        item = {"title": "Test", "field": "value|with|pipes"}
        md = fmt.format_item(item)
        assert "value\\|with\\|pipes" in md

    def test_empty_batch(self):
        fmt = MarkdownFormatter()
        result = fmt.format_batch([])
        assert result == ""

    def test_custom_title_field(self):
        fmt = MarkdownFormatter(title_field="name")
        item = {"name": "Article Name", "date": "2024-01-01"}
        md = fmt.format_item(item)
        assert "# Article Name" in md
