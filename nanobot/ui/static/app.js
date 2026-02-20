/**
 * nanobot Educational UI - JavaScript
 * Shows how agentic AI works step-by-step
 */

// ============================================
// State Management
// ============================================
const state = {
    ws: null,
    config: null,
    currentSession: 'ui:default',
    isProcessing: false,
    debugVisible: true,

    // Metrics
    metrics: {
        model: 'Loading...',
        contextTokens: 0,
        contextLimit: 128000,
        inputTokens: 0,
        outputTokens: 0
    },

    // Processing state
    currentStep: null,
    toolCalls: [],
    messageFlow: [],

    // Auto-refresh polling
    activeTab: 'chat',
    pollTimers: {},
    pollInterval: 5000,  // 5 seconds
};

// ============================================
// API Client
// ============================================
const api = {
    baseUrl: '',

    async getConfig() {
        const res = await fetch(`${this.baseUrl}/api/config`);
        return res.json();
    },

    async saveConfig(config) {
        const res = await fetch(`${this.baseUrl}/api/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config })
        });
        return res.json();
    },

    async getSessions() {
        const res = await fetch(`${this.baseUrl}/api/sessions`);
        return res.json();
    },

    async getSession(key) {
        const res = await fetch(`${this.baseUrl}/api/sessions/${encodeURIComponent(key)}`);
        return res.json();
    },

    async deleteSession(key) {
        const res = await fetch(`${this.baseUrl}/api/sessions/${encodeURIComponent(key)}`, {
            method: 'DELETE'
        });
        return res.json();
    },

    async getStatus() {
        const res = await fetch(`${this.baseUrl}/api/status`);
        return res.json();
    },

    // Logs API
    async getLogs(sessionId = null, limit = 50) {
        const params = new URLSearchParams();
        if (sessionId) params.set('session_id', sessionId);
        params.set('limit', limit);
        const res = await fetch(`${this.baseUrl}/api/logs?${params}`);
        return res.json();
    },

    async getLog(sessionId, logId) {
        const res = await fetch(`${this.baseUrl}/api/logs/${encodeURIComponent(sessionId)}/${encodeURIComponent(logId)}`);
        return res.json();
    },

    async deleteLog(sessionId, logId) {
        const res = await fetch(`${this.baseUrl}/api/logs/${encodeURIComponent(sessionId)}/${encodeURIComponent(logId)}`, {
            method: 'DELETE'
        });
        return res.json();
    }
};

// ============================================
// WebSocket Connection
// ============================================
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/chat`;

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        updateStatus('Connected', true);
    };

    state.ws.onclose = () => {
        updateStatus('Disconnected', false);
        setTimeout(connectWebSocket, 3000);
    };

    state.ws.onerror = () => {
        updateStatus('Error', false);
    };

    state.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };
}

function updateStatus(text, connected) {
    const statusText = document.getElementById('status-text');
    const statusDot = document.querySelector('.status-dot');

    statusText.textContent = text;
    statusDot.style.background = connected ? 'var(--color-tool-result)' : 'var(--color-error)';
}

// ============================================
// Message Handling
// ============================================
function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'status':
            if (data.content === 'Processing...') {
                setStep('receive', 'complete');
                setStep('context', 'active');
            }
            addToMessageFlow('system', data.content);
            break;

        case 'debug_step':
            handleDebugStep(data);
            break;

        case 'debug_metrics':
            handleDebugMetrics(data.content);
            break;

        case 'debug_prompt':
            handleDebugPrompt(data.content);
            break;

        case 'tools':
            setStep('context', 'complete');
            setStep('llm', 'complete');
            setStep('tools', 'active');
            addMessage('tool', data.content, '🔧');
            addToMessageFlow('tool', data.content);
            break;

        case 'tool_result':
            addToolCall(data.tool, data.arguments, data.content);
            addMessage('tool-result', `✓ ${data.tool}: ${data.content}`, '✓');
            addToMessageFlow('tool', `Result: ${data.content}`);
            break;

        case 'response':
            setStep('tools', 'complete');
            setStep('response', 'complete');
            addMessage('assistant', data.content, '🐈');
            addToMessageFlow('assistant', data.content);
            state.isProcessing = false;
            updateSendButton();

            // Update output tokens (estimate)
            state.metrics.outputTokens += Math.ceil(data.content.length / 4);
            updateMetrics();

            // Auto-refresh sessions and logs after response
            refreshAfterChat();
            break;

        case 'error':
            addMessage('error', data.content, '⚠️');
            addToMessageFlow('system', `Error: ${data.content}`);
            state.isProcessing = false;
            updateSendButton();
            resetSteps();
            break;
    }
}

function handleDebugStep(data) {
    const stepMap = {
        'receive_input': 'receive',
        'build_context': 'context',
        'call_llm': 'llm',
        'execute_tools': 'tools',
        'generate_response': 'response'
    };

    const step = stepMap[data.step] || data.step;

    if (data.status === 'start') {
        setStep(step, 'active');
    } else if (data.status === 'complete') {
        setStep(step, 'complete');
        // Populate step details if available
        if (data.step_detail) {
            populateStepDetails(step, data.step_detail);
        }
    }

    if (data.details) {
        addToMessageFlow('system', data.details);
    }
}

function populateStepDetails(stepName, detail) {
    const stepEl = document.querySelector(`[data-step="${stepName}"]`);
    if (!stepEl) return;

    const detailsEl = stepEl.querySelector('.step-details');
    if (!detailsEl) return;

    let html = '';
    for (const [key, value] of Object.entries(detail)) {
        let displayValue = value;
        if (typeof value === 'object' && value !== null) {
            displayValue = JSON.stringify(value, null, 2);
            html += `<div class="detail-row"><span class="detail-label">${escapeHtml(key)}:</span></div><pre>${escapeHtml(displayValue)}</pre>`;
        } else {
            displayValue = String(value);
            if (displayValue.length > 500) {
                // Long text: render in a scrollable pre block
                html += `<div class="detail-row"><span class="detail-label">${escapeHtml(key)}:</span></div><pre>${escapeHtml(displayValue)}</pre>`;
            } else {
                html += `<div class="detail-row"><span class="detail-label">${escapeHtml(key)}:</span><span class="detail-value">${escapeHtml(displayValue)}</span></div>`;
            }
        }
    }
    detailsEl.innerHTML = html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderStepFullDetails(details) {
    if (!details || Object.keys(details).length === 0) {
        return '<em>No details available</em>';
    }
    let html = '';
    for (const [key, value] of Object.entries(details)) {
        if (typeof value === 'object' && value !== null) {
            const formatted = JSON.stringify(value, null, 2);
            html += `<div class="detail-row"><span class="detail-label">${escapeHtml(key)}:</span></div><pre>${escapeHtml(formatted)}</pre>`;
        } else {
            const strVal = String(value);
            if (strVal.length > 500) {
                html += `<div class="detail-row"><span class="detail-label">${escapeHtml(key)}:</span></div><pre>${escapeHtml(strVal)}</pre>`;
            } else {
                html += `<div class="detail-row"><span class="detail-label">${escapeHtml(key)}:</span><span class="detail-value">${escapeHtml(strVal)}</span></div>`;
            }
        }
    }
    return html;
}

function handleDebugMetrics(metrics) {
    state.metrics = { ...state.metrics, ...metrics };
    updateMetrics();
}

function handleDebugPrompt(prompt) {
    const systemPromptEl = document.getElementById('system-prompt');
    if (systemPromptEl) {
        systemPromptEl.textContent = prompt;
    }
}

// ============================================
// Processing Steps
// ============================================
function setStep(stepName, status) {
    const steps = document.querySelectorAll('.step');
    steps.forEach(step => {
        if (step.dataset.step === stepName) {
            // Preserve expandable class
            step.className = `step ${status} expandable`;
        }
    });
}

function resetSteps() {
    const steps = document.querySelectorAll('.step');
    steps.forEach(step => {
        step.className = 'step waiting expandable';
        step.classList.remove('expanded');
        const details = step.querySelector('.step-details');
        if (details) {
            details.classList.add('hidden');
            details.innerHTML = '';
        }
    });
}

function setupStepExpansion() {
    document.querySelectorAll('.step.expandable').forEach(step => {
        const header = step.querySelector('.step-header');
        if (header) {
            header.addEventListener('click', () => {
                const details = step.querySelector('.step-details');
                if (details && details.innerHTML.trim()) {
                    step.classList.toggle('expanded');
                    details.classList.toggle('hidden');
                }
            });
        }
    });
}

// ============================================
// Metrics Display
// ============================================
function updateMetrics() {
    const { model, contextTokens, contextLimit, inputTokens, outputTokens } = state.metrics;

    document.getElementById('metric-model').textContent = model || 'Unknown';
    document.getElementById('metric-tokens').textContent = formatTokens(contextTokens);
    document.getElementById('metric-limit').textContent = formatTokens(contextLimit);
    document.getElementById('metric-input').textContent = formatTokens(inputTokens);
    document.getElementById('metric-output').textContent = formatTokens(outputTokens);

    const percentage = contextLimit > 0 ? (contextTokens / contextLimit) * 100 : 0;
    document.getElementById('token-bar-fill').style.width = `${Math.min(percentage, 100)}%`;
}

function formatTokens(num) {
    if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

// ============================================
// Chat Messages
// ============================================
function addMessage(type, content, avatar = '') {
    const messagesContainer = document.getElementById('chat-messages');

    // Remove welcome message if present
    const welcome = messagesContainer.querySelector('.welcome-message');
    if (welcome) {
        welcome.remove();
    }

    const avatarMap = {
        'user': '👤',
        'assistant': '🐈',
        'tool': '🔧',
        'tool-result': '✓',
        'system': '⚙️',
        'error': '⚠️'
    };

    const labelMap = {
        'user': 'You',
        'assistant': 'nanobot',
        'tool': 'Tool Call',
        'tool-result': 'Tool Result',
        'system': 'System',
        'error': 'Error'
    };

    const message = document.createElement('div');
    message.className = `message ${type}`;
    message.innerHTML = `
        <div class="message-avatar">${avatar || avatarMap[type] || '💬'}</div>
        <div class="message-content">
            <div class="message-label">${labelMap[type] || type}</div>
            <div class="message-text">${formatContent(content)}</div>
            <div class="message-time">${new Date().toLocaleTimeString()}</div>
        </div>
    `;

    messagesContainer.appendChild(message);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function formatContent(content) {
    // Basic markdown formatting
    let html = content
        .replace(/```(\w+)?\n?([\s\S]*?)```/g, '<pre class="code-block"><code>$2</code></pre>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
    return html;
}

// ============================================
// Tool Calls Display
// ============================================
function addToolCall(name, args, result) {
    const toolCallsEl = document.getElementById('tool-calls');

    // Clear empty state
    const emptyState = toolCallsEl.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    const item = document.createElement('div');
    item.className = 'tool-call-item';
    item.innerHTML = `
        <div class="tool-call-name">🔧 ${name}</div>
        ${args ? `<div class="tool-call-args">${JSON.stringify(args, null, 2).substring(0, 100)}...</div>` : ''}
        <div class="tool-call-result">${result.substring(0, 150)}${result.length > 150 ? '...' : ''}</div>
    `;

    toolCallsEl.appendChild(item);
    toolCallsEl.scrollTop = toolCallsEl.scrollHeight;
}

// ============================================
// Message Flow Display
// ============================================
function addToMessageFlow(role, content) {
    const flowEl = document.getElementById('message-flow');

    // Clear empty state
    const emptyState = flowEl.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    const time = new Date().toLocaleTimeString();
    const preview = content.length > 80 ? content.substring(0, 80) + '...' : content;

    const item = document.createElement('div');
    item.className = `flow-item ${role}`;
    item.innerHTML = `
        <span class="flow-role">${role}</span>
        <span class="flow-content">${preview}</span>
        <span class="flow-time">${time}</span>
    `;

    flowEl.appendChild(item);
    flowEl.scrollTop = flowEl.scrollHeight;

    state.messageFlow.push({ role, content, time });
}

function clearMessageFlow() {
    const flowEl = document.getElementById('message-flow');
    flowEl.innerHTML = '<div class="empty-state">Send a message to see the flow</div>';
    state.messageFlow = [];

    const toolCallsEl = document.getElementById('tool-calls');
    toolCallsEl.innerHTML = '<div class="empty-state">No tool calls yet</div>';
    state.toolCalls = [];
}

// ============================================
// Send Message
// ============================================
function sendMessage(message) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        alert('Not connected. Please wait...');
        return;
    }

    state.isProcessing = true;
    updateSendButton();

    // Reset UI for new message
    resetSteps();
    setStep('receive', 'active');

    // Add user message
    addMessage('user', message, '👤');
    addToMessageFlow('user', message);

    // Update input tokens (rough estimate: ~4 chars per token)
    state.metrics.inputTokens += Math.ceil(message.length / 4);
    state.metrics.contextTokens += Math.ceil(message.length / 4);
    updateMetrics();

    // Send via WebSocket
    state.ws.send(JSON.stringify({
        message,
        session_id: state.currentSession,
    }));
}

function updateSendButton() {
    const sendBtn = document.getElementById('send-btn');
    const input = document.getElementById('chat-input');

    if (state.isProcessing) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<span>Processing...</span>';
        input.disabled = true;
    } else {
        sendBtn.disabled = false;
        sendBtn.innerHTML = '<span>Send</span><span class="send-icon">→</span>';
        input.disabled = false;
        input.focus();
    }
}

// ============================================
// Debug Panel Toggle
// ============================================
function toggleDebugPanel() {
    const panel = document.getElementById('debug-panel');
    const btn = document.getElementById('toggle-debug');

    state.debugVisible = !state.debugVisible;

    if (state.debugVisible) {
        panel.classList.remove('hidden');
        btn.classList.add('active');
    } else {
        panel.classList.add('hidden');
        btn.classList.remove('active');
    }
}

// ============================================
// Collapsible Sections
// ============================================
function setupCollapsibles() {
    document.querySelectorAll('.toggle-section').forEach(toggle => {
        toggle.addEventListener('click', () => {
            const section = toggle.closest('.collapsible');
            section.classList.toggle('expanded');
        });
    });
}

// ============================================
// Tab Navigation
// ============================================
function switchTab(tabName) {
    // Update nav items
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `${tabName}-tab`);
    });

    // Track active tab for polling
    state.activeTab = tabName;

    // Load tab-specific content
    if (tabName === 'history') {
        loadSessions();
        startPolling('history', loadSessions);
    } else if (tabName === 'config') {
        loadConfig();
        stopPolling('history');
        stopPolling('logs');
    } else if (tabName === 'logs') {
        loadLogs();
        startPolling('logs', loadLogs);
    } else {
        stopPolling('history');
        stopPolling('logs');
    }
}

// ============================================
// Auto-Polling System
// ============================================
function startPolling(name, callback) {
    stopPolling(name);  // Clear existing timer
    state.pollTimers[name] = setInterval(() => {
        // Only poll if still on the same tab
        if ((name === 'history' && state.activeTab === 'history') ||
            (name === 'logs' && state.activeTab === 'logs')) {
            callback();
        } else {
            stopPolling(name);
        }
    }, state.pollInterval);
}

function stopPolling(name) {
    if (state.pollTimers[name]) {
        clearInterval(state.pollTimers[name]);
        delete state.pollTimers[name];
    }
}

function stopAllPolling() {
    Object.keys(state.pollTimers).forEach(name => stopPolling(name));
}

function refreshAfterChat() {
    // Refresh sessions and logs after a chat response completes
    // Use a small delay to ensure backend has written the data
    setTimeout(() => {
        loadSessions();  // Update session list silently
        if (state.activeTab === 'logs') {
            loadLogs();  // If on logs tab, refresh immediately
        }
    }, 500);
}

// ============================================
// Sessions / History
// ============================================
async function loadSessions() {
    const historyList = document.getElementById('history-list');
    // Only show "Loading" on first load, not during auto-refresh
    if (!historyList.dataset.loaded) {
        historyList.innerHTML = '<p class="loading-text">Loading sessions...</p>';
    }

    try {
        const { sessions } = await api.getSessions();

        if (sessions.length === 0) {
            historyList.innerHTML = '<p class="loading-text">No conversation history yet</p>';
            historyList.dataset.loaded = 'true';
            return;
        }

        historyList.innerHTML = sessions.map(session => `
            <div class="history-item" data-key="${session.key}">
                <div class="history-key">${session.key}</div>
                <div class="history-time">${new Date(session.updated_at).toLocaleString()}</div>
                <div class="history-arrow">→</div>
            </div>
        `).join('');
        historyList.dataset.loaded = 'true';

        // Add click handlers
        historyList.querySelectorAll('.history-item').forEach(item => {
            item.addEventListener('click', () => showSessionDetail(item.dataset.key));
        });

        // Update session selector
        updateSessionSelector(sessions);
    } catch (error) {
        // Silently fail during auto-refresh, only show error on first load
        if (!historyList.dataset.loaded) {
            historyList.innerHTML = `<p class="loading-text">Error loading sessions: ${error.message}</p>`;
        }
    }
}

function updateSessionSelector(sessions) {
    const select = document.getElementById('session-select');
    select.innerHTML = '<option value="ui:default">Default</option>';

    sessions.forEach(session => {
        if (session.key !== 'ui:default') {
            select.innerHTML += `<option value="${session.key}">${session.key}</option>`;
        }
    });

    select.value = state.currentSession;
}

async function showSessionDetail(key) {
    document.getElementById('history-list').classList.add('hidden');
    document.getElementById('history-detail').classList.remove('hidden');
    document.getElementById('history-session-key').textContent = key;

    const messagesEl = document.getElementById('history-messages');
    messagesEl.innerHTML = '<p class="loading-text">Loading...</p>';

    try {
        const session = await api.getSession(key);

        messagesEl.innerHTML = session.messages.map(msg => `
            <div class="message ${msg.role}">
                <div class="message-avatar">${msg.role === 'user' ? '👤' : '🐈'}</div>
                <div class="message-content">
                    <div class="message-label">${msg.role === 'user' ? 'You' : 'nanobot'}</div>
                    <div class="message-text">${formatContent(msg.content)}</div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        messagesEl.innerHTML = `<p class="loading-text">Error: ${error.message}</p>`;
    }
}

function hideSessionDetail() {
    document.getElementById('history-list').classList.remove('hidden');
    document.getElementById('history-detail').classList.add('hidden');
}

async function deleteCurrentSession() {
    const key = document.getElementById('history-session-key').textContent;
    if (!confirm(`Delete session "${key}"?`)) return;

    try {
        await api.deleteSession(key);
        hideSessionDetail();
        loadSessions();
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// ============================================
// Configuration
// ============================================
async function loadConfig() {
    try {
        const config = await api.getConfig();
        state.config = config;

        // Update model in metrics
        if (config.agents?.defaults?.model) {
            state.metrics.model = config.agents.defaults.model;
            updateMetrics();
        }

        renderConfig(config);
    } catch (error) {
        console.error('Error loading config:', error);
    }
}

function renderConfig(config) {
    // Providers
    const providersEl = document.getElementById('providers-config');
    const providers = ['openrouter', 'anthropic', 'openai', 'gemini', 'deepseek', 'vllm'];

    providersEl.innerHTML = providers.map(name => {
        const provider = config.providers?.[name] || {};
        const displayName = name.charAt(0).toUpperCase() + name.slice(1);

        return `
            <div class="config-card">
                <div class="config-card-title">
                    <span>🤖</span> ${displayName}
                </div>
                <div class="config-fields">
                    <div class="config-field">
                        <label>API Key</label>
                        <input type="password" 
                               data-path="providers.${name}.apiKey"
                               value="${provider.apiKey || provider.api_key || ''}"
                               placeholder="Enter API key">
                    </div>
                    <div class="config-field">
                        <label>API Base</label>
                        <input type="text"
                               data-path="providers.${name}.apiBase"
                               value="${provider.apiBase || provider.api_base || ''}"
                               placeholder="Optional custom base URL">
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // Agent defaults
    const agentEl = document.getElementById('agent-config');
    const defaults = config.agents?.defaults || {};

    agentEl.innerHTML = `
        <div class="config-field">
            <label>Default Model</label>
            <input type="text"
                   data-path="agents.defaults.model"
                   value="${defaults.model || 'openrouter/anthropic/claude-sonnet-4'}"
                   placeholder="e.g., openrouter/anthropic/claude-sonnet-4">
        </div>
        <div class="config-field">
            <label>Max Tool Iterations</label>
            <input type="number"
                   data-path="agents.defaults.maxToolIterations"
                   value="${defaults.maxToolIterations || defaults.max_tool_iterations || 10}"
                   min="1" max="50">
        </div>
    `;

    // Tools
    const toolsEl = document.getElementById('tools-config');
    const tools = config.tools || {};

    toolsEl.innerHTML = `
        <div class="config-field">
            <label>Restrict to Workspace</label>
            <select data-path="tools.restrictToWorkspace">
                <option value="true" ${tools.restrictToWorkspace || tools.restrict_to_workspace ? 'selected' : ''}>Yes</option>
                <option value="false" ${!tools.restrictToWorkspace && !tools.restrict_to_workspace ? 'selected' : ''}>No</option>
            </select>
        </div>
        <div class="config-field">
            <label>Web Search API Key (Brave)</label>
            <input type="password"
                   data-path="tools.web.search.apiKey"
                   value="${tools.web?.search?.apiKey || tools.web?.search?.api_key || ''}"
                   placeholder="Optional Brave Search API key">
        </div>
    `;

    // Raw JSON
    document.getElementById('raw-config-editor').value = JSON.stringify(config, null, 2);
}

async function saveConfig() {
    // Collect values from inputs
    const config = JSON.parse(JSON.stringify(state.config || {}));

    document.querySelectorAll('[data-path]').forEach(input => {
        const path = input.dataset.path.split('.');
        let obj = config;

        for (let i = 0; i < path.length - 1; i++) {
            if (!obj[path[i]]) obj[path[i]] = {};
            obj = obj[path[i]];
        }

        let value = input.value;
        if (input.type === 'number') value = parseInt(value);
        if (value === 'true') value = true;
        if (value === 'false') value = false;

        obj[path[path.length - 1]] = value;
    });

    try {
        await api.saveConfig(config);
        state.config = config;

        // Update model in metrics
        if (config.agents?.defaults?.model) {
            state.metrics.model = config.agents.defaults.model;
            updateMetrics();
        }

        alert('Configuration saved!');
    } catch (error) {
        alert(`Error saving configuration: ${error.message}`);
    }
}

// ============================================
// New Session
// ============================================
function createNewSession() {
    const sessionId = `ui:${Date.now()}`;
    state.currentSession = sessionId;

    const select = document.getElementById('session-select');
    const option = document.createElement('option');
    option.value = sessionId;
    option.textContent = sessionId;
    select.appendChild(option);
    select.value = sessionId;

    // Clear chat
    document.getElementById('chat-messages').innerHTML = `
        <div class="welcome-message">
            <div class="welcome-icon">🐈</div>
            <h2>New Session Started</h2>
            <p>Watch how the AI processes your requests step-by-step</p>
        </div>
    `;

    clearMessageFlow();
    resetSteps();
}

// ============================================
// Execution Logs
// ============================================
let currentLog = null;

async function loadLogs() {
    const logsList = document.getElementById('logs-list');
    // Only show "Loading" on first load, not during auto-refresh
    if (!logsList.dataset.loaded) {
        logsList.innerHTML = '<p class="loading-text">Loading logs...</p>';
    }

    try {
        const { logs } = await api.getLogs();

        if (logs.length === 0) {
            logsList.innerHTML = '<p class="loading-text">No execution logs yet. Send some messages to generate logs!</p>';
            logsList.dataset.loaded = 'true';
            return;
        }

        logsList.innerHTML = logs.map(log => `
            <div class="log-item ${log.has_error ? 'has-error' : ''}" 
                 data-session="${log.session_id}" 
                 data-log-id="${log.log_id}">
                <span class="log-item-time">${formatLogTime(log.started_at)}</span>
                <span class="log-item-message">${log.message_preview || '(empty)'}</span>
                <span class="log-item-meta">
                    <span>🔧 ${log.tool_count}</span>
                    <span>🔄 ${log.iterations}</span>
                    <span>🤖 ${log.model || 'unknown'}</span>
                </span>
            </div>
        `).join('');
        logsList.dataset.loaded = 'true';

        // Add click handlers
        logsList.querySelectorAll('.log-item').forEach(item => {
            item.addEventListener('click', () => showLogDetail(item.dataset.session, item.dataset.logId));
        });
    } catch (error) {
        // Silently fail during auto-refresh
        if (!logsList.dataset.loaded) {
            logsList.innerHTML = `<p class="loading-text">Error loading logs: ${error.message}</p>`;
        }
    }
}

function formatLogTime(isoString) {
    if (!isoString) return 'Unknown';
    const date = new Date(isoString);
    return date.toLocaleString();
}

async function showLogDetail(sessionId, logId) {
    document.getElementById('logs-list').classList.add('hidden');
    document.getElementById('log-detail').classList.remove('hidden');

    const logContent = document.getElementById('log-content');
    logContent.innerHTML = '<p class="loading-text">Loading...</p>';

    try {
        const log = await api.getLog(sessionId, logId);
        currentLog = { sessionId, logId };

        document.getElementById('log-title').textContent = `Log ${logId} - ${formatLogTime(log.started_at)}`;

        logContent.innerHTML = `
            <!-- Overview -->
            <div class="log-section">
                <div class="log-section-title">📊 Overview</div>
                <div class="log-section-content">
Model: ${escapeHtml(log.model || 'unknown')}
Iterations: ${log.iterations}
Started: ${formatLogTime(log.started_at)}
Completed: ${formatLogTime(log.completed_at)}
                </div>
            </div>

            <!-- User Message -->
            <div class="log-section">
                <div class="log-section-title">👤 User Message</div>
                <div class="log-section-content">${escapeHtml(log.user_message)}</div>
            </div>

            <!-- Processing Steps with Full Details -->
            <div class="log-section">
                <div class="log-section-title">🔄 Processing Steps (click to expand)</div>
                <div class="log-step-list">
                    ${(log.steps || []).map((step, idx) => `
                        <div class="log-step-item expandable-log-step" data-idx="${idx}">
                            <div class="log-step-header">
                                <span class="step-icon">${step.status === 'complete' ? '✅' : '⏳'}</span>
                                <span class="step-summary"><strong>${escapeHtml(step.step)}</strong>: ${escapeHtml(step.summary || '')}</span>
                                <span class="step-expand">▶</span>
                            </div>
                            <div class="log-step-details hidden">
                                ${renderStepFullDetails(step.details || {})}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>

            <!-- Tool Executions -->
            ${(log.tool_executions && log.tool_executions.length > 0) ? `
            <div class="log-section">
                <div class="log-section-title">🔧 Tool Executions (${log.tool_executions.length})</div>
                <div class="log-tool-list">
                    ${log.tool_executions.map(tool => `
                        <div class="log-tool-item">
                            <div class="tool-name">${escapeHtml(tool.tool_name)} (${tool.elapsed_seconds?.toFixed(2)}s)</div>
                            <div class="tool-args">${escapeHtml(JSON.stringify(tool.arguments, null, 2))}</div>
                            <div class="tool-result">${escapeHtml(tool.result)}</div>
                        </div>
                    `).join('')}
                </div>
            </div>
            ` : ''}

            <!-- Assistant Response -->
            <div class="log-section">
                <div class="log-section-title">🐈 Assistant Response</div>
                <div class="log-section-content">${escapeHtml(log.assistant_response || '(no response)')}</div>
            </div>

            <!-- System Prompt -->
            ${log.system_prompt ? `
            <div class="log-section">
                <div class="log-section-title">📝 System Prompt</div>
                <div class="log-section-content">${escapeHtml(log.system_prompt)}</div>
            </div>
            ` : ''}

            <!-- Raw Metrics -->
            ${log.metrics ? `
            <div class="log-section">
                <div class="log-section-title">📈 Metrics</div>
                <div class="log-section-content">${escapeHtml(JSON.stringify(log.metrics, null, 2))}</div>
            </div>
            ` : ''}
        `;

        // Add click handlers for expandable steps in log detail
        logContent.querySelectorAll('.expandable-log-step').forEach(stepEl => {
            const header = stepEl.querySelector('.log-step-header');
            const details = stepEl.querySelector('.log-step-details');
            const expand = stepEl.querySelector('.step-expand');
            if (header && details) {
                header.style.cursor = 'pointer';
                header.addEventListener('click', () => {
                    details.classList.toggle('hidden');
                    if (expand) expand.textContent = details.classList.contains('hidden') ? '▶' : '▼';
                });
            }
        });
    } catch (error) {
        logContent.innerHTML = `<p class="loading-text">Error: ${error.message}</p>`;
    }
}

function hideLogDetail() {
    document.getElementById('logs-list').classList.remove('hidden');
    document.getElementById('log-detail').classList.add('hidden');
    currentLog = null;
}

async function deleteCurrentLog() {
    if (!currentLog) return;
    if (!confirm('Delete this execution log?')) return;

    try {
        await api.deleteLog(currentLog.sessionId, currentLog.logId);
        hideLogDetail();
        loadLogs();
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// ============================================
// Initialization
// ============================================
async function init() {
    // Connect WebSocket
    connectWebSocket();

    // Load initial data
    try {
        const status = await api.getStatus();
        state.metrics.model = status.model;
        updateMetrics();
    } catch (e) {
        console.error('Error loading status:', e);
    }

    // Load config to get system prompt (if available)
    loadConfig();

    // Setup event listeners
    setupEventListeners();
    setupCollapsibles();
    setupStepExpansion();
}

function setupEventListeners() {
    // Tab navigation
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => switchTab(item.dataset.tab));
    });

    // Chat input
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (chatInput.value.trim() && !state.isProcessing) {
                sendMessage(chatInput.value.trim());
                chatInput.value = '';
            }
        }
    });

    // Auto-resize textarea
    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = Math.min(chatInput.scrollHeight, 150) + 'px';
    });

    sendBtn.addEventListener('click', () => {
        if (chatInput.value.trim() && !state.isProcessing) {
            sendMessage(chatInput.value.trim());
            chatInput.value = '';
            chatInput.style.height = 'auto';
        }
    });

    // Session selector
    document.getElementById('session-select').addEventListener('change', (e) => {
        state.currentSession = e.target.value;
    });

    // New session
    document.getElementById('new-session-btn').addEventListener('click', createNewSession);

    // Debug panel toggle
    document.getElementById('toggle-debug').addEventListener('click', toggleDebugPanel);

    // History buttons
    document.getElementById('refresh-history-btn')?.addEventListener('click', loadSessions);
    document.getElementById('back-to-list-btn')?.addEventListener('click', hideSessionDetail);
    document.getElementById('delete-session-btn')?.addEventListener('click', deleteCurrentSession);

    // Config buttons
    document.getElementById('reload-config-btn')?.addEventListener('click', loadConfig);
    document.getElementById('save-config-btn')?.addEventListener('click', saveConfig);

    // Logs buttons
    document.getElementById('refresh-logs-btn')?.addEventListener('click', loadLogs);
    document.getElementById('back-to-logs-btn')?.addEventListener('click', hideLogDetail);
    document.getElementById('delete-log-btn')?.addEventListener('click', deleteCurrentLog);
}

// Start the app
document.addEventListener('DOMContentLoaded', init);
