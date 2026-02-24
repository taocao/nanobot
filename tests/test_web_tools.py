"""Tests for web tools: web_search and web_fetch."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nanobot.agent.tools.web import (
    MARKDOWN_PROXY_DOMAINS,
    WebFetchTool,
    WebSearchTool,
    _format_error,
    _format_http_error,
    _is_low_quality,
    _is_markdown_proxy,
    _validate_url,
)


# ──────────────────────── Helpers ────────────────────────


def _make_response(
    text: str = "",
    status_code: int = 200,
    content_type: str = "text/html",
    url: str = "https://example.com",
    json_data: Any = None,
) -> httpx.Response:
    """Build a mock httpx.Response."""
    headers = {"content-type": content_type}
    resp = httpx.Response(
        status_code=status_code,
        headers=headers,
        text=text,
        request=httpx.Request("GET", url),
    )
    if json_data is not None:
        resp._content = json.dumps(json_data).encode()
    return resp


# ──────────────────────── _validate_url ────────────────────────


class TestValidateUrl:
    def test_valid_http(self):
        ok, _ = _validate_url("http://example.com")
        assert ok

    def test_valid_https(self):
        ok, _ = _validate_url("https://example.com/path?q=1")
        assert ok

    def test_invalid_scheme(self):
        ok, msg = _validate_url("ftp://example.com")
        assert not ok
        assert "http" in msg.lower()

    def test_missing_domain(self):
        ok, msg = _validate_url("https://")
        assert not ok


# ──────────────────────── _is_markdown_proxy ────────────────────────


class TestIsMarkdownProxy:
    def test_jina_reader(self):
        assert _is_markdown_proxy("https://r.jina.ai/https://example.com")

    def test_non_proxy(self):
        assert not _is_markdown_proxy("https://example.com")

    def test_bad_url(self):
        assert not _is_markdown_proxy("")


# ──────────────────────── _format_error ────────────────────────


class TestFormatError:
    def test_contains_url_and_error(self):
        msg = _format_error("https://x.com", "boom")
        assert "https://x.com" in msg
        assert "boom" in msg
        assert "❌" in msg

    def test_custom_suggestions(self):
        msg = _format_error("https://x.com", "err", ["do A", "do B"])
        assert "do A" in msg
        assert "do B" in msg


class TestFormatHttpError:
    def test_403_suggests_jina(self):
        msg = _format_http_error("https://x.com", 403)
        assert "r.jina.ai" in msg

    def test_404_suggests_url_check(self):
        msg = _format_http_error("https://x.com", 404)
        assert "typo" in msg.lower() or "check" in msg.lower()


# ──────────────────────── WebSearchTool ────────────────────────


class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_no_api_key(self):
        tool = WebSearchTool(api_key="")
        result = await tool.execute(query="hello")
        assert "BRAVE_API_KEY" in result
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_search_success(self):
        tool = WebSearchTool(api_key="test-key")
        mock_resp = _make_response(
            content_type="application/json",
            json_data={"web": {"results": [
                {"title": "Example", "url": "https://example.com", "description": "A page"}
            ]}},
        )
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(query="test")
        assert "Example" in result

    @pytest.mark.asyncio
    async def test_search_timeout(self):
        tool = WebSearchTool(api_key="test-key")
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(query="test")
        assert "timed out" in result
        assert "❌" in result


# ──────────────────────── WebFetchTool ────────────────────────


class TestWebFetchTool:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        tool = WebFetchTool()
        result = await tool.execute(url="ftp://bad")
        assert "❌" in result
        assert "http" in result.lower()

    @pytest.mark.asyncio
    async def test_jina_passthrough(self):
        """r.jina.ai URLs should return text directly, not re-process HTML."""
        tool = WebFetchTool()
        md_content = "# Hello World\n\nThis is clean markdown."
        mock_resp = _make_response(
            text=md_content,
            content_type="text/plain",
            url="https://r.jina.ai/https://example.com",
        )
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://r.jina.ai/https://example.com")

        data = json.loads(result)
        assert data["extractor"] == "passthrough"
        assert "Hello World" in data["text"]

    @pytest.mark.asyncio
    async def test_text_markdown_content_type_passthrough(self):
        """text/markdown content-type should skip readability."""
        tool = WebFetchTool()
        md = "## Section\nSome markdown content."
        mock_resp = _make_response(text=md, content_type="text/markdown", url="https://example.com/page.md")
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://example.com/page.md")

        data = json.loads(result)
        assert data["extractor"] == "passthrough"

    @pytest.mark.asyncio
    async def test_http_403_error(self):
        tool = WebFetchTool()
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(403, request=request)
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.HTTPStatusError(
                "forbidden", request=request, response=response
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://example.com")

        assert "❌" in result
        assert "r.jina.ai" in result  # suggests proxy

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        tool = WebFetchTool()
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://example.com")

        assert "❌" in result
        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_html_extraction(self):
        """HTML should be extracted with readability."""
        tool = WebFetchTool()
        html_content = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"
        mock_resp = _make_response(text=html_content, content_type="text/html")
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://example.com")

        data = json.loads(result)
        assert data["extractor"] in ("readability", "fallback-strip")
        assert data["status"] == 200

    @pytest.mark.asyncio
    async def test_readability_import_fallback(self):
        """If readability is not installed, fall back to tag-stripping."""
        tool = WebFetchTool()
        html_content = "<html><body><p>Fallback content</p></body></html>"
        mock_resp = _make_response(text=html_content, content_type="text/html")

        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # Simulate readability not installed
            import builtins
            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "readability":
                    raise ImportError("No module named 'readability'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = await tool.execute(url="https://example.com")

        data = json.loads(result)
        assert data["extractor"] == "fallback-strip"
        assert "Fallback content" in data["text"]

    @pytest.mark.asyncio
    async def test_truncation(self):
        tool = WebFetchTool(max_chars=20)
        long_text = "A" * 100
        mock_resp = _make_response(text=long_text, content_type="text/plain")
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://example.com/big.txt")

        data = json.loads(result)
        assert data["truncated"] is True
        assert len(data["text"]) == 20

    @pytest.mark.asyncio
    async def test_error_messages_are_not_json(self):
        """Error messages should be human-readable, NOT JSON blobs."""
        tool = WebFetchTool()
        result = await tool.execute(url="ftp://bad.url")
        # Should not be valid JSON
        try:
            parsed = json.loads(result)
            # If it parses, it should not have an "error" key (old format)
            assert "error" not in parsed, "Error still returned as JSON blob"
        except json.JSONDecodeError:
            pass  # Expected — errors are now plain text
        assert "❌" in result


# ──────────────────────── _is_low_quality (SPA detection) ────────────────────────


class TestIsLowQuality:
    def test_short_text_large_html(self):
        """Short extracted text from large HTML = SPA."""
        assert _is_low_quality("tiny", "x" * 10000)

    def test_ok_text_large_html(self):
        """Reasonable text-to-HTML ratio is fine."""
        assert not _is_low_quality("x" * 2000, "x" * 10000)

    def test_small_html_not_flagged(self):
        """Small HTML pages should not be flagged even with short text."""
        assert not _is_low_quality("hi", "<p>hi</p>")

    def test_ratio_check(self):
        """Text < 5% of HTML = low quality."""
        html = "x" * 20000
        text = "x" * 500  # 2.5% ratio
        assert _is_low_quality(text, html)


class TestSpaFallback:
    @pytest.mark.asyncio
    async def test_low_quality_triggers_jina_fallback(self):
        """SPA site with garbled readability output should trigger jina fallback."""
        tool = WebFetchTool()
        # Simulate a large HTML page with tiny readable content
        big_html = "<html><body>" + "<script>var x = 1;</script>" * 500 + "<p>tiny</p></body></html>"
        jina_md = "# Full Content\n\nThis is the proper markdown from Jina Reader." + "x" * 300

        # First call returns the HTML, second call (jina fallback) returns markdown
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "r.jina.ai" in url:
                return _make_response(text=jina_md, content_type="text/plain", url=url)
            return _make_response(text=big_html, content_type="text/html", url=url)

        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://docs.example.com/page")

        data = json.loads(result)
        assert data["extractor"] == "jina-fallback"
        assert "Full Content" in data["text"]

    @pytest.mark.asyncio
    async def test_llms_txt_header_surfaced(self):
        """If response has Link: llms-txt header, it should appear in result."""
        tool = WebFetchTool()
        html = "<html><body><p>" + "Content here. " * 100 + "</p></body></html>"
        resp = httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "link": '</llms.txt>; rel="llms-txt", </llms-full.txt>; rel="llms-full-txt"',
            },
            text=html,
            request=httpx.Request("GET", "https://docs.example.com/page"),
        )
        with patch("nanobot.agent.tools.web.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await tool.execute(url="https://docs.example.com/page")

        data = json.loads(result)
        assert "llms_txt" in data
        assert data["llms_txt"] == "https://docs.example.com/llms.txt"
