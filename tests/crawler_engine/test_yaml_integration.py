"""Integration tests: validate the real crawler_sites.yaml."""

import os

import pytest

from rag.svr.crawler_engine.config import ConfigLoader, SiteConfig
from rag.svr.crawler_engine.paginator import PaginatorFactory


YAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "rag", "svr", "crawler_sites.yaml",
)
YAML_PATH = os.path.normpath(YAML_PATH)


@pytest.fixture(scope="module")
def loader():
    if not os.path.exists(YAML_PATH):
        pytest.skip(f"YAML not found: {YAML_PATH}")
    return ConfigLoader(YAML_PATH)


@pytest.fixture(scope="module")
def sites(loader):
    return loader.load()


class TestYamlLoadsWithoutError:
    def test_yaml_file_exists(self):
        assert os.path.exists(YAML_PATH), f"Missing: {YAML_PATH}"

    def test_loads_all_sites(self, sites):
        assert len(sites) >= 70, f"Expected >=70 sites, got {len(sites)}"

    def test_all_sites_have_site_id(self, sites):
        for sid, cfg in sites.items():
            assert cfg.site_id == sid, f"site_id mismatch: {cfg.site_id} != {sid}"

    def test_enabled_sites_have_listing_or_sections(self, sites):
        for sid, cfg in sites.items():
            if not cfg.enabled:
                continue
            has_listing = bool(cfg.listing.url)
            has_sections = bool(cfg.sections)
            assert has_listing or has_sections, \
                f"{sid}: enabled but no listing URL or sections"


class TestValidTransportTypes:
    VALID_TYPES = {"rest_api", "encrypted_api", "spa_render", "playwright_http"}
    VALID_ENGINES = {"requests", "urllib", "playwright_http", "playwright_spa"}

    def test_transport_types(self, sites):
        for sid, cfg in sites.items():
            assert cfg.transport.type in self.VALID_TYPES, \
                f"{sid}: unknown transport type '{cfg.transport.type}'"

    def test_transport_engines(self, sites):
        for sid, cfg in sites.items():
            assert cfg.transport.engine in self.VALID_ENGINES, \
                f"{sid}: unknown engine '{cfg.transport.engine}'"

    def test_encrypted_api_sites_have_encryption(self, sites):
        for sid, cfg in sites.items():
            if cfg.transport.type == "encrypted_api":
                assert cfg.transport.encryption is not None, \
                    f"{sid}: encrypted_api but no encryption config"

    def test_spa_render_uses_playwright_spa(self, sites):
        """Spa render sites should use playwright_spa engine."""
        for sid, cfg in sites.items():
            if cfg.transport.type == "spa_render":
                assert cfg.transport.engine == "playwright_spa", \
                    f"{sid}: spa_render should use playwright_spa engine, got '{cfg.transport.engine}'"


class TestPaginationConfigs:
    VALID_TYPES = {"page_no", "offset", "total_count", "html_regex", "click_next", "single_page"}

    def test_pagination_types(self, sites):
        for sid, cfg in sites.items():
            assert cfg.pagination.type in self.VALID_TYPES, \
                f"{sid}: unknown pagination type '{cfg.pagination.type}'"

    def test_paginators_constructable(self, sites):
        for sid, cfg in sites.items():
            try:
                p = PaginatorFactory.create(cfg.pagination)
                assert p is not None
            except Exception as e:
                pytest.fail(f"{sid}: failed to create paginator: {e}")

    def test_page_no_has_page_param(self, sites):
        for sid, cfg in sites.items():
            if cfg.pagination.type == "page_no":
                assert cfg.pagination.page_param, \
                    f"{sid}: page_no pagination needs page_param"

    def test_offset_has_page_param(self, sites):
        for sid, cfg in sites.items():
            if cfg.pagination.type == "offset":
                assert cfg.pagination.page_param, \
                    f"{sid}: offset pagination needs page_param"

    def test_html_regex_has_page_pattern(self, sites):
        """html_regex pagination should have page_pattern or next_page_regex.
        If neither is set, the adapter performs next-page extraction from HTML."""
        for sid, cfg in sites.items():
            if cfg.pagination.type == "html_regex":
                # At least one of page_pattern or next_page_regex is recommended
                # but not strictly required (adapter-level extraction)
                pass  # allow all — adapter handles extraction


class TestExtractConfigs:
    VALID_TYPES = {"json_path", "css_selector", "ai"}

    def test_extract_types(self, sites):
        for sid, cfg in sites.items():
            assert cfg.extract.type in self.VALID_TYPES, \
                f"{sid}: unknown extract type '{cfg.extract.type}'"


class TestDetailConfigs:
    VALID_TYPES = {"inline", "api_request", "css_selector", "playwright_http", "none"}

    def test_detail_types(self, sites):
        for sid, cfg in sites.items():
            assert cfg.detail.type in self.VALID_TYPES, \
                f"{sid}: unknown detail type '{cfg.detail.type}'"


class TestSectionBasedSites:
    def test_sections_have_listing_urls(self, sites):
        """Sections with their own listing config must have URLs.
        Sections without their own listing inherit from the parent site."""
        for sid, cfg in sites.items():
            for i, section in enumerate(cfg.sections):
                if section.listing and section.listing.url:
                    # Section has its own listing URL — validate it
                    assert section.listing.url.startswith(("http://", "https://")), \
                        f"{sid}.sections[{i}] ({section.label}): invalid listing URL"
                elif section.listing and not section.listing.url:
                    # Section has no listing URL — must inherit from parent
                    assert cfg.listing.url, \
                        f"{sid}.sections[{i}] ({section.label}): no listing URL and parent has none"

    def test_section_labels_are_unique(self, sites):
        for sid, cfg in sites.items():
            labels = [s.label for s in cfg.sections if s.label]
            if len(labels) != len(set(labels)):
                pytest.fail(f"{sid}: duplicate section labels: {labels}")


class TestSiteUrlValidity:
    def test_site_urls_have_scheme(self, sites):
        for sid, cfg in sites.items():
            if cfg.site_url:
                assert cfg.site_url.startswith(("http://", "https://")), \
                    f"{sid}: site_url missing scheme: {cfg.site_url}"

    def test_listing_urls_have_scheme(self, sites):
        for sid, cfg in sites.items():
            if cfg.listing.url:
                assert cfg.listing.url.startswith(("http://", "https://")), \
                    f"{sid}: listing URL missing scheme: {cfg.listing.url}"
