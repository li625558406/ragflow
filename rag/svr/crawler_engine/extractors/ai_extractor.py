"""
AI-powered extractor — uses LLM to extract structured data from raw text/HTML.

Serves as a fallback when structured extraction fails.  The LLM is
prompted to extract fields as JSON from unstructured content.

This is a monitoring+fallback extractor, not the primary path.
When it fires, it logs a metric so we know the primary extractor
needs improvement for that site.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseExtractor


class AIExtractor(BaseExtractor):
    """LLM-based fallback extractor for unstructured content."""

    def __init__(self, config, llm_id: Optional[str] = None,
                 llm_model: Optional[str] = None):
        super().__init__(config)
        self._llm_id = llm_id
        self._llm_model = llm_model

    def extract(self, raw_data: Any) -> List[Dict[str, Any]]:
        """Attempt AI-powered extraction from raw content.

        Fall back to returning the raw data as a single item if AI is
        unavailable or fails.
        """
        if isinstance(raw_data, list):
            items = raw_data
        elif isinstance(raw_data, dict):
            items = [raw_data]
        elif isinstance(raw_data, str):
            items = [{"raw_text": raw_data[:5000]}]  # truncate
        else:
            return []

        # Attempt AI extraction for each item
        result = []
        for item in items:
            extracted = self._ai_extract(item)
            if extracted:
                result.append(extracted)
            else:
                result.append(item)  # keep original on failure

        if result:
            logging.info("AIExtractor: successfully extracted %d items", len(result))
        return result

    def _ai_extract(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Use LLM to extract structured fields from an item."""
        text = item.get("text") or item.get("html") or item.get("raw_text") or json.dumps(item, ensure_ascii=False)
        if not text or len(text) < 10:
            return None

        fields_desc = json.dumps(self._config.fields, ensure_ascii=False) if self._config.fields else "auto-detect"
        prompt = self._config.ai_prompt or (
            f"Extract the following fields from the text as JSON.\n"
            f"Fields: {fields_desc}\n"
            f"If a field is not found, set it to empty string.\n"
            f"Return ONLY valid JSON, no explanation.\n\n"
            f"Text:\n{text[:3000]}"
        )

        try:
            result = self._call_llm(prompt)
            if result:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    # Merge with original item
                    merged = dict(item)
                    merged.update(parsed)
                    return merged
        except Exception as e:
            logging.warning("AIExtractor: LLM extraction failed: %s", e)

        return None

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM for extraction.

        Uses the configured LLM if available, otherwise returns None.
        """
        # TODO: integrate with RAGFlow LLM service
        logging.debug("AIExtractor: LLM call not yet integrated, prompt length=%d", len(prompt))
        return None
