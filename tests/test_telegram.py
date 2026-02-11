"""Tests for Telegram channel message handling.

Tests cover:
- Markdown to Telegram HTML conversion
- Long message splitting (paragraph, line, hard-cut boundaries)
- Empty message handling
- Send method with chunking and fallback logic
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.channels.telegram import TelegramChannel, _markdown_to_telegram_html


# ========================================
# Tests for _markdown_to_telegram_html()
# ========================================

class TestMarkdownToTelegramHtml:
    """Test markdown to Telegram HTML conversion."""

    def test_empty_string(self):
        assert _markdown_to_telegram_html("") == ""

    def test_plain_text(self):
        assert _markdown_to_telegram_html("Hello world") == "Hello world"

    def test_bold(self):
        result = _markdown_to_telegram_html("**bold text**")
        assert "<b>bold text</b>" in result

    def test_italic(self):
        result = _markdown_to_telegram_html("_italic text_")
        assert "<i>italic text</i>" in result

    def test_inline_code(self):
        result = _markdown_to_telegram_html("use `print()` here")
        assert "<code>print()</code>" in result

    def test_code_block(self):
        result = _markdown_to_telegram_html("```python\nprint('hi')\n```")
        assert "<pre><code>" in result
        assert "print(&#x27;hi&#x27;)" in result or "print('hi')" in result

    def test_link(self):
        result = _markdown_to_telegram_html("[click](https://example.com)")
        assert '<a href="https://example.com">click</a>' in result

    def test_strikethrough(self):
        result = _markdown_to_telegram_html("~~removed~~")
        assert "<s>removed</s>" in result

    def test_bullet_list(self):
        result = _markdown_to_telegram_html("- item one\n- item two")
        assert "• item one" in result
        assert "• item two" in result

    def test_headers_stripped(self):
        result = _markdown_to_telegram_html("# My Title")
        assert "#" not in result
        assert "My Title" in result

    def test_html_entities_escaped(self):
        result = _markdown_to_telegram_html("a < b & c > d")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_code_block_html_escaped(self):
        result = _markdown_to_telegram_html("```\n<script>alert('xss')</script>\n```")
        assert "&lt;script&gt;" in result

    def test_blockquote(self):
        result = _markdown_to_telegram_html("> quoted text")
        assert "quoted text" in result
        assert ">" not in result or "&gt;" in result


# ========================================
# Tests for TelegramChannel._split_message()
# ========================================

class TestSplitMessage:
    """Test message splitting logic."""

    def test_short_message_no_split(self):
        """Messages within the limit should return as a single chunk."""
        result = TelegramChannel._split_message("Hello world", max_len=4096)
        assert result == ["Hello world"]

    def test_exact_limit_no_split(self):
        """Message exactly at the limit should not split."""
        text = "x" * 4096
        result = TelegramChannel._split_message(text, max_len=4096)
        assert len(result) == 1
        assert result[0] == text

    def test_split_at_paragraph_boundary(self):
        """Should prefer splitting at paragraph boundaries (double newline)."""
        para1 = "A" * 100
        para2 = "B" * 100
        text = para1 + "\n\n" + para2
        result = TelegramChannel._split_message(text, max_len=150)
        assert len(result) == 2
        assert result[0] == para1
        assert result[1] == para2

    def test_split_at_line_boundary(self):
        """Should split at line boundaries when no paragraph boundary exists."""
        line1 = "A" * 100
        line2 = "B" * 100
        text = line1 + "\n" + line2
        result = TelegramChannel._split_message(text, max_len=150)
        assert len(result) == 2
        assert result[0] == line1
        assert result[1] == line2

    def test_hard_cut_when_no_boundaries(self):
        """Should hard-cut when there are no line boundaries."""
        text = "A" * 200
        result = TelegramChannel._split_message(text, max_len=100)
        assert len(result) == 2
        assert result[0] == "A" * 100
        assert result[1] == "A" * 100

    def test_multiple_chunks(self):
        """Should produce multiple chunks for very long text."""
        # 5 paragraphs, each 100 chars, split at 150
        paras = ["P" * 100 for _ in range(5)]
        text = "\n\n".join(paras)
        result = TelegramChannel._split_message(text, max_len=150)
        assert len(result) == 5
        for chunk in result:
            assert len(chunk) <= 150

    def test_empty_string(self):
        """Empty string should return empty list (filtered by strip check)."""
        result = TelegramChannel._split_message("")
        assert result == [] or result == [""]

    def test_whitespace_only_chunks_filtered(self):
        """Whitespace-only chunks should be filtered out."""
        text = "Hello\n\n\n\n\n\nWorld"
        result = TelegramChannel._split_message(text, max_len=10)
        for chunk in result:
            assert chunk.strip()  # No empty chunks

    def test_no_chunk_exceeds_limit(self):
        """No chunk should ever exceed the max length."""
        text = "Word " * 2000  # ~10000 chars
        result = TelegramChannel._split_message(text, max_len=500)
        for chunk in result:
            assert len(chunk) <= 500

    def test_all_content_preserved(self):
        """Total content length should be preserved across chunks (minus stripped whitespace)."""
        words = [f"word{i}" for i in range(100)]
        text = " ".join(words)
        result = TelegramChannel._split_message(text, max_len=200)
        # All characters should be present (minus stripped whitespace between chunks)
        total_chars = sum(len(c) for c in result)
        assert total_chars >= len(text) - len(result)  # Allow for stripped whitespace

    def test_split_boundary_not_too_early(self):
        """Should not split too close to the start (min 1/4 of max_len)."""
        # A newline very early, then a long run
        text = "Hi\n" + "A" * 200
        result = TelegramChannel._split_message(text, max_len=100)
        # Should NOT split at position 2, should hard-cut instead
        assert len(result[0]) >= 25  # At least 1/4 of 100

    def test_realistic_telegram_message(self):
        """Test with a realistic long AI response."""
        paragraphs = [
            "Here's what I found about your question:\n",
            "The answer involves several key points that I'll explain below.\n",
            "First, let's look at the main concept. " + "Detail " * 50 + "\n",
            "Second, we need to consider the implications. " + "More detail " * 50 + "\n",
            "Finally, here's my recommendation: " + "Conclusion " * 30,
        ]
        text = "\n".join(paragraphs)
        result = TelegramChannel._split_message(text, max_len=500)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 500
            assert chunk.strip()


# ========================================
# Tests for TelegramChannel.send()
# ========================================

class TestTelegramSend:
    """Test the send method with mocked Telegram bot."""

    @pytest.fixture
    def channel(self):
        """Create a TelegramChannel with mocked internals."""
        config = MagicMock()
        config.token = "fake-token"
        config.allowed_users = []
        bus = MagicMock()
        ch = TelegramChannel(config, bus)
        ch._app = MagicMock()
        ch._app.bot = MagicMock()
        ch._app.bot.send_message = AsyncMock()
        return ch

    @pytest.fixture
    def outbound_msg(self):
        """Create a mock OutboundMessage."""
        from nanobot.bus.events import OutboundMessage
        return OutboundMessage(
            channel="telegram",
            chat_id="12345",
            content="Hello, this is a test message."
        )

    @pytest.mark.asyncio
    async def test_send_short_message(self, channel, outbound_msg):
        """Short messages should be sent as a single call."""
        await channel.send(outbound_msg)
        channel._app.bot.send_message.assert_called_once()
        call = channel._app.bot.send_message.call_args
        assert call.kwargs["chat_id"] == 12345
        assert call.kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_empty_message(self, channel):
        """Empty messages should be silently skipped."""
        from nanobot.bus.events import OutboundMessage
        msg = OutboundMessage(channel="telegram", chat_id="12345", content="")
        await channel.send(msg)
        channel._app.bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_whitespace_message(self, channel):
        """Whitespace-only messages should be silently skipped."""
        from nanobot.bus.events import OutboundMessage
        msg = OutboundMessage(channel="telegram", chat_id="12345", content="   \n  ")
        await channel.send(msg)
        channel._app.bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_long_message_splits(self, channel):
        """Long messages should be sent as multiple calls."""
        from nanobot.bus.events import OutboundMessage
        long_text = "\n\n".join(["Paragraph " + str(i) + " " + "x" * 3000 for i in range(3)])
        msg = OutboundMessage(channel="telegram", chat_id="12345", content=long_text)
        await channel.send(msg)
        assert channel._app.bot.send_message.call_count > 1

    @pytest.mark.asyncio
    async def test_send_html_fallback(self, channel, outbound_msg):
        """Should fall back to plain text when HTML parsing fails."""
        channel._app.bot.send_message = AsyncMock(
            side_effect=[Exception("Bad HTML"), None]
        )
        await channel.send(outbound_msg)
        assert channel._app.bot.send_message.call_count == 2
        # Second call should be plain text (no parse_mode)
        second_call = channel._app.bot.send_message.call_args_list[1]
        assert "parse_mode" not in second_call.kwargs

    @pytest.mark.asyncio
    async def test_send_invalid_chat_id(self, channel):
        """Invalid chat_id should log error and not crash."""
        from nanobot.bus.events import OutboundMessage
        msg = OutboundMessage(channel="telegram", chat_id="not-a-number", content="Hi")
        await channel.send(msg)
        channel._app.bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_bot_not_running(self):
        """Should handle case where bot is not initialized."""
        config = MagicMock()
        config.token = "fake"
        config.allowed_users = []
        bus = MagicMock()
        ch = TelegramChannel(config, bus)
        ch._app = None

        from nanobot.bus.events import OutboundMessage
        msg = OutboundMessage(channel="telegram", chat_id="12345", content="Hi")
        # Should not raise
        await ch.send(msg)
