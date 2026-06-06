"""Tests for crawler_engine.config — YAML loading and SiteConfig parsing."""

import os
import tempfile

import pytest

from rag.svr.crawler_engine.config import ConfigLoader, SiteConfig, TransportConfig


SAMPLE_YAML = """
version: "1.0"
sites:
  test_site:
    name: "Test Site"
    site_url: "https://example.com"
    enabled: true
    transport:
      type: rest_api
      engine: requests
      headers:
        User-Agent: "TestAgent/1.0"
      verify_ssl: false
      timeout: 30
    listing:
      url: "https://example.com/api/list"
      method: POST
      body_type: json
    pagination:
      type: page_no
      page_param: "page"
      page_size_param: "pageSize"
      page_size: 20
      start: 1
      total_field: "totalCount"
      items_field: "items"
    anti_crawler:
      delay_min: 1.0
      delay_max: 3.0
      max_retries: 3
    extract:
      type: json_path
      items_path: "items"
      fields:
        id: "uuid"
        title: "name"
    detail:
      type: inline
    format:
      parser_id: "laws"
      upload_batch_size: 5
"""


class TestConfigLoader:
    """Tests for the ConfigLoader class."""

    def test_load_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_YAML)
            tmp_path = f.name

        try:
            loader = ConfigLoader(tmp_path)
            sites = loader.load()
            assert "test_site" in sites
            cfg = sites["test_site"]
            assert isinstance(cfg, SiteConfig)
            assert cfg.name == "Test Site"
            assert cfg.site_url == "https://example.com"
            assert cfg.enabled is True
        finally:
            os.unlink(tmp_path)

    def test_transport_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_YAML)
            tmp_path = f.name

        try:
            loader = ConfigLoader(tmp_path)
            cfg = loader.get("test_site")
            assert cfg.transport.type == "rest_api"
            assert cfg.transport.engine == "requests"
            assert cfg.transport.headers["User-Agent"] == "TestAgent/1.0"
            assert cfg.transport.verify_ssl is False
            assert cfg.transport.timeout == 30
        finally:
            os.unlink(tmp_path)

    def test_pagination_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_YAML)
            tmp_path = f.name

        try:
            loader = ConfigLoader(tmp_path)
            cfg = loader.get("test_site")
            assert cfg.pagination.type == "page_no"
            assert cfg.pagination.page_size == 20
            assert cfg.pagination.total_field == "totalCount"
            assert cfg.pagination.items_field == "items"
        finally:
            os.unlink(tmp_path)

    def test_extract_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_YAML)
            tmp_path = f.name

        try:
            loader = ConfigLoader(tmp_path)
            cfg = loader.get("test_site")
            assert cfg.extract.type == "json_path"
            assert cfg.extract.fields["id"] == "uuid"
            assert cfg.extract.fields["title"] == "name"
        finally:
            os.unlink(tmp_path)

    def test_missing_file(self):
        loader = ConfigLoader("/nonexistent/path.yaml")
        with pytest.raises(FileNotFoundError):
            loader.load()

    def test_missing_site(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_YAML)
            tmp_path = f.name

        try:
            loader = ConfigLoader(tmp_path)
            with pytest.raises(KeyError):
                loader.get("nonexistent_site")
        finally:
            os.unlink(tmp_path)

    def test_get_enabled(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(SAMPLE_YAML)
            tmp_path = f.name

        try:
            loader = ConfigLoader(tmp_path)
            enabled = loader.get_enabled()
            assert len(enabled) == 1
            assert enabled[0].site_id == "test_site"
        finally:
            os.unlink(tmp_path)

    def test_encrypted_api_config(self):
        encrypted_yaml = """
version: "1.0"
sites:
  enc_site:
    name: "Encrypted Site"
    site_url: "https://enc.example.com"
    transport:
      type: encrypted_api
      engine: playwright_http
      encryption:
        algorithm: sm4_ecb
        key: "90bdd291004611ef87fc52540023e781"
        key_encoding: hex
      signing:
        algorithm: md5
        secret: "test_secret_123"
    listing:
      url: "https://enc.example.com/api/list"
    pagination:
      type: page_no
      page_size: 20
    extract:
      type: json_path
      items_path: "rows"
    format:
      parser_id: "naive"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(encrypted_yaml)
            tmp_path = f.name

        try:
            loader = ConfigLoader(tmp_path)
            cfg = loader.get("enc_site")
            assert cfg.transport.type == "encrypted_api"
            assert cfg.transport.encryption.algorithm == "sm4_ecb"
            assert cfg.transport.encryption.key == "90bdd291004611ef87fc52540023e781"
            assert cfg.transport.signing.algorithm == "md5"
            assert cfg.transport.signing.secret == "test_secret_123"
        finally:
            os.unlink(tmp_path)
