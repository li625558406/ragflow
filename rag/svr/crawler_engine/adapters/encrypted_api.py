"""
Encrypted API adapter — SM4/AES decryption + MD5 portal signing.

Handles Chinese government procurement sites that encrypt their API
responses.  Supports:
- SM4-ECB decryption (e.g. easy_prt_bidding, enjoy5191)
- AES-256-CBC decryption (e.g. ggzyfw_fujian)
- MD5 portal signature generation
- Fallback to PlaywrightHttpClient when requests/urllib is blocked

Two encryption response patterns:
  A) Full binary encryption — raw response IS the ciphertext (SM4-ECB)
  B) JSON-wrapped + base64 — response is {"State":"200","Data":"<b64>"}
     then base64-decode Data, then AES decrypt (ggzyfw sites).
     Controlled by encryption.field in config.
"""

import base64
import json
import logging
import re
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode


def _get_json_value(data, path: str) -> Any:
    """Get a nested value by dot-separated path (dict keys + list indices).

    Example: "result.announcementList.0.contentId"
    """
    if not path:
        return None
    cur = data
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list):
            try:
                idx = int(key)
                cur = cur[idx] if 0 <= idx < len(cur) else None
            except (ValueError, TypeError):
                return None
        else:
            return None
    return cur

from ..config import SiteConfig
from ..crypto_utils import (
    sm4_ecb_decrypt,
    sm4_ecb_encrypt,
    aes_cbc_decrypt,
    md5_sign,
    hex_to_bytes,
)
from .. import resolve_params
from .base import BaseAdapter


class EncryptedApiAdapter(BaseAdapter):
    """Adapter for encrypted API endpoints."""

    def __init__(self, config: SiteConfig):
        super().__init__(config)
        self._use_playwright = (self._transport.engine == "playwright_http")
        self._pw_client = None

    # ------------------------------------------------------------------
    # fetch_items — listing page
    # ------------------------------------------------------------------

    def fetch_items(self, page_params: Dict[str, Any],
                    listing_override=None) -> Optional[List[Dict[str, Any]]]:
        """Fetch and decrypt a page of items."""
        listing = listing_override if listing_override else self._config.listing
        url = listing.url
        body_type = listing.body_type or "form"

        # Build params
        params = dict(listing.params)
        params.update(page_params)

        # Resolve {{ page }} / {{ page_size }} / {{ today }} / {{ N_days_ago }} templates
        pag_cfg = self._config.pagination
        page_val = str(page_params.get(pag_cfg.page_param, ""))
        size_val = str(page_params.get(pag_cfg.page_size_param, ""))
        params = resolve_params(params, page_val, size_val)

        # Build sign + extra headers
        extra_headers = {}
        if self._transport.signing:
            # Add timestamp (required for ggzyfw sign calculation)
            params["ts"] = int(time.time() * 1000)
            sign_val = md5_sign(params, self._transport.signing.secret)
            header_name = self._transport.signing.header_name or "sign"
            extra_headers[header_name] = sign_val

        for attempt in range(self._config.anti_crawler.max_retries):
            try:
                if self._use_playwright:
                    raw = self._fetch_via_playwright(url, params, listing.method,
                                                     body_type, extra_headers)
                else:
                    raw = self._fetch_via_urllib(url, params, listing.method,
                                                 body_type, extra_headers)

                if raw is None:
                    continue

                self._last_raw = raw

                # Decrypt if needed
                if self._transport.encryption:
                    decrypted = self._decrypt(raw)
                    if decrypted:
                        return self._parse_decrypted(decrypted)
                else:
                    return self._parse_json(raw)

                logging.warning("EncryptedApiAdapter: decrypt returned empty, attempt %d", attempt + 1)
                time.sleep(2 + attempt * 3)

            except Exception as e:
                logging.warning("EncryptedApiAdapter: attempt %d failed: %s", attempt + 1, e)
                time.sleep(2 + attempt * 3)

        return None

    # ------------------------------------------------------------------
    # HTTP transport
    # ------------------------------------------------------------------

    def _maybe_encrypt_request(self, url: str, params: Dict, method: str,
                                body_type: str) -> tuple[str, Dict, Optional[bytes]]:
        """Apply request-side encryption when configured.

        Returns (url, params_or_empty, body_bytes).
        - enc.encrypt_request=False: passthrough — body/url built by caller as usual.
        - POST + encrypt_request: body is SM4-encrypted JSON of params,
          wrapped per `request_format` (json_string | raw_hex).
        - GET  + encrypt_request: url becomes `?<request_param_name>=<hex>`,
          body is None.
        """
        enc = self._transport.encryption
        if not enc or not getattr(enc, "encrypt_request", False):
            return url, params, None

        # SM4-ECB encrypt the JSON-serialised params → hex
        if enc.algorithm == "sm4_ecb":
            key = hex_to_bytes(enc.key) if enc.key_encoding == "hex" else enc.key.encode("utf-8")
            payload = json.dumps(params, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ct = sm4_ecb_encrypt(payload, key)
            hex_ct = ct.hex()
        elif enc.algorithm == "aes_256_cbc":
            from ..crypto_utils import aes_cbc_encrypt
            key = hex_to_bytes(enc.key) if enc.key_encoding == "hex" else enc.key.encode("utf-8")
            iv = hex_to_bytes(enc.iv) if enc.key_encoding == "hex" else enc.iv.encode("utf-8")
            payload = json.dumps(params, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            hex_ct = aes_cbc_encrypt(payload, key, iv).hex()
        else:
            logging.warning("EncryptedApiAdapter: encrypt_request not supported for %s",
                            enc.algorithm)
            return url, params, None

        if method.upper() == "POST":
            if enc.request_format == "raw_hex":
                body = hex_ct.encode("utf-8")
            else:  # json_string (default)
                body = json.dumps(hex_ct).encode("utf-8")
            return url, {}, body

        # GET: append ?<param_name>=<hex>
        param_name = enc.request_param_name or "encryptParams"
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{param_name}={hex_ct}", {}, None

    def _fetch_via_urllib(self, url: str, params: Dict, method: str = "GET",
                           body_type: str = "form",
                           extra_headers: Optional[Dict[str, str]] = None) -> Optional[bytes]:
        """Fetch via urllib with headers from transport config."""
        headers = dict(self._transport.headers)
        if extra_headers:
            headers.update(extra_headers)
        timeout = self._transport.timeout

        url, params, enc_body = self._maybe_encrypt_request(url, params, method.upper(), body_type)
        if enc_body is not None:
            # Encrypted request — body is pre-built, bypass body_type
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json;charset=UTF-8"
            req = urllib.request.Request(url, data=enc_body, headers=headers,
                                         method=method.upper())
        elif method.upper() == "POST":
            if body_type == "json":
                data = json.dumps(params, ensure_ascii=False).encode("utf-8")
                if "Content-Type" not in headers:
                    headers["Content-Type"] = "application/json;charset=UTF-8"
            else:
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

    def _fetch_via_playwright(self, url: str, params: Dict, method: str = "GET",
                               body_type: str = "form",
                               extra_headers: Optional[Dict[str, str]] = None) -> Optional[bytes]:
        """Fetch via PlaywrightHttpClient."""
        import sys
        import os
        _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        _PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)

        from rag.svr.crawler_utils import PlaywrightHttpClient

        if self._pw_client is None:
            # Fix: sync_playwright conflicts with a running asyncio event loop.
            try:
                import asyncio
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                try:
                    import nest_asyncio
                    nest_asyncio.apply()
                except ImportError:
                    logging.warning("EncryptedApiAdapter: asyncio loop detected but "
                                    "nest_asyncio not installed.")
            self._pw_client = PlaywrightHttpClient()

        try:
            url, enc_params, enc_body = self._maybe_encrypt_request(
                url, params, method.upper(), body_type,
            )
            if enc_body is not None:
                # Encrypted request — body is pre-built bytes.  Pass as decoded
                # string so PlaywrightHttpClient sends exactly what we give it
                # (it would otherwise stringify the bytes object incorrectly).
                merged_headers = dict(extra_headers or {})
                if "Content-Type" not in merged_headers:
                    merged_headers["Content-Type"] = "application/json;charset=UTF-8"
                resp = self._pw_client.post(url, data=enc_body.decode("utf-8"),
                                            headers=merged_headers)
            elif method.upper() == "POST":
                if body_type == "json":
                    resp = self._pw_client.post(url, json_body=enc_params, headers=extra_headers)
                else:
                    resp = self._pw_client.post(url, data=enc_params, headers=extra_headers)
            else:
                resp = self._pw_client.get(url, headers=extra_headers)

            if resp.status_code != 200:
                return None
            return resp.content if isinstance(resp.content, bytes) else resp.content.encode("utf-8")
        except Exception as e:
            logging.warning("EncryptedApiAdapter: playwright fetch failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Decryption
    # ------------------------------------------------------------------

    def _decrypt(self, raw: bytes) -> Optional[str]:
        """Decrypt response based on encryption config.

        Three patterns:
          A) enc.field NOT set → raw bytes are the ciphertext (SM4-ECB etc.)
          B) enc.field IS set + JSON object response → look up field, base64-
             decode (ggzyfw pattern: {"State":"200","Data":"<b64>"})
          C) enc.field IS set + JSON string-literal response → strip quotes,
             treat directly as hex ciphertext. Used by easy-prt.com where
             the response body is a JSON-encoded string like "<hex>" rather
             than a {"field": "<hex>"} object.
        """
        enc = self._transport.encryption
        if not enc:
            return raw.decode("utf-8", errors="replace")

        try:
            text = raw.decode("utf-8", errors="replace").strip()

            # --- Pattern B: JSON-wrapped object + base64 (ggzyfw) ---
            if getattr(enc, "field", None):
                # First try parsing as JSON
                try:
                    wrapper = json.loads(text)
                except json.JSONDecodeError:
                    # Not JSON → fall through to Pattern C (string-literal hex)
                    wrapper = None

                if isinstance(wrapper, dict):
                    field_val = wrapper.get(enc.field)
                    if field_val is None:
                        logging.warning(
                            "EncryptedApiAdapter: field '%s' not found in response, keys=%s",
                            enc.field, list(wrapper.keys()),
                        )
                        return None
                    # ggzyfw returns base64-encoded ciphertext under the field
                    if isinstance(field_val, str) and re.fullmatch(r"[0-9A-Fa-f]+", field_val):
                        # Pure hex → direct ciphertext (no base64 layer)
                        ciphertext = bytes.fromhex(field_val)
                    else:
                        try:
                            ciphertext = base64.b64decode(field_val)
                        except Exception:
                            ciphertext = field_val.encode("utf-8")
                    return self._decrypt_raw(ciphertext)

                # --- Pattern C: JSON string-literal "<hex>" ---
                # Strip surrounding quotes (handles both " and \" escapes)
                stripped = text.strip()
                if stripped.startswith('"') and stripped.endswith('"'):
                    stripped = stripped[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                stripped = stripped.strip()
                if re.fullmatch(r"[0-9A-Fa-f]+", stripped):
                    return self._decrypt_raw(bytes.fromhex(stripped))

                logging.warning(
                    "EncryptedApiAdapter: enc.field set but response is neither "
                    "JSON object nor pure hex string (preview=%r)",
                    text[:80],
                )
                return None

            # --- Pattern A: raw ciphertext ---
            # Two body shapes occur in the wild:
            #   A1) raw binary ciphertext bytes
            #   A2) ASCII-hex string  e.g. b"bb3ebb9a..." (easy-prt emits this)
            # If the body decodes as pure hex ASCII, convert first.
            text_str = raw.decode("utf-8", errors="replace").strip()
            if re.fullmatch(r"[0-9A-Fa-f]+", text_str) and len(text_str) % 2 == 0:
                return self._decrypt_raw(bytes.fromhex(text_str))
            return self._decrypt_raw(raw)

        except Exception as e:
            logging.error("EncryptedApiAdapter: decryption failed: %s", e)
            return None

    def _decrypt_raw(self, ciphertext: bytes) -> Optional[str]:
        """Decrypt raw ciphertext bytes using the configured algorithm."""
        enc = self._transport.encryption

        if enc.algorithm == "sm4_ecb":
            key = hex_to_bytes(enc.key) if enc.key_encoding == "hex" else enc.key.encode("utf-8")
            plain = sm4_ecb_decrypt(ciphertext, key)
            return plain.decode(enc.encoding, errors="replace").strip()

        elif enc.algorithm == "aes_256_cbc":
            key = hex_to_bytes(enc.key) if enc.key_encoding == "hex" else enc.key.encode("utf-8")
            iv = hex_to_bytes(enc.iv) if enc.key_encoding == "hex" else enc.iv.encode("utf-8")
            plain = aes_cbc_decrypt(ciphertext, key, iv)
            return plain.decode(enc.encoding, errors="replace").strip()

        else:
            logging.warning("EncryptedApiAdapter: unknown algorithm '%s'", enc.algorithm)
            return ciphertext.decode(enc.encoding, errors="replace")

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    def _parse_json(self, raw: bytes) -> List[Dict[str, Any]]:
        """Parse raw bytes as JSON and extract items."""
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items_field = self._config.pagination.items_field
            if items_field:
                # Support dot-path (e.g. "result.records")
                v = _get_json_value(data, items_field)
                if isinstance(v, list):
                    return v
            for key in ("rows", "data", "list", "records", "result", "Table"):
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

    # ------------------------------------------------------------------
    # Detail fetch
    # ------------------------------------------------------------------

    def fetch_detail(self, item: Dict[str, Any],
                     detail_override=None) -> Optional[Dict[str, Any]]:
        """Fetch detail page for encrypted API sites.

        Optional chained content fetch (`detail.content_endpoint`):
        after the primary detail response is parsed, the adapter makes a
        second HTTP call to retrieve the full-text HTML body and additional
        attachments.  Templates in `content_endpoint.params` may reference
        nested paths of the primary detail response (e.g.
        `{result.announcementList.0.contentId}`).
        """
        detail_cfg = detail_override or self._config.detail

        # css_selector / inline / none handled by base class
        if detail_cfg.type not in ("api_request",) or not detail_cfg.url:
            return super().fetch_detail(item)

        primary_inner = self._call_endpoint(
            url_tmpl=detail_cfg.url,
            params_tmpl=detail_cfg.params,
            method=detail_cfg.method,
            body_type=detail_cfg.body_type or "form",
            item=item,
        )

        if primary_inner is None:
            return item

        # Merge primary detail response (full JSON, then nested `result` if present)
        self._merge_detail_into_item(item, primary_inner)

        # Extract content_field (dot-path) from primary response if configured
        if detail_cfg.content_field:
            val = _get_json_value(primary_inner, detail_cfg.content_field)
            if val:
                item["content"] = str(val)

        # Promote detail attachment_fields (dot-paths) → item["files"]
        self._promote_attachments(item, primary_inner, detail_cfg.attachment_fields)

        # ── Optional chained content call ──
        ce = detail_cfg.content_endpoint
        if ce and ce.url:
            ce_inner = self._call_endpoint(
                url_tmpl=ce.url,
                params_tmpl=ce.params,
                method=ce.method,
                body_type=ce.body_type or "form",
                item=item,
                response_ctx=primary_inner,
            )
            if ce_inner is not None:
                self._merge_detail_into_item(item, ce_inner)
                if ce.content_field:
                    val = _get_json_value(ce_inner, ce.content_field)
                    if val:
                        item["content"] = str(val)
                self._promote_attachments(item, ce_inner, ce.attachment_fields)

        return item

    # ------------------------------------------------------------------
    # Detail helpers
    # ------------------------------------------------------------------

    def _call_endpoint(self, url_tmpl: str, params_tmpl: Dict[str, Any],
                       method: str, body_type: str,
                       item: Dict[str, Any],
                       response_ctx: Optional[Dict[str, Any]] = None,
                       retries: int = 3) -> Optional[Any]:
        """Call an encrypted API endpoint with template-substituted params.

        Templates:
          - {item_key}      → item[key] (flat substitution)
          - {a.b.0.c}       → nested path lookup against `response_ctx`
                             (used to chain detail → content via contentId)

        Returns decrypted+parsed JSON (dict/list), or None on failure.
        """
        if not url_tmpl:
            return None
        url = self._substitute(url_tmpl, item, response_ctx)

        params: Dict[str, Any] = {}
        for pkey, pval in params_tmpl.items():
            if isinstance(pval, str) and "{" in pval:
                params[pkey] = self._substitute(pval, item, response_ctx)
            else:
                params[pkey] = pval

        extra_headers = {}
        if self._transport.signing:
            params["ts"] = int(time.time() * 1000)
            sign_val = md5_sign(params, self._transport.signing.secret)
            header_name = self._transport.signing.header_name or "sign"
            extra_headers[header_name] = sign_val

        for attempt in range(retries):
            try:
                if self._use_playwright:
                    raw = self._fetch_via_playwright(url, params, method,
                                                     body_type, extra_headers)
                else:
                    raw = self._fetch_via_urllib(url, params, method,
                                                 body_type, extra_headers)
                if raw is None:
                    continue

                if self._transport.encryption:
                    decrypted = self._decrypt(raw)
                    if not decrypted:
                        time.sleep(1 + attempt)
                        continue
                    try:
                        return json.loads(decrypted)
                    except json.JSONDecodeError:
                        logging.warning(
                            "EncryptedApiAdapter: decrypted detail is not JSON: %r",
                            decrypted[:120],
                        )
                        return None
                else:
                    return json.loads(raw.decode("utf-8", errors="replace"))
            except Exception as e:
                logging.warning("EncryptedApiAdapter: endpoint %s failed: %s", url, e)
                time.sleep(1 + attempt)
        return None

    @staticmethod
    def _substitute(template: str, item: Dict[str, Any],
                    response_ctx: Optional[Dict[str, Any]] = None) -> str:
        """Replace `{key}` / `{a.b.0.c}` placeholders.

        Priority:
          1. Flat item key (backward compatible)
          2. Nested path in response_ctx (for chained content call)
        """
        if "{" not in template:
            return template

        def _resolve(match: re.Match) -> str:
            key = match.group(1)
            # 1) Flat key on item (backward compat)
            if key in item:
                return str(item[key])
            # 2) Nested dot-path on item (e.g. announcementList.0.contentId)
            val = _get_json_value(item, key)
            if val is not None:
                return str(val)
            # 3) Nested dot-path on response_ctx (chained from prior response)
            if response_ctx is not None:
                val = _get_json_value(response_ctx, key)
                if val is not None:
                    return str(val)
            # Unresolved → empty (avoid leaving literal {x} in URL)
            return ""

        return re.sub(r"\{([^{}]+)\}", _resolve, template)

    @staticmethod
    def _merge_detail_into_item(item: Dict[str, Any], inner: Any) -> None:
        """Merge detail JSON into item, including nested `result` object.

        - Top-level dict keys merge into item (skipping existing keys)
        - If a `result`/`data` key holds a dict, that dict's keys also merge
          in (so `result.announcementList` becomes `item["announcementList"]`)
        """
        if not isinstance(inner, dict):
            return
        for k, v in inner.items():
            if k in ("result", "data") and isinstance(v, dict):
                for ik, iv in v.items():
                    if ik not in item:
                        item[ik] = iv
            elif k not in item:
                item[k] = v

    def _promote_attachments(self, item: Dict[str, Any], inner: Any,
                             attachment_paths: List[str]) -> None:
        """Walk attachment dot-paths in `inner`, normalise, append to files.

        Recognises common file metadata shapes from government portals:
          [{"url": "...", "fileName": "..."}, {"fileUrl": "...", "name": "..."}]

        Relative fileUrl (e.g. "/business/2025/9/11/x.pdf") is auto-absolutised
        against SiteConfig.attachment_host when configured.
        """
        if not attachment_paths:
            return
        host_prefix = (getattr(self._config, "attachment_host", "") or "").rstrip("/")
        collected: List[Dict[str, Any]] = []
        for path in attachment_paths:
            raw = _get_json_value(inner, path)
            if isinstance(raw, list):
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    file_url = (entry.get("url") or entry.get("fileUrl")
                                or entry.get("file_url") or entry.get("href") or "")
                    if not file_url:
                        continue
                    file_name = (entry.get("fileName") or entry.get("name")
                                 or entry.get("file_name") or "")
                    collected.append({"file_name": file_name, "file_url": file_url})
            elif isinstance(raw, dict):
                file_url = (raw.get("url") or raw.get("fileUrl")
                            or raw.get("file_url") or "")
                if file_url:
                    file_name = (raw.get("fileName") or raw.get("name") or "")
                    collected.append({"file_name": file_name, "file_url": file_url})

        if not collected:
            return
        # Absolutise relative URLs against attachment_host (if configured).
        # E.g. "/business/2025/9/11/x.pdf" + "https://files.example.com"
        #    → "https://files.example.com/business/2025/9/11/x.pdf"
        if host_prefix:
            for c in collected:
                u = c.get("file_url", "")
                if u and not u.startswith(("http://", "https://")):
                    c["file_url"] = host_prefix + (u if u.startswith("/") else "/" + u)
        # Dedup by url, merge with existing
        existing = item.get("files") if isinstance(item.get("files"), list) else []
        seen = {e.get("file_url") for e in existing if isinstance(e, dict)}
        for c in collected:
            if c["file_url"] not in seen:
                seen.add(c["file_url"])
                existing.append(c)
        item["files"] = existing

    def cleanup(self) -> None:
        if self._pw_client:
            try:
                self._pw_client.close()
            except Exception:
                pass
            self._pw_client = None
