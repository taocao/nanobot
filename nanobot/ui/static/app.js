/**
 * nanobot Web UI - Main Application
 */

// ============================================================================
// State
// ============================================================================

const state = {
    config: null,
    sessions: [],
    currentSession: 'ui:default',
    ws: null,
    isProcessing: false,
};

// ============================================================================
// API Functions
// ============================================================================

const api = {
    async getConfig() {
        const res = await fetch('/api/config');
        return res.json();
    },

    async saveConfig(config) {
        const res = await fetch('/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config }),
        });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Failed to save config');
        }
        return res.json();
    },

    async getSessions() {
        const res = await fetch('/api/sessions');
        return res.json();
    },

    async getSession(key) {
        const res = await fetch(`/api/sessions/${encodeURIComponent(key)}`);
        return res.json();
    },

    async deleteSession(key) {
        const res = await fetch(`/api/sessions/${encodeURIComponent(key)}`, {
            method: 'DELETE',
        });
        return res.json();
    },

    async getStatus() {
        const res = await fetch('/api/status');
        return res.json();
    },

    async chat(message, sessionId) {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, session_id: sessionId }),
        });
        return res.json();
    },
};

// ============================================================================
// WebSocket
// ============================================================================

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/chat`;

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        console.log('WebSocket connected');
        updateStatus(true);
    };

    state.ws.onclose = () => {
        console.log('WebSocket disconnected');
        updateStatus(false);
        // Reconnect after delay
        setTimeout(connectWebSocket, 3000);
    };

    state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    state.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };
}

function handleWebSocketMessage(data) {
    const toolProgress = document.getElementById('tool-progress');
    const toolStatus = document.getElementById('tool-status');

    switch (data.type) {
        case 'status':
            toolProgress.classList.remove('hidden');
            toolStatus.textContent = data.content;
            break;

        case 'tools':
            toolProgress.classList.remove('hidden');
            toolStatus.textContent = data.content;
            break;

        case 'tool_result':
            // Show brief tool result
            toolStatus.textContent = `${data.tool}: Done`;
            break;

        case 'response':
            // Hide progress, add message
            toolProgress.classList.add('hidden');
            addMessage('assistant', data.content);
            state.isProcessing = false;
            updateSendButton();
            break;

        case 'error':
            toolProgress.classList.add('hidden');
            addMessage('assistant', `Error: ${data.content}`);
            state.isProcessing = false;
            updateSendButton();
            break;
    }
}

function sendMessage(message) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        alert('Not connected. Please wait...');
        return;
    }

    state.isProcessing = true;
    updateSendButton();

    addMessage('user', message);

    // Clear welcome message if present
    const welcome = document.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    state.ws.send(JSON.stringify({
        message,
        session_id: state.currentSession,
    }));
}

// ============================================================================
// UI Functions
// ============================================================================

function updateStatus(connected) {
    const dot = document.querySelector('.status-dot');
    const text = document.getElementById('status-text');

    if (connected) {
        dot.classList.add('connected');
        text.textContent = 'Connected';
    } else {
        dot.classList.remove('connected');
        text.textContent = 'Disconnected';
    }
}

function updateSendButton() {
    const btn = document.getElementById('send-btn');
    btn.disabled = state.isProcessing;
    btn.querySelector('span').textContent = state.isProcessing ? 'Processing...' : 'Send';
}

function addMessage(role, content) {
    const container = document.getElementById('chat-messages');
    const message = document.createElement('div');
    message.className = `message ${role}`;

    const avatar = role === 'user' ? '👤' : '🐈';

    // Format content (basic markdown-like formatting)
    const formattedContent = formatContent(content);

    message.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">${formattedContent}</div>
    `;

    container.appendChild(message);
    container.scrollTop = container.scrollHeight;
}

function formatContent(content) {
    // Escape HTML
    let safe = content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Code blocks
    safe = safe.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');

    // Inline code
    safe = safe.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold
    safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    // Newlines
    safe = safe.replace(/\n/g, '<br>');

    return safe;
}

// ============================================================================
// Tab Navigation
// ============================================================================

function initTabs() {
    const navItems = document.querySelectorAll('.nav-item');

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const tabId = item.dataset.tab;

            // Update nav
            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');

            // Update content
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.getElementById(`${tabId}-tab`).classList.add('active');

            // Load tab data
            if (tabId === 'history') loadSessions();
            if (tabId === 'config') loadConfig();
        });
    });
}

// ============================================================================
// Chat Tab
// ============================================================================

function initChat() {
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const newSessionBtn = document.getElementById('new-session-btn');

    // Auto-resize textarea
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 200) + 'px';
    });

    // Send on Enter (Shift+Enter for newline)
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    sendBtn.addEventListener('click', handleSend);

    newSessionBtn.addEventListener('click', () => {
        const id = `ui:session_${Date.now()}`;
        state.currentSession = id;

        // Add to select
        const select = document.getElementById('session-select');
        const option = document.createElement('option');
        option.value = id;
        option.textContent = `Session ${Date.now()}`;
        select.appendChild(option);
        select.value = id;

        // Clear chat
        document.getElementById('chat-messages').innerHTML = `
            <div class="welcome-message">
                <div class="welcome-icon">🐈</div>
                <h2>New Session</h2>
                <p>Ready to chat!</p>
            </div>
        `;
    });

    document.getElementById('session-select').addEventListener('change', (e) => {
        state.currentSession = e.target.value;
        loadSessionMessages(e.target.value);
    });
}

function handleSend() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();

    if (!message || state.isProcessing) return;

    sendMessage(message);
    input.value = '';
    input.style.height = 'auto';
}

async function loadSessionMessages(sessionId) {
    try {
        const session = await api.getSession(sessionId);
        const container = document.getElementById('chat-messages');
        container.innerHTML = '';

        if (session.messages && session.messages.length > 0) {
            session.messages.forEach(msg => {
                if (msg.role === 'user' || msg.role === 'assistant') {
                    addMessage(msg.role, msg.content);
                }
            });
        } else {
            container.innerHTML = `
                <div class="welcome-message">
                    <div class="welcome-icon">🐈</div>
                    <h2>Welcome to nanobot!</h2>
                    <p>I'm your ultra-lightweight AI assistant. Ask me anything!</p>
                </div>
            `;
        }
    } catch (e) {
        console.error('Failed to load session:', e);
    }
}

// ============================================================================
// History Tab
// ============================================================================

async function loadSessions() {
    const list = document.getElementById('history-list');
    const detail = document.getElementById('history-detail');

    list.classList.remove('hidden');
    detail.classList.add('hidden');

    try {
        const data = await api.getSessions();
        state.sessions = data.sessions;

        if (state.sessions.length === 0) {
            list.innerHTML = '<p class="loading-text">No sessions yet. Start chatting!</p>';
            return;
        }

        list.innerHTML = state.sessions.map(s => `
            <div class="session-card" data-key="${s.key}">
                <div class="session-info">
                    <h3>${s.key}</h3>
                    <p>Last updated: ${formatDate(s.updated_at)}</p>
                </div>
                <span>→</span>
            </div>
        `).join('');

        // Add click handlers
        list.querySelectorAll('.session-card').forEach(card => {
            card.addEventListener('click', () => {
                showSessionDetail(card.dataset.key);
            });
        });
    } catch (e) {
        list.innerHTML = '<p class="loading-text">Error loading sessions</p>';
    }
}

async function showSessionDetail(key) {
    const list = document.getElementById('history-list');
    const detail = document.getElementById('history-detail');
    const keyEl = document.getElementById('history-session-key');
    const messages = document.getElementById('history-messages');

    list.classList.add('hidden');
    detail.classList.remove('hidden');
    keyEl.textContent = key;

    try {
        const session = await api.getSession(key);

        messages.innerHTML = session.messages.map(msg => {
            if (msg.role === 'user' || msg.role === 'assistant') {
                const avatar = msg.role === 'user' ? '👤' : '🐈';
                return `
                    <div class="message ${msg.role}">
                        <div class="message-avatar">${avatar}</div>
                        <div class="message-content">${formatContent(msg.content)}</div>
                    </div>
                `;
            }
            return '';
        }).join('');
    } catch (e) {
        messages.innerHTML = '<p class="loading-text">Error loading session</p>';
    }

    // Delete button
    document.getElementById('delete-session-btn').onclick = async () => {
        if (confirm('Delete this session?')) {
            await api.deleteSession(key);
            loadSessions();
        }
    };
}

function formatDate(isoString) {
    if (!isoString) return 'Unknown';
    return new Date(isoString).toLocaleString();
}

document.getElementById('back-to-list-btn')?.addEventListener('click', loadSessions);
document.getElementById('refresh-history-btn')?.addEventListener('click', loadSessions);

// ============================================================================
// Config Tab
// ============================================================================

async function loadConfig() {
    try {
        state.config = await api.getConfig();
        renderConfig();
    } catch (e) {
        console.error('Failed to load config:', e);
    }
}

function renderConfig() {
    const config = state.config;

    // Providers
    const providersGrid = document.getElementById('providers-config');
    providersGrid.innerHTML = '';

    const providers = config.providers || {};
    Object.entries(providers).forEach(([name, providerConfig]) => {
        const card = document.createElement('div');
        card.className = 'config-card';
        card.innerHTML = `
            <h4>${name}</h4>
            <div class="config-fields">
                <div class="config-field">
                    <label>API Key</label>
                    <input type="password" 
                           data-path="providers.${name}.apiKey" 
                           value="${providerConfig.apiKey || ''}"
                           placeholder="Enter API key">
                </div>
                ${providerConfig.apiBase !== undefined ? `
                <div class="config-field">
                    <label>API Base</label>
                    <input type="text" 
                           data-path="providers.${name}.apiBase" 
                           value="${providerConfig.apiBase || ''}"
                           placeholder="Optional">
                </div>
                ` : ''}
            </div>
        `;
        providersGrid.appendChild(card);
    });

    // Agent defaults
    const agentConfig = document.getElementById('agent-config');
    const defaults = config.agents?.defaults || {};
    agentConfig.innerHTML = `
        <div class="config-field">
            <label>Model</label>
            <input type="text" 
                   data-path="agents.defaults.model" 
                   value="${defaults.model || 'anthropic/claude-opus-4-5'}">
        </div>
        <div class="config-field">
            <label>Workspace</label>
            <input type="text" 
                   data-path="agents.defaults.workspace" 
                   value="${defaults.workspace || '~/.nanobot/workspace'}">
        </div>
        <div class="config-field">
            <label>Max Tokens</label>
            <input type="number" 
                   data-path="agents.defaults.maxTokens" 
                   value="${defaults.maxTokens || 8192}">
        </div>
        <div class="config-field">
            <label>Temperature</label>
            <input type="number" 
                   step="0.1"
                   data-path="agents.defaults.temperature" 
                   value="${defaults.temperature || 0.7}">
        </div>
    `;

    // Tools
    const toolsConfig = document.getElementById('tools-config');
    const tools = config.tools || {};
    toolsConfig.innerHTML = `
        <div class="config-field">
            <label>Brave Search API Key</label>
            <input type="password" 
                   data-path="tools.web.search.apiKey" 
                   value="${tools.web?.search?.apiKey || ''}"
                   placeholder="Optional, for web search">
        </div>
        <div class="config-field">
            <label>Shell Timeout (seconds)</label>
            <input type="number" 
                   data-path="tools.exec.timeout" 
                   value="${tools.exec?.timeout || 60}">
        </div>
    `;

    // Raw JSON
    const rawEditor = document.getElementById('raw-config-editor');
    rawEditor.value = JSON.stringify(config, null, 2);
}

async function saveConfig() {
    try {
        // Collect values from form
        const inputs = document.querySelectorAll('[data-path]');
        const config = JSON.parse(JSON.stringify(state.config)); // Deep clone

        inputs.forEach(input => {
            const path = input.dataset.path.split('.');
            let obj = config;

            // Navigate to parent
            for (let i = 0; i < path.length - 1; i++) {
                if (!obj[path[i]]) obj[path[i]] = {};
                obj = obj[path[i]];
            }

            // Set value
            const key = path[path.length - 1];
            let value = input.value;

            if (input.type === 'number') {
                value = parseFloat(value);
            }

            obj[key] = value;
        });

        await api.saveConfig(config);
        state.config = config;
        alert('Configuration saved!');
    } catch (e) {
        alert(`Failed to save: ${e.message}`);
    }
}

function initConfig() {
    document.getElementById('save-config-btn').addEventListener('click', saveConfig);
    document.getElementById('reload-config-btn').addEventListener('click', loadConfig);

    // Toggle raw JSON section
    document.querySelector('.toggle-section')?.addEventListener('click', function () {
        this.classList.toggle('open');
        this.nextElementSibling.classList.toggle('hidden');
    });
}

// ============================================================================
// Initialize
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initChat();
    initConfig();

    // Load status
    api.getStatus().then(status => {
        console.log('nanobot status:', status);
    });

    // Connect WebSocket
    connectWebSocket();

    // Load initial session
    loadSessionMessages(state.currentSession);
});
