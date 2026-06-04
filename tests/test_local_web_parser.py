"""
Tests for the local fetch_url WebParser.
本地 WebParser 测试：抽取、fallback、短内容提示和安全边界。
"""

from __future__ import annotations

import os
import socket
import sys
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.local_web_parser import LocalWebParser, LocalWebParserError


@pytest.fixture(autouse=True)
def clear_local_webparser_cache():
    LocalWebParser._fetch_cache.clear()


class TestLocalExtraction:

    def test_trafilatura_success(self, monkeypatch):
        monkeypatch.setattr("tools.local_web_parser.trafilatura.extract", lambda *args, **kwargs: "# Example\n\nReadable body")

        result = LocalWebParser().extract_content("<html><title>T</title><body>ignored</body></html>", "https://example.com")

        assert result.content.startswith("# Example")
        assert result.title == "T"
        assert result.backend == "local:trafilatura"

    def test_trafilatura_empty_uses_bs4_fallback(self, monkeypatch):
        monkeypatch.setattr("tools.local_web_parser.trafilatura.extract", lambda *args, **kwargs: "")
        html = """
        <html><title>Fallback</title><body>
          <nav>navigation noise</nav>
          <main><h1>Article</h1><p>This paragraph should survive.</p></main>
          <footer>footer noise</footer>
        </body></html>
        """

        result = LocalWebParser().extract_content(html, "https://example.com")

        assert "Article" in result.content
        assert "This paragraph should survive" in result.content
        assert "navigation noise" not in result.content
        assert result.backend == "local:bs4-markdownify"

    def test_text_format_uses_plain_text_fallback(self, monkeypatch):
        monkeypatch.setattr("tools.local_web_parser.trafilatura.extract", lambda *args, **kwargs: "")
        html = "<html><body><main><h1>Title</h1><p>Plain text body.</p></main></body></html>"

        result = LocalWebParser().extract_content(html, "https://example.com", format_type="text")

        assert "Title" in result.content
        assert "#" not in result.content
        assert result.backend == "local:bs4-text"

    def test_json_content_is_returned_without_html_extraction(self):
        result = LocalWebParser().extract_content('{"slideshow":{"title":"Sample Slide Show"}}', "https://httpbin.org/json")

        assert '"slideshow"' in result.content
        assert '"title": "Sample Slide Show"' in result.content
        assert result.backend == "local:raw-json"

    def test_plain_text_content_is_returned_without_html_extraction(self):
        result = LocalWebParser().extract_content("city,population\nShanghai,24870000", "https://example.com/data.csv")

        assert "city,population" in result.content
        assert "Shanghai" in result.content
        assert result.backend == "local:raw-text"

    def test_short_content_warning(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_MIN_CONTENT_LENGTH", 120)
        content = LocalWebParser().add_short_content_warning("short")

        assert "short" in content
        assert "Warning:" in content
        assert "not treat this as complete page content" in content

    @pytest.mark.asyncio
    async def test_browser_fallback_uses_rendered_html_when_static_is_short(self, monkeypatch):
        import config as cfg

        async def fake_fetch_html(self, url):
            return "<html><body>short</body></html>", url

        async def fake_fetch_rendered_html(self, url):
            return "<html><title>Rendered</title><body><main>Rendered content is now long enough for extraction.</main></body></html>", url

        def fake_extract(html, *args, **kwargs):
            if "Rendered content" in html:
                return "Rendered content is now long enough for extraction."
            return "tiny"

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_MIN_CONTENT_LENGTH", 40)
        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_BROWSER_FALLBACK", True)
        monkeypatch.setattr(LocalWebParser, "fetch_html", fake_fetch_html)
        monkeypatch.setattr(LocalWebParser, "fetch_rendered_html", fake_fetch_rendered_html)
        monkeypatch.setattr("tools.local_web_parser.trafilatura.extract", fake_extract)

        result = await LocalWebParser().fetch("https://example.com")

        assert "Rendered content" in result.content
        assert result.backend == "local:playwright+trafilatura"


class TestFetchHtml:

    @pytest.mark.asyncio
    async def test_non_html_content_type_rejected(self, monkeypatch):
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", lambda **kwargs: _FakeClient("application/pdf", [b"%PDF"]))

        with pytest.raises(LocalWebParserError, match="unsupported content-type"):
            await LocalWebParser().fetch_html("https://example.com/file.pdf")

    @pytest.mark.asyncio
    async def test_json_content_type_allowed(self, monkeypatch):
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", lambda **kwargs: _FakeClient("application/json", [b'{"ok":true}']))

        html, final_url = await LocalWebParser().fetch_html("https://example.com/data.json")

        assert html == '{"ok":true}'
        assert final_url == "https://example.com/final"

    @pytest.mark.asyncio
    async def test_structured_json_content_type_allowed(self, monkeypatch):
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", lambda **kwargs: _FakeClient("application/problem+json", [b'{"error":"bad"}']))

        html, final_url = await LocalWebParser().fetch_html("https://example.com/problem")

        assert html == '{"error":"bad"}'
        assert final_url == "https://example.com/final"

    @pytest.mark.asyncio
    async def test_text_csv_content_type_allowed(self, monkeypatch):
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", lambda **kwargs: _FakeClient("text/csv", [b"city,population\nShanghai,24870000"]))

        html, final_url = await LocalWebParser().fetch_html("https://example.com/data.csv")

        assert "Shanghai" in html
        assert final_url == "https://example.com/final"

    @pytest.mark.asyncio
    async def test_too_large_response_rejected(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_MAX_BYTES", 4)
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", lambda **kwargs: _FakeClient("text/html", [b"abc", b"def"]))

        with pytest.raises(LocalWebParserError, match="response too large"):
            await LocalWebParser().fetch_html("https://example.com")

    @pytest.mark.asyncio
    async def test_fetch_html_success(self, monkeypatch):
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", lambda **kwargs: _FakeClient("text/html", [b"<html>ok</html>"]))

        html, final_url = await LocalWebParser().fetch_html("https://example.com")

        assert html == "<html>ok</html>"
        assert final_url == "https://example.com/final"

    @pytest.mark.asyncio
    async def test_fetch_html_uses_lru_cache_for_repeated_url(self, monkeypatch):
        import config as cfg

        calls = {"count": 0}

        def make_client(**kwargs):
            calls["count"] += 1
            return _FakeClient("text/html", [f"<html>ok {calls['count']}</html>".encode()])

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_CACHE_SIZE", 4)
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", make_client)

        first = await LocalWebParser().fetch_html("https://example.com")
        second = await LocalWebParser().fetch_html("https://example.com")

        assert first == second
        assert calls["count"] == 1

    @pytest.mark.asyncio
    async def test_fetch_html_cache_can_be_disabled(self, monkeypatch):
        import config as cfg

        calls = {"count": 0}

        def make_client(**kwargs):
            calls["count"] += 1
            return _FakeClient("text/html", [f"<html>ok {calls['count']}</html>".encode()])

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_CACHE_SIZE", 0)
        monkeypatch.setattr(LocalWebParser, "_ensure_public_url", _fake_ensure_public_url)
        monkeypatch.setattr("tools.local_web_parser.httpx.AsyncClient", make_client)

        first = await LocalWebParser().fetch_html("https://example.com")
        second = await LocalWebParser().fetch_html("https://example.com")

        assert first != second
        assert calls["count"] == 2


class TestUrlSafety:

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self):
        with pytest.raises(ValueError, match="http and https"):
            await LocalWebParser()._ensure_public_url("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_rejects_localhost(self):
        with pytest.raises(ValueError, match="blocks localhost/private"):
            await LocalWebParser()._ensure_public_url("http://localhost:8000")

    @pytest.mark.asyncio
    async def test_rejects_private_ip(self):
        with pytest.raises(ValueError, match="blocks localhost/private"):
            await LocalWebParser()._ensure_public_url("http://169.254.169.254/latest/meta-data")

    @pytest.mark.asyncio
    async def test_allows_benchmark_proxy_dns_for_public_hostname(self, monkeypatch):
        monkeypatch.setattr(
            "tools.local_web_parser.socket.getaddrinfo",
            lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.91", 443))],
        )

        parsed = await LocalWebParser()._ensure_public_url("https://example.com")

        assert parsed.hostname == "example.com"


class _FakeClient:
    def __init__(self, content_type: str, chunks: list[bytes]):
        self._content_type = content_type
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str):
        return _FakeStream(self._content_type, self._chunks)


class _FakeStream:
    def __init__(self, content_type: str, chunks: list[bytes]):
        self.response = _FakeResponse(content_type, chunks)

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeResponse:
    def __init__(self, content_type: str, chunks: list[bytes]):
        self.headers = {"content-type": content_type}
        self.url = "https://example.com/final"
        self.encoding = "utf-8"
        self._chunks = chunks

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


async def _fake_ensure_public_url(self, url: str):
    return urlsplit(url)
