"""
YAML configuration loader for the unified crawler framework.

Loads crawler_sites.yaml and provides SiteConfig dataclass instances
with validation and defaults.
"""

import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union

import yaml


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EncryptionConfig:
    """Encryption/decryption settings."""
    algorithm: str = ""            # sm4_ecb, aes_256_cbc
    key: str = ""                  # hex-encoded key
    iv: str = ""                   # hex-encoded IV (for CBC modes)
    key_encoding: str = "hex"      # hex | utf8
    field: str = ""                # which field to decrypt
    encoding: str = "utf-8"        # output encoding


@dataclass
class SigningConfig:
    """Request signing settings."""
    algorithm: str = "md5"         # md5
    secret: str = ""               # signing secret
    header_name: str = ""          # header name for the signature value
    fields_in_sign: List[str] = field(default_factory=list)  # fields to include


@dataclass
class CaptchaConfig:
    """CAPTCHA handling settings."""
    type: str = ""                 # ocr (ddddocr), manual, skip
    input_selector: str = ""       # CSS selector for CAPTCHA input
    image_selector: str = ""       # CSS selector for CAPTCHA image
    submit_selector: str = ""      # CSS selector for submit button


@dataclass
class ProxyConfig:
    """Proxy configuration."""
    url: str = ""                  # http://host:port or socks5://host:port
    username: str = ""
    password: str = ""


@dataclass
class TransportConfig:
    """Transport layer configuration."""
    type: str = "rest_api"         # rest_api | encrypted_api | spa_render | playwright_http | scrapling_stealth
    engine: str = "requests"       # requests | urllib | playwright_http | playwright_spa | scrapling
    headers: Dict[str, str] = field(default_factory=dict)
    session_init_url: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)
    verify_ssl: bool = True
    timeout: int = 60
    vue_http: bool = False          # Use Vue.__vue__.$http proxy for API calls (bypasses signature verification)
    encryption: Optional[EncryptionConfig] = None
    signing: Optional[SigningConfig] = None
    captcha: Optional[CaptchaConfig] = None
    proxy: Optional[ProxyConfig] = None
    # ── Scrapling / browser settings ──
    headless: bool = True           # headless browser mode
    solve_cloudflare: bool = False  # auto-solve Cloudflare Turnstile
    network_idle: bool = True       # wait for network idle before extracting
    impersonate: str = ""           # browser fingerprint (e.g. "chrome", "firefox135")
    adaptive: bool = False          # enable self-healing selectors (auto_save + adaptive)
    block_resources: bool = True    # block images/fonts/media for speed


@dataclass
class ListingConfig:
    """Listing page / API configuration."""
    url: str = ""
    method: str = "GET"            # GET | POST
    body_type: str = "query"       # query | json | form
    params: Dict[str, Any] = field(default_factory=dict)
    body: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PaginationConfig:
    """Pagination strategy configuration."""
    type: str = "page_no"          # page_no | offset | total_count | html_regex | click_next | single_page
    page_param: str = "page"
    page_size_param: str = "rows"
    page_size: int = 20
    start: int = 1
    total_field: str = "total"
    total_pages_field: str = ""
    items_field: str = "rows"
    max_pages: int = 0             # 0 = unlimited
    next_page_selector: str = ""   # CSS for "next page" button (click_next)
    next_page_regex: str = ""      # regex to extract next page URL (html_regex)
    page_pattern: str = ""         # static page URL pattern (html_regex, e.g. /index_{}.html)
    first_page_no_suffix: bool = False  # html_regex: start page uses listing.url, others use pattern


@dataclass
class AntiCrawlerConfig:
    """Anti-crawler strategy configuration."""
    delay_min: float = 1.0
    delay_max: float = 2.5
    max_retries: int = 4
    session_reset_on_429: bool = True
    exponential_backoff: bool = True
    eight_am_check: bool = False    # only run after 8 AM
    max_consecutive_empty: int = 5  # stop after N empty pages


@dataclass
class FieldMapping:
    """Map between API response fields and internal field names."""
    source: str = ""               # source field name in response
    target: str = ""               # internal field name
    default: Any = ""              # default value


@dataclass
class ExtractConfig:
    """Data extraction configuration for list and detail."""
    type: str = "json_path"        # json_path | css_selector | ai
    items_path: str = ""           # JSON path to items array (e.g. "rows" or "data.list")
    fields: Dict[str, str] = field(default_factory=dict)  # internal_name -> source_field
    ai_fallback: bool = True
    ai_prompt: str = ""


@dataclass
class DetailConfig:
    """Detail page fetching configuration."""
    type: str = "inline"           # inline | api_request | css_selector | none
    url: str = ""                  # API URL template, supports {id} placeholder
    method: str = "GET"
    body_type: str = "query"
    params: Dict[str, Any] = field(default_factory=dict)
    extract: Optional[ExtractConfig] = None
    content_field: str = "content"  # field that contains the main content
    attachment_fields: List[str] = field(default_factory=list)
    transport: Optional["TransportConfig"] = None  # override transport for detail (e.g. spa_render)


@dataclass
class FormatConfig:
    """Markdown formatting template configuration."""
    template: str = ""
    title_field: str = "title"
    date_field: str = "date"
    parser_id: str = "naive"
    upload_batch_size: int = 10
    output_filename_pattern: str = "{site_id}_batch_{batch_num:04d}.md"


@dataclass
class SectionConfig:
    """A subsection/section within a site (e.g. different site columns)."""
    label: str = ""
    listing: Optional[ListingConfig] = None
    pagination: Optional[PaginationConfig] = None
    extract: Optional[ExtractConfig] = None
    detail: Optional[DetailConfig] = None


@dataclass
class SiteConfig:
    """Complete configuration for a single site crawler."""
    site_id: str = ""
    name: str = ""
    site_url: str = ""
    enabled: bool = True
    detect_interval: int = 300       # detection check interval in seconds (default 5 min)
    detect_enabled: bool = True     # whether this site participates in detection
    transport: TransportConfig = field(default_factory=TransportConfig)
    listing: ListingConfig = field(default_factory=ListingConfig)
    pagination: PaginationConfig = field(default_factory=PaginationConfig)
    anti_crawler: AntiCrawlerConfig = field(default_factory=AntiCrawlerConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    detail: DetailConfig = field(default_factory=DetailConfig)
    format: FormatConfig = field(default_factory=FormatConfig)
    sections: List[SectionConfig] = field(default_factory=list)


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

class ConfigLoader:
    """Load and manage site configurations from YAML."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self._sites: Dict[str, SiteConfig] = {}
        self._defaults: Dict[str, Any] = {}
        self._loaded = False

    def load(self) -> Dict[str, SiteConfig]:
        """Load all site configs from YAML. Returns dict keyed by site_id."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not raw or "sites" not in raw:
            raise ValueError(f"No 'sites' key in {self.config_path}")

        # Extract global defaults if present
        self._defaults = raw.get("defaults", {})

        self._sites = {}
        for site_id, site_data in raw["sites"].items():
            self._sites[site_id] = self._parse_site(site_id, site_data)

        self._loaded = True
        logging.info("Loaded %d site configs from %s", len(self._sites), self.config_path)
        return self._sites

    def get(self, site_id: str) -> SiteConfig:
        """Get a single site config by ID. Loads on first access."""
        if not self._loaded:
            self.load()
        if site_id not in self._sites:
            raise KeyError(f"Site '{site_id}' not found in config")
        return self._sites[site_id]

    def get_enabled(self) -> List[SiteConfig]:
        """Return all enabled site configs."""
        if not self._loaded:
            self.load()
        return [s for s in self._sites.values() if s.enabled]

    def list_site_ids(self) -> List[str]:
        if not self._loaded:
            self.load()
        return list(self._sites.keys())

    # ---- Private helpers ----

    def _parse_site(self, site_id: str, data: dict) -> SiteConfig:
        return SiteConfig(
            site_id=site_id,
            name=data.get("name", site_id),
            site_url=data.get("site_url", ""),
            enabled=data.get("enabled", True),
            detect_interval=data.get("detect_interval", self._defaults.get("detect_interval", 300)),
            detect_enabled=data.get("detect_enabled", self._defaults.get("detect_enabled", True)),
            transport=self._parse_transport(data.get("transport", {})),
            listing=self._parse_listing(data.get("listing", {})),
            pagination=self._parse_pagination(data.get("pagination", {})),
            anti_crawler=self._parse_anti_crawler(data.get("anti_crawler", {})),
            extract=self._parse_extract(data.get("extract", {})),
            detail=self._parse_detail(data.get("detail", {})),
            format=self._parse_format(data.get("format", {})),
            sections=[
                self._parse_section(s) for s in data.get("sections", [])
            ],
        )

    def _parse_transport(self, data: dict) -> TransportConfig:
        enc = data.get("encryption")
        sig = data.get("signing")
        cap = data.get("captcha")
        prx = data.get("proxy")
        return TransportConfig(
            type=data.get("type", "rest_api"),
            engine=data.get("engine", "requests"),
            headers=data.get("headers", {}),
            session_init_url=data.get("session_init_url", ""),
            cookies=data.get("cookies", {}),
            verify_ssl=data.get("verify_ssl", True),
            timeout=data.get("timeout", 60),
            encryption=EncryptionConfig(**enc) if enc else None,
            signing=SigningConfig(**sig) if sig else None,
            vue_http=data.get("vue_http", False),
            captcha=CaptchaConfig(**cap) if cap else None,
            proxy=ProxyConfig(**prx) if prx else None,
            headless=data.get("headless", True),
            solve_cloudflare=data.get("solve_cloudflare", False),
            network_idle=data.get("network_idle", True),
            impersonate=data.get("impersonate", ""),
            adaptive=data.get("adaptive", False),
            block_resources=data.get("block_resources", True),
        )

    def _parse_listing(self, data: dict) -> ListingConfig:
        return ListingConfig(
            url=data.get("url", ""),
            method=data.get("method", "GET"),
            body_type=data.get("body_type", "query"),
            params=data.get("params", {}),
            body=data.get("body", {}),
        )

    def _parse_pagination(self, data: dict) -> PaginationConfig:
        return PaginationConfig(
            type=data.get("type", "page_no"),
            page_param=data.get("page_param", "page"),
            page_size_param=data.get("page_size_param", "rows"),
            page_size=data.get("page_size", 20),
            start=data.get("start", 1),
            total_field=data.get("total_field", "total"),
            total_pages_field=data.get("total_pages_field", ""),
            items_field=data.get("items_field", "rows"),
            max_pages=data.get("max_pages", 0),
            next_page_selector=data.get("next_page_selector", ""),
            next_page_regex=data.get("next_page_regex", ""),
            page_pattern=data.get("page_pattern", ""),
            first_page_no_suffix=data.get("first_page_no_suffix", False),
        )

    def _parse_anti_crawler(self, data: dict) -> AntiCrawlerConfig:
        return AntiCrawlerConfig(
            delay_min=data.get("delay_min", 1.0),
            delay_max=data.get("delay_max", 2.5),
            max_retries=data.get("max_retries", 4),
            session_reset_on_429=data.get("session_reset_on_429", True),
            exponential_backoff=data.get("exponential_backoff", True),
            eight_am_check=data.get("eight_am_check", False),
            max_consecutive_empty=data.get("max_consecutive_empty", 5),
        )

    def _parse_extract(self, data: dict) -> ExtractConfig:
        if not data:
            return ExtractConfig()
        return ExtractConfig(
            type=data.get("type", "json_path"),
            items_path=data.get("items_path", ""),
            fields=data.get("fields", {}),
            ai_fallback=data.get("ai_fallback", True),
            ai_prompt=data.get("ai_prompt", ""),
        )

    def _parse_detail(self, data: dict) -> DetailConfig:
        if not data:
            return DetailConfig()
        extract = data.get("extract")
        transport = data.get("transport")
        return DetailConfig(
            type=data.get("type", "inline"),
            url=data.get("url", ""),
            method=data.get("method", "GET"),
            body_type=data.get("body_type", "query"),
            params=data.get("params", {}),
            extract=self._parse_extract(extract) if extract else None,
            content_field=data.get("content_field", "content"),
            attachment_fields=data.get("attachment_fields", []),
            transport=self._parse_transport(transport) if transport else None,
        )

    def _parse_format(self, data: dict) -> FormatConfig:
        return FormatConfig(
            template=data.get("template", ""),
            title_field=data.get("title_field", "title"),
            date_field=data.get("date_field", "date"),
            parser_id=data.get("parser_id", "naive"),
            upload_batch_size=data.get("upload_batch_size", 10),
            output_filename_pattern=data.get(
                "output_filename_pattern", "{site_id}_batch_{batch_num:04d}.md"
            ),
        )

    def _parse_section(self, data: dict) -> SectionConfig:
        return SectionConfig(
            label=data.get("label", ""),
            listing=self._parse_listing(data.get("listing", {})),
            pagination=self._parse_pagination(data.get("pagination", {})),
            extract=self._parse_extract(data.get("extract", {})),
            detail=self._parse_detail(data.get("detail", {})),
        )
