"""
Encrypted API adapter — SM4/AES decryption + MD5 portal signing.

Handles Chinese government procurement sites that encrypt their API
responses.  Supports:
- SM4-ECB decryption (e.g. easy_prt_bidding, enjoy5191)
- AES-256-CBC decryption (e.g. ggzyfw_fujian)
- MD5 portal signature generation
- Fallback to PlaywrightHttpClient when requests/urllib is blocked
"""

import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from ..config import SiteConfig
from ..crypto_utils import (
    sm4_ecb_decrypt,
    aes_cbc_decrypt,
    md5_sign,
    hex_to_bytes,
)
from .base import BaseAdapter


class EncryptedApiAdapter(BaseAdapter):
    """Adapter for encrypted API endpoints."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        self._use_playwright = (self._transport.engine == "playwright_http")
        self._pw_client = None

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Fetch and decrypt a page of items."""
        listing = listing_override if listing_override else self._config.listing
        url = listing.url

        # Build params with optional signing
        params = dict(listing.params)
        params.update(page_params)

        if self._transport.signing:
            sign_val = md5_sign(params, self._transport.signing.secret)
            params["sign"] = sign_val

        for attempt in range(self._config.anti_crawler.max_retries):
            try:
                if self._use_playwright:
                    raw = self._fetch_via_playwright(url, params, listing.method)
                else:
                    raw = self._fetch_via_urllib(url, params, listing.method)

                if raw is None:
                    continue

                self._last_raw = raw

                # Decrypt if needed
                if self._transport.encryption:
                    decrypted = self._decrypt(raw)
                    if decrypted:
                        return self._parse_decrypted(decrypted)
                else:
                    # No encryption, try JSON directly
                    return self._parse_json(raw)

                logging.warning("EncryptedApiAdapter: decrypt returned empty, attempt %d", attempt + 1)
                time.sleep(2 + attempt * 3)

            except Exception as e:
                logging.warning("EncryptedApiAdapter: attempt %d failed: %s", attempt + 1, e)
                time.sleep(2 + attempt * 3)

        return None

    def _fetch_via_urllib(self, url: str, params: Dict, method: str = "GET") -> Optional[bytes]:
        """Fetch via urllib with headers from transport config."""
        from urllib.parse import urlencode

        headers = dict(self._transport.headers)
        timeout = self._transport.timeout

        if method.upper() == "POST":
            data = urlencode(params).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        else:
            qs = urlencode(params)
            full_url = f"{url}?{qs}" if qs else url
            req = urllib.request.Request(full_url, headers=headers)

        if not self._transport.verify_ssl:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)

        return resp.read()

    def _fetch_via_playwright(self, url: str, params: Dict, method: str = "GET") -> Optional[bytes]:
        """Fetch via PlaywrightHttpClient."""
        import sys
        import os
        _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        _PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)

        from rag.svr.crawler_utils import PlaywrightHttpClient

        if self._pw_client is None:
            self._pw_client = PlaywrightHttpClient()

        try:
            if method.upper() == "POST":
                resp = self._pw_client.post(url, data=params)
            else:
                resp = self._pw_client.get(url, params=params)

            if resp.status_code != 200:
                return None
            return resp.content if isinstance(resp.content, bytes) else resp.content.encode("utf-8")
        except Exception as e:
            logging.warning("EncryptedApiAdapter: playwright fetch failed: %s", e)
            return None

    def _decrypt(self, raw: bytes) -> Optional[str]:
        """Decrypt response based on encryption config."""
        enc = self._transport.encryption
        if not enc:
            return raw.decode("utf-8", errors="replace")

        try:
            if enc.algorithm == "sm4_ecb":
                key = hex_to_bytes(enc.key) if enc.key_encoding == "hex" else enc.key.encode("utf-8")
                plain = sm4_ecb_decrypt(raw, key)
                return plain.decode(enc.encoding, errors="replace").strip()

            elif enc.algorithm == "aes_256_cbc":
                key = hex_to_bytes(enc.key) if enc.key_encoding == "hex" else enc.key.encode("utf-8")
                iv = hex_to_bytes(enc.iv) if enc.key_encoding == "hex" else enc.iv.encode("utf-8")
                plain = aes_cbc_decrypt(raw, key, iv)
                return plain.decode(enc.encoding, errors="replace").strip()

            else:
                logging.warning("EncryptedApiAdapter: unknown algorithm '%s'", enc.algorithm)
                return raw.decode(enc.encoding, errors="replace")
        except Exception as e:
            logging.error("EncryptedApiAdapter: decryption failed: %s", e)
            return None

    def _parse_json(self, raw: bytes) -> List[Dict[str, Any]]:
        """Parse raw bytes as JSON and extract items."""
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items_field = self._config.pagination.items_field
            if items_field and items_field in data:
                items = data[items_field]
                return items if isinstance(items, list) else []
            for key in ("rows", "data", "list", "records", "result"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []

    def _parse_decrypted(self, text: str) -> List[Dict[str, Any]]:
        """Parse decrypted text as JSON and extract items."""
        try:
            return self._parse_json(text.encode("utf-8"))
        except json.JSONDecodeError:
            logging.warning("EncryptedApiAdapter: decrypted text is not valid JSON")
            return [{"raw_text": text}]

    def fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch detail page for encrypted API sites."""
        detail_cfg = self._config.detail

        # css_selector / inline / none handled by base class
        if detail_cfg.type not in ("api_request",) or not detail_cfg.url:
            return super().fetch_detail(item)

        url = detail_cfg.url
        for key, val in item.items():
            url = url.replace("{" + key + "}", str(val))

        params = dict(detail_cfg.params)
        if self._transport.signing:
            params["sign"] = md5_sign(params, self._transport.signing.secret)

        for attempt in range(3):
            try:
                if self._use_playwright:
                    raw = self._fetch_via_playwright(url, params, detail_cfg.method)
                else:
                    raw = self._fetch_via_urllib(url, params, detail_cfg.method)

                if raw:
                    if self._transport.encryption:
                        decrypted = self._decrypt(raw)
                        if decrypted:
                            item["content"] = decrypted
                    else:
                        item["content"] = raw.decode("utf-8", errors="replace")
                return item
            except Exception as e:
                logging.warning("EncryptedApiAdapter: detail fetch failed: %s", e)
                time.sleep(1 + attempt)

        return item

    def cleanup(self) -> None:
        if self._pw_client:
            try:
                self._pw_client.close()
            except Exception:
                pass
            self._pw_client = None
