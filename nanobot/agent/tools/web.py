"""Web tools: web_search and web_fetch."""

import html
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool

# Shared constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks

# Domains that already return clean markdown/text — skip readability processing
MARKDOWN_PROXY_DOMAINS = {"r.jina.ai"}

# Content quality thresholds for SPA/JS-rendered site detection
_MIN_MEANINGFUL_CHARS = 200  # Extracted text shorter than this is suspicious
_MIN_HTML_SIZE_FOR_RATIO_CHECK = 5000  # Only check ratio if raw HTML is this big
_MIN_TEXT_TO_HTML_RATIO = 0.05  # 5%: if extracted text is < 5% of HTML, likely SPA


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL: must be http(s) with valid domain."""
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


def _is_markdown_proxy(url: str) -> bool:
    """Check if the URL is a known markdown-proxy that returns clean text."""
    try:
        host = urlparse(url).netloc.lower()
        return host in MARKDOWN_PROXY_DOMAINS
    except Exception:
        return False


def _is_low_quality(extracted_text: str, raw_html: str) -> bool:
    """Detect if readability extraction got very little useful content (SPA/JS site)."""
    text_len = len(extracted_text.strip())
    html_len = len(raw_html)
    if text_len < _MIN_MEANINGFUL_CHARS and html_len > _MIN_HTML_SIZE_FOR_RATIO_CHECK:
        return True
    if html_len > _MIN_HTML_SIZE_FOR_RATIO_CHECK and text_len / html_len < _MIN_TEXT_TO_HTML_RATIO:
        return True
    return False


def _format_error(url: str, error: str, suggestions: list[str] | None = None) -> str:
    """Format a human-readable error message for the LLM."""
    lines = [f"❌ web_fetch failed for {url}", f"Error: {error}"]
    hints = suggestions or [
        "Try again later",
        "Use web_search to find cached/alternative content",
        "Check if the URL is accessible",
    ]
    lines.append("Suggestions:")
    for hint in hints:
        lines.append(f"  - {hint}")
    return "\n".join(lines)


def _format_http_error(url: str, status: int) -> str:
    """Format an HTTP error with actionable advice based on status code."""
    reasons = {
        401: "Unauthorized — the page requires authentication",
        403: "Forbidden — the server blocked the request (bot protection or paywall)",
        404: "Not found — the page does not exist or the URL is incorrect",
        429: "Rate limited — too many requests",
        500: "Server error — the site is having problems",
        502: "Bad gateway — upstream server failed",
        503: "Service unavailable — the site is temporarily down",
    }
    reason = reasons.get(status, f"HTTP {status}")
    suggestions = []
    if status == 403:
        suggestions = [
            "Try fetching via a reader proxy: https://r.jina.ai/<original-url>",
            "Use web_search to find the content from a cache or mirror",
            "Use the summarize skill if available",
        ]
    elif status == 404:
        suggestions = [
            "Double-check the URL for typos",
            "Use web_search to find the correct page",
        ]
    elif status == 429:
        suggestions = ["Wait a moment and try again"]
    return _format_error(url, reason, suggestions if suggestions else None)


class WebSearchTool(Tool):
    """Search the web using Brave Search API."""
    
    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Results (1-10)", "minimum": 1, "maximum": 10}
        },
        "required": ["query"]
    }
    
    def __init__(self, api_key: str | None = None, max_results: int = 5):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self.max_results = max_results
    
    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        if not self.api_key:
            logger.warning("web_search called but BRAVE_API_KEY not configured")
            return (
                "❌ web_search failed: BRAVE_API_KEY not configured.\n"
                "Suggestions:\n"
                "  - Set BRAVE_API_KEY in your config or environment\n"
                "  - Use web_fetch directly if you already have a URL"
            )
        
        try:
            n = min(max(count or self.max_results, 1), 10)
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": n},
                    headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
                    timeout=10.0
                )
                r.raise_for_status()
            
            results = r.json().get("web", {}).get("results", [])
            if not results:
                return f"No results for: {query}"
            
            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            logger.debug(f"web_search returned {len(results)} results for '{query}'")
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            logger.warning(f"web_search HTTP error: {e.response.status_code} for query '{query}'")
            return f"❌ web_search failed for query '{query}': HTTP {e.response.status_code}"
        except httpx.TimeoutException:
            logger.warning(f"web_search timed out for query '{query}'")
            return f"❌ web_search timed out for query '{query}'. Try again later."
        except Exception as e:
            logger.warning(f"web_search error for query '{query}': {e}")
            return f"❌ web_search failed for query '{query}': {e}"


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""
    
    name = "web_fetch"
    description = (
        "Fetch URL and extract readable content (HTML → markdown/text). "
        "For r.jina.ai URLs, returns the clean markdown directly without re-processing."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100}
        },
        "required": ["url"]
    }
    
    def __init__(self, max_chars: int = 50000):
        self.max_chars = max_chars
    
    async def execute(self, url: str, extractMode: str = "markdown", maxChars: int | None = None, **kwargs: Any) -> str:
        max_chars = maxChars or self.max_chars

        # Validate URL before fetching
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            msg = _format_error(url, f"URL validation failed: {error_msg}", [
                "Check that the URL starts with http:// or https://",
                "Ensure the URL has a valid domain",
            ])
            logger.warning(f"web_fetch URL validation failed: {error_msg} for '{url}'")
            return msg

        is_proxy = _is_markdown_proxy(url)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=30.0
            ) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = _format_http_error(url, e.response.status_code)
            logger.warning(f"web_fetch HTTP {e.response.status_code} for '{url}'")
            return msg
        except httpx.TimeoutException:
            msg = _format_error(url, "Connection timed out after 30s", [
                "Try again later",
                "The site may be slow or blocking automated requests",
                "Try fetching via https://r.jina.ai/<url> for better access",
            ])
            logger.warning(f"web_fetch timed out for '{url}'")
            return msg
        except httpx.TooManyRedirects:
            msg = _format_error(url, f"Too many redirects (>{MAX_REDIRECTS})", [
                "The URL may be in a redirect loop",
                "Try the final destination URL directly",
            ])
            logger.warning(f"web_fetch too many redirects for '{url}'")
            return msg
        except Exception as e:
            msg = _format_error(url, str(e))
            logger.warning(f"web_fetch connection error for '{url}': {e}")
            return msg

        ctype = r.headers.get("content-type", "")

        # ── Markdown-proxy or plain text responses → return as-is ──
        if is_proxy or "text/markdown" in ctype or (
            "text/plain" in ctype and not ("text/html" in ctype)
        ):
            text = r.text.strip()
            extractor = "passthrough"
            logger.debug(f"web_fetch passthrough for '{url}' (proxy={is_proxy}, ctype={ctype})")
        # ── JSON ──
        elif "application/json" in ctype:
            text, extractor = json.dumps(r.json(), indent=2), "json"
        # ── HTML → extract with readability ──
        elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
            text, extractor = self._extract_html(r.text, extractMode)

            # Quality check — SPA/JS-rendered sites produce garbled output
            if _is_low_quality(text, r.text):
                logger.info(
                    f"web_fetch low-quality extraction for '{url}' "
                    f"({len(text)} chars from {len(r.text)} HTML), "
                    "retrying via r.jina.ai"
                )
                jina_text = await self._fetch_via_jina(url)
                if jina_text:
                    text, extractor = jina_text, "jina-fallback"
                else:
                    logger.warning(f"Jina fallback also failed for '{url}'")
        else:
            text, extractor = r.text, "raw"

        # Surface llms.txt link if present (e.g. Mintlify docs)
        llms_txt_url = None
        link_header = r.headers.get("link", "")
        if "llms-txt" in link_header or "llms.txt" in link_header:
            # Parse e.g. </llms.txt>; rel="llms-txt"
            import re as _re
            m = _re.search(r'<([^>]+)>;\s*rel=["\']?llms-txt', link_header)
            if m:
                path = m.group(1)
                if path.startswith("/"):
                    parsed = urlparse(url)
                    llms_txt_url = f"{parsed.scheme}://{parsed.netloc}{path}"
                else:
                    llms_txt_url = path

        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]

        result_dict: dict[str, Any] = {
            "url": url, "finalUrl": str(r.url), "status": r.status_code,
            "extractor": extractor, "truncated": truncated,
            "length": len(text), "text": text,
        }
        if llms_txt_url:
            result_dict["llms_txt"] = llms_txt_url

        result = json.dumps(result_dict)
        logger.debug(
            f"web_fetch OK: {url} → {extractor}, {len(text)} chars, "
            f"status={r.status_code}, truncated={truncated}"
        )
        return result

    async def _fetch_via_jina(self, original_url: str) -> str | None:
        """Fetch content via r.jina.ai as fallback for SPA/JS-rendered sites."""
        jina_url = f"https://r.jina.ai/{original_url}"
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=30.0,
            ) as client:
                r = await client.get(jina_url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/plain",
                })
                r.raise_for_status()
            text = r.text.strip()
            if text and len(text) > _MIN_MEANINGFUL_CHARS:
                logger.debug(f"Jina fallback OK for '{original_url}': {len(text)} chars")
                return text
            return None
        except Exception as e:
            logger.warning(f"Jina fallback failed for '{original_url}': {e}")
            return None

    def _extract_html(self, raw_html: str, mode: str) -> tuple[str, str]:
        """Extract content from HTML using readability, with graceful fallback."""
        try:
            from readability import Document
        except ImportError:
            logger.warning(
                "readability-lxml not installed — falling back to basic tag-stripping. "
                "Install with: pip install readability-lxml"
            )
            text = _strip_tags(raw_html)
            return _normalize(text), "fallback-strip"

        try:
            doc = Document(raw_html)
            content = (
                self._to_markdown(doc.summary()) if mode == "markdown"
                else _strip_tags(doc.summary())
            )
            text = f"# {doc.title()}\n\n{content}" if doc.title() else content
            return text, "readability"
        except Exception as e:
            logger.warning(f"readability extraction failed: {e}, falling back to tag-strip")
            text = _strip_tags(raw_html)
            return _normalize(text), "fallback-strip"
    
    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        # Convert links, headings, lists before stripping tags
        text = re.sub(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
                      lambda m: f'[{_strip_tags(m[2])}]({m[1]})', html, flags=re.I)
        text = re.sub(r'<h([1-6])[^>]*>([\s\S]*?)</h\1>',
                      lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n', text, flags=re.I)
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
