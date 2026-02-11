# Telegram Channel

Nanobot includes a Telegram channel for chatting with your AI assistant via Telegram.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram
2. Copy the bot token
3. Configure in `~/.nanobot/config.json`:

```json
{
  "channels": {
    "telegram": {
      "token": "YOUR_BOT_TOKEN",
      "allowed_users": ["your_telegram_username"]
    }
  }
}
```

4. Start nanobot:

```bash
nanobot run
```

## Features

### Message Handling
- Text messages
- Photo/image processing
- Voice/audio transcription (via Groq)
- Document attachments

### Markdown to HTML
Messages from the AI are automatically converted from Markdown to Telegram-compatible HTML:

| Markdown | Telegram Display |
|----------|-----------------|
| `**bold**` | **bold** |
| `_italic_` | *italic* |
| `` `code` `` | `code` |
| ```` ```block``` ```` | Code block |
| `[link](url)` | Clickable link |
| `~~strike~~` | ~~strikethrough~~ |
| `- item` | • bullet point |

### Long Message Splitting
Telegram has a 4096 character limit per message. When the AI generates a longer response, nanobot automatically splits it into multiple messages:

1. **Paragraph boundaries** (`\n\n`) — preferred, keeps related content together
2. **Line boundaries** (`\n`) — secondary, avoids mid-sentence cuts
3. **Hard cut** — last resort for very long lines with no breaks

Each chunk is sent with a small delay (300ms) to avoid Telegram rate limits.

### Empty Message Handling
If the AI responds with only tool calls and no text content, empty messages are silently skipped instead of producing errors.

### HTML Fallback
If the Markdown-to-HTML conversion produces invalid HTML for a chunk, nanobot automatically retries that chunk as plain text.

## Security

Use `allowed_users` to restrict who can interact with your bot:

```json
{
  "channels": {
    "telegram": {
      "token": "...",
      "allowed_users": ["alice", "bob"]
    }
  }
}
```

When empty, all users can interact with the bot.
