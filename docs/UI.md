# Nanobot Web UI

A web-based interface for nanobot that provides interactive chat, configuration management, and session history browsing.

## Features

### 💬 Chat Interface
- Real-time streaming responses via WebSocket
- Tool execution progress display
- Continuous conversation with context preservation
- Session management (create/switch sessions)

### ⚙️ Configuration Editor
- Visual JSON editor for `~/.nanobot/config.json`
- Grouped sections (Providers, Agent Defaults, Tools)
- Show/hide API key fields
- Validation before saving

### 📜 History Browser
- List all conversation sessions with timestamps
- View full conversation history per session
- Delete old sessions

## Installation

```bash
# Install with UI dependencies
pip install nanobot-ai[ui]

# Or from source
pip install -e ".[ui]"
```

## Usage

```bash
# Start the UI server
nanobot ui --port 8080

# With custom host (for network access)
nanobot ui --host 0.0.0.0 --port 8080
```

Then open http://localhost:8080 in your browser.

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Get current configuration |
| `/api/config` | PUT | Update configuration |
| `/api/sessions` | GET | List all sessions |
| `/api/sessions/{key}` | GET | Get session history |
| `/api/sessions/{key}` | DELETE | Delete session |
| `/api/status` | GET | Get nanobot status |
| `/api/chat` | POST | Send message (sync) |
| `/ws/chat` | WebSocket | Streaming chat |

## Architecture

```
nanobot/ui/
├── __init__.py       # Module init
├── api.py            # FastAPI application
└── static/
    ├── index.html    # Main UI page
    ├── style.css     # Dark theme styling
    └── app.js        # Client-side logic
```

## Screenshots

The UI features a modern dark theme with:
- Sidebar navigation (Chat, History, Config tabs)
- Gradient accent colors (cyan to purple)
- Glassmorphism effects
- Responsive layout for mobile

## Dependencies

The UI uses optional dependencies to keep the core nanobot lightweight:

- `fastapi>=0.100.0` - Web framework
- `uvicorn>=0.23.0` - ASGI server
