"""
Unified anti-crawler strategy manager.

Handles:
- Request delays (randomized)
- Retry with exponential backoff
- Session reset on 429 / rate-limit
- 8 AM check (some government sites only update overnight)
- CAPTCHA detection and handling (OCR via ddddocr)
- Consecutive empty page detection
"""

import logging
import random
import time
from datetime import datetime
from typing import Optional

import requests

from .config import AntiCrawlerConfig, CaptchaConfig

# Common User-Agent pool — realistic browser fingerprints for rotation.
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def get_random_ua() -> str:
    """Return a random User-Agent string from the pool."""
    return random.choice(_UA_POOL)


class AntiCrawlerManager:
    """Manages anti-crawler strategies based on site configuration."""

    def __init__(self, config: AntiCrawlerConfig, captcha_config: Optional[CaptchaConfig] = None):
        self._config = config
        self._captcha_config = captcha_config
        self._consecutive_empty = 0

    # ---- Delay ----

    def delay(self) -> None:
        """Sleep for a randomized interval between requests."""
        t = random.uniform(self._config.delay_min, self._config.delay_max)
        time.sleep(t)

    def backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff wait time."""
        if self._config.exponential_backoff:
            return (2 ** attempt) + random.uniform(1, 3)
        return self._config.delay_max * attempt

    # ---- Retry logic ----

    def should_retry(self, attempt: int) -> bool:
        return attempt < self._config.max_retries

    # ---- 8 AM check ----

    def check_eight_am(self) -> bool:
        """Return True if crawler should proceed (after 8 AM or check disabled)."""
        if not self._config.eight_am_check:
            return True
        return datetime.now().hour >= 8

    # ---- Rate limit handling ----

    def handle_rate_limit(self, sess: requests.Session, init_url: str = "") -> None:
        """Handle 429 response: wait and optionally reset session."""
        if self._config.session_reset_on_429:
            sess.cookies.clear()
            if init_url:
                try:
                    sess.get(init_url, timeout=30)
                except Exception:
                    pass

    # ---- Stale streak ----

    def record_empty_page(self) -> bool:
        """Record an empty page. Returns True if we should stop."""
        self._consecutive_empty += 1
        return self._consecutive_empty >= self._config.max_consecutive_empty

    def record_new_items(self) -> None:
        """Reset the consecutive empty counter."""
        self._consecutive_empty = 0

    @property
    def consecutive_empty(self) -> int:
        return self._consecutive_empty

    # ---- CAPTCHA (ddddocr) ----

    @staticmethod
    def solve_captcha_ocr(image_bytes: bytes) -> Optional[str]:
        """Attempt to solve a CAPTCHA image using ddddocr.

        Returns the recognized text, or None on failure.
        """
        try:
            import ddddocr
            ocr = ddddocr.DdddOcr()
            result = ocr.classification(image_bytes)
            return result
        except ImportError:
            logging.warning("ddddocr not available for CAPTCHA solving")
            return None
        except Exception as e:
            logging.warning("ddddocr CAPTCHA solving failed: %s", e)
            return None

    # ---- Log summary ----

    def log_summary(self) -> None:
        logging.info("Anti-crawler: delay=%.1f-%.1fs, retries=%d, consecutive_empty=%d",
                     self._config.delay_min, self._config.delay_max,
                     self._config.max_retries, self._consecutive_empty)
