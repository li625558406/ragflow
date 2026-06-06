"""Adapter layer for different HTTP transport methods."""

from .base import BaseAdapter
from .rest_api import RestApiAdapter
from .encrypted_api import EncryptedApiAdapter
from .spa_render import SpaRenderAdapter
from .playwright_http import PlaywrightHttpAdapter

__all__ = [
    "BaseAdapter",
    "RestApiAdapter",
    "EncryptedApiAdapter",
    "SpaRenderAdapter",
    "PlaywrightHttpAdapter",
]
