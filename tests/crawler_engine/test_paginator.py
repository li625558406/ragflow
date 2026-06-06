"""Tests for crawler_engine.paginator — pagination strategies."""

from rag.svr.crawler_engine.config import PaginationConfig
from rag.svr.crawler_engine.paginator import (
    PageNoPaginator,
    OffsetPaginator,
    TotalCountPaginator,
    SinglePagePaginator,
    HtmlRegexPaginator,
    ClickNextPaginator,
    PaginatorFactory,
)


class TestPageNoPaginator:
    def test_basic_pagination(self):
        config = PaginationConfig(
            type="page_no", page_param="page", page_size_param="rows",
            page_size=20, start=1, total_field="total", max_pages=1,
        )
        paginator = PageNoPaginator(config)
        pages = list(paginator.pages())
        assert len(pages) == 1
        first = pages[0]
        assert first["page"] == 1
        assert first["rows"] == 20

    def test_max_pages(self):
        config = PaginationConfig(
            type="page_no", page_param="page", page_size_param="rows",
            page_size=20, start=1, max_pages=3,
        )
        paginator = PageNoPaginator(config)
        pages = list(paginator.pages())
        assert len(pages) == 3
        assert pages[0]["page"] == 1
        assert pages[1]["page"] == 2
        assert pages[2]["page"] == 3

    def test_update_total(self):
        config = PaginationConfig(
            type="page_no", total_field="total",
        )
        paginator = PageNoPaginator(config)
        total = paginator.update_total({"total": 100, "rows": []})
        assert total == 100

    def test_start_at(self):
        config = PaginationConfig(
            type="page_no", page_param="page", page_size_param="rows",
            page_size=20, start=1, max_pages=2,
        )
        paginator = PageNoPaginator(config)
        pages = list(paginator.pages(start_at=5))
        assert pages[0]["page"] == 5
        assert pages[1]["page"] == 6


class TestOffsetPaginator:
    def test_basic_offset(self):
        config = PaginationConfig(
            type="offset", page_param="start", page_size_param="limit",
            page_size=20, max_pages=3,
        )
        paginator = OffsetPaginator(config)
        pages = list(paginator.pages())
        assert len(pages) == 3
        assert pages[0]["start"] == 0
        assert pages[1]["start"] == 20
        assert pages[2]["start"] == 40


class TestTotalCountPaginator:
    def test_total_from_response(self):
        config = PaginationConfig(
            type="total_count", page_param="page", page_size_param="rows",
            page_size=20, total_field="total",
        )
        paginator = TotalCountPaginator(config)
        pages = list(paginator.pages())
        # First call gives one page
        assert len(pages) == 1
        assert pages[0]["page"] == 0

        # After update_total, pages should be calculable
        total = paginator.update_total({"total": 55})
        assert total == 55


class TestSinglePagePaginator:
    def test_single_page(self):
        config = PaginationConfig(type="single_page")
        paginator = SinglePagePaginator(config)
        pages = list(paginator.pages())
        assert len(pages) == 1
        assert pages[0] == {}

    def test_total_is_one(self):
        config = PaginationConfig(type="single_page")
        paginator = SinglePagePaginator(config)
        total = paginator.update_total({})
        assert total == 1


class TestPaginatorFactory:
    def test_create_page_no(self):
        config = PaginationConfig(type="page_no")
        p = PaginatorFactory.create(config)
        assert isinstance(p, PageNoPaginator)

    def test_create_offset(self):
        config = PaginationConfig(type="offset")
        p = PaginatorFactory.create(config)
        assert isinstance(p, OffsetPaginator)

    def test_create_total_count(self):
        config = PaginationConfig(type="total_count")
        p = PaginatorFactory.create(config)
        assert isinstance(p, TotalCountPaginator)

    def test_create_single_page(self):
        config = PaginationConfig(type="single_page")
        p = PaginatorFactory.create(config)
        assert isinstance(p, SinglePagePaginator)

    def test_create_html_regex(self):
        config = PaginationConfig(type="html_regex")
        p = PaginatorFactory.create(config)
        assert isinstance(p, HtmlRegexPaginator)

    def test_create_click_next(self):
        config = PaginationConfig(type="click_next")
        p = PaginatorFactory.create(config)
        assert isinstance(p, ClickNextPaginator)

    def test_unknown_type_defaults_to_page_no(self):
        config = PaginationConfig(type="unknown_type")
        p = PaginatorFactory.create(config)
        assert isinstance(p, PageNoPaginator)


class TestHtmlRegexPaginator:
    def test_generates_page_url_suffix(self):
        config = PaginationConfig(
            type="html_regex", page_param="page", page_size_param="rows",
            page_pattern="/zwgk/zcfg/index_{}.htm",
            page_size=10, start=1, max_pages=3,
        )
        paginator = HtmlRegexPaginator(config)
        pages = list(paginator.pages())
        assert len(pages) == 3
        assert pages[0]["_page_url_suffix"] == "/zwgk/zcfg/index_1.htm"
        assert pages[1]["_page_url_suffix"] == "/zwgk/zcfg/index_2.htm"
        assert pages[2]["_page_url_suffix"] == "/zwgk/zcfg/index_3.htm"

    def test_no_pattern(self):
        config = PaginationConfig(
            type="html_regex", page_pattern="",
            page_param="page", page_size=20, start=1, max_pages=2,
        )
        paginator = HtmlRegexPaginator(config)
        pages = list(paginator.pages())
        assert len(pages) == 2
        assert "_page_url_suffix" not in pages[0]


class TestClickNextPaginator:
    def test_generates_click_signals(self):
        config = PaginationConfig(type="click_next", start=1, max_pages=3)
        paginator = ClickNextPaginator(config)
        pages = list(paginator.pages())
        assert len(pages) == 3
        for p in pages:
            assert p["_click_next"] is True
        assert pages[0]["_page"] == 1
        assert pages[1]["_page"] == 2

    def test_update_total_from_list(self):
        config = PaginationConfig(type="click_next")
        paginator = ClickNextPaginator(config)
        total = paginator.update_total([{"a": 1}, {"a": 2}, {"a": 3}])
        assert total == 3
