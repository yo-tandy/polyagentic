// Polyagentic Dashboard - Main Application

async function safeFetch(url, fallback = {}) {
    try {
        const res = await fetch(url);
        if (res.status === 401) {
            window.location.href = '/auth/login';
            return fallback;
        }
        if (!res.ok) {
            console.warn(`${url} returned ${res.status}`);
            return fallback;
        }
        return await res.json();
    } catch (err) {
        console.warn(`${url} failed:`, err);
        return fallback;
    }
}

const App = {
    ws: null,
    reconnectDelay: 1000,
    currentUser: null,

    async init() {
        ProjectSelector.init();
        AgentPanel.init('agents-list');
        TaskBoard.init('task-board');
        ChatView.init('chat-messages', 'chat-input', 'chat-send');
        ActivityLog.init('activity-log');
        GitPanel.init('git-info');
        KnowledgePanel.init('knowledge-list');
        TeamConfig.init();
        SessionStatus.init();
        ConversationBar.init();
        ConversationWindow.init();
        ProjectInfo.init();

        await this.loadUserInfo();
        await this.loadInitialState();
        this.connectWebSocket();
        this.startPolling();
        this._initReauthModal();
    },

    async loadUserInfo() {
        const raw = await safeFetch('/auth/me', null);
        const res = raw?.user || raw;
        if (res && res.id) {
            this.currentUser = res;
            const userEl = document.getElementById('header-user');
            const nameEl = document.getElementById('header-user-name');
            const avatarEl = document.getElementById('header-user-avatar');
            if (userEl) userEl.style.display = 'flex';
            if (nameEl) nameEl.textContent = res.name || res.email || '';
            if (avatarEl && res.picture_url) {
                avatarEl.src = res.picture_url;
                avatarEl.style.display = 'inline-block';
            } else if (avatarEl) {
                avatarEl.style.display = 'none';
            }
            // Logout handler
            const logoutBtn = document.getElementById('logout-btn');
            if (logoutBtn) {
                logoutBtn.addEventListener('click', async () => {
                    await fetch('/auth/logout', { method: 'POST' });
                    window.location.href = '/auth/login';
                });
            }
        }
    },

    async loadInitialState() {
        const [agentsRes, tasksRes, phasesRes, activityRes, branchesRes, logRes, chatRes] = await Promise.all([
            safeFetch('/api/agents', { agents: [] }),
            safeFetch('/api/tasks', { tasks: [] }),
            safeFetch('/api/phases', { phases: [] }),
            safeFetch('/api/activity', { activity: [] }),
            safeFetch('/api/git/branches', { branches: [] }),
            safeFetch('/api/git/log', { log: [] }),
            safeFetch('/api/chat/history', { messages: [] }),
        ]);

        AgentPanel.render(agentsRes.agents || []);
        TaskBoard.updatePhases(phasesRes.phases || []);
        TaskBoard.render(tasksRes.tasks || []);
        ActivityLog.render(activityRes.activity || []);
        GitPanel.render(branchesRes.branches || [], logRes.log || []);

        // Check for active conversations (now returns a list)
        const convRes = await safeFetch('/api/conversations/active', { conversations: [] });
        const activeConvs = convRes.conversations || [];
        const activeConvIds = new Set(activeConvs.map(c => c.id));

        // Restore conversation windows first (so messages can be added to them)
        for (const conv of activeConvs) {
            ConversationWindow.show(conv);
        }

        // Restore chat history (split main vs conversation messages)
        for (const msg of (chatRes.messages || [])) {
            const type = msg.sender_type === 'human' ? 'user' : 'agent';
            const name = msg.sender_type === 'human' ? (msg.sender_name || 'You') : (msg.sender_name || msg.sender || 'Agent');
            if (msg.conversation_id && activeConvIds.has(msg.conversation_id)) {
                ConversationWindow.addMessage(name, msg.content, type, {
                    ...msg.metadata,
                    conversation_id: msg.conversation_id,
                });
            } else if (!msg.conversation_id) {
                ChatView.addMessage(name, msg.content, type, msg.metadata);
            }
        }

        // Load knowledge base
        KnowledgePanel.load();
    },

    connectWebSocket() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/ws`;

        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            const el = document.getElementById('connection-status');
            if (el) {
                el.textContent = 'Connected';
                el.classList.add('connected');
            }
            this.reconnectDelay = 1000;
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleEvent(data);
            } catch (err) {
                console.error('WS parse error:', err);
            }
        };

        this.ws.onclose = () => {
            const el = document.getElementById('connection-status');
            if (el) {
                el.textContent = 'Disconnected';
                el.classList.remove('connected');
            }
            setTimeout(() => this.connectWebSocket(), this.reconnectDelay);
            this.reconnectDelay = Math.min(this.reconnectDelay * 2, 10000);
        };

        this.ws.onerror = () => {
            this.ws.close();
        };
    },

    handleEvent(event) {
        switch (event.event_type) {
            case 'agent_status':
                AgentPanel.updateStatus(event.data.agent_id, event.data.status, event.data.last_error, event.data.activity);
                break;

            case 'chat_response':
                if (event.data.conversation_id) {
                    ConversationWindow.addMessage(
                        event.data.sender || 'Agent',
                        event.data.content,
                        'agent',
                        { ...event.data.metadata, conversation_id: event.data.conversation_id }
                    );
                } else {
                    ChatView.addMessage(
                        event.data.sender || 'Agent',
                        event.data.content,
                        'agent',
                        event.data.metadata
                    );
                }
                break;

            case 'new_message':
                ActivityLog.addEntry(event.data);
                this.refreshTasks();
                break;

            case 'task_update':
                this.refreshTasks();
                break;

            case 'phase_update':
                this.refreshPhases();
                break;

            case 'git_activity':
                this.refreshGit();
                break;

            case 'knowledge_updated':
                KnowledgePanel.load();
                break;

            case 'comments_updated':
                // Refresh doc if user is viewing the affected document
                if (KnowledgePanel._selectedDocId === event.data?.doc_id) {
                    KnowledgePanel._loadAndRenderDoc(event.data.doc_id);
                }
                break;

            case 'session_status':
                SessionStatus.handleSessionUpdate(event.data);
                if (event.data.session_state === 'paused') {
                    AgentPanel.updateStatus(event.data.agent_id, 'session-paused');
                } else if (event.data.session_state === 'active') {
                    AgentPanel.updateStatus(event.data.agent_id, 'idle');
                }
                break;

            case 'agent_added':
                this.refreshAgents();
                break;

            case 'conversation_started':
                ConversationWindow.show(event.data);
                break;

            case 'conversation_ended':
                ConversationWindow.hide(event.data.id);
                break;

            case 'project_switched':
                // Full reload on project switch
                location.reload();
                break;

            case 'mcp_request':
                ActivityLog.addEntry({
                    type: 'mcp',
                    sender: event.data.agent_id,
                    content: `Requesting capability: ${event.data.capability}`,
                    timestamp: new Date().toISOString(),
                });
                break;

            case 'mcp_installed':
                ActivityLog.addEntry({
                    type: 'mcp',
                    sender: event.data.deployed_by || 'system',
                    content: `MCP server "${event.data.server_name}" deployed to ${event.data.target_agent}`,
                    timestamp: new Date().toISOString(),
                });
                break;

            case 'auth_required':
                App.showReauthModal();
                break;

            case 'auth_restored':
                App.hideReauthModal();
                // Update all pending-reauth agents to idle
                if (event.data.resumed_agents) {
                    event.data.resumed_agents.forEach(id => {
                        AgentPanel.updateStatus(id, 'idle');
                    });
                }
                break;
        }
    },

    showReauthModal() {
        const modal = document.getElementById('reauth-modal');
        if (modal) {
            modal.classList.add('active');
            // Reset status text
            const status = document.getElementById('reauth-status');
            if (status) { status.textContent = ''; status.className = 'reauth-status'; }
            // Re-enable button
            const btn = document.getElementById('reauth-confirm');
            if (btn) { btn.disabled = false; btn.textContent = 'Re-authenticate'; }
        }
    },

    hideReauthModal() {
        const modal = document.getElementById('reauth-modal');
        if (modal) modal.classList.remove('active');
    },

    _initReauthModal() {
        const confirmBtn = document.getElementById('reauth-confirm');
        const cancelBtn = document.getElementById('reauth-cancel');
        const closeBtn = document.getElementById('reauth-close');
        const statusEl = document.getElementById('reauth-status');

        if (confirmBtn) {
            confirmBtn.addEventListener('click', async () => {
                confirmBtn.disabled = true;
                confirmBtn.textContent = 'Authenticating...';
                if (statusEl) {
                    statusEl.textContent = 'Opening OAuth login in your browser...';
                    statusEl.className = 'reauth-status reauth-status--pending';
                }
                try {
                    const res = await fetch('/sessions/reauth', { method: 'POST' });
                    const data = await res.json();
                    if (data.status === 'ok') {
                        if (statusEl) {
                            statusEl.textContent = 'Authentication successful!';
                            statusEl.className = 'reauth-status reauth-status--success';
                        }
                        setTimeout(() => App.hideReauthModal(), 1500);
                    } else {
                        if (statusEl) {
                            statusEl.textContent = `Authentication failed: ${data.error || 'Unknown error'}`;
                            statusEl.className = 'reauth-status reauth-status--error';
                        }
                        confirmBtn.disabled = false;
                        confirmBtn.textContent = 'Re-authenticate';
                    }
                } catch (err) {
                    if (statusEl) {
                        statusEl.textContent = `Error: ${err.message}`;
                        statusEl.className = 'reauth-status reauth-status--error';
                    }
                    confirmBtn.disabled = false;
                    confirmBtn.textContent = 'Re-authenticate';
                }
            });
        }

        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => App.hideReauthModal());
        }
        if (closeBtn) {
            closeBtn.addEventListener('click', () => App.hideReauthModal());
        }
    },

    async refreshTasks() {
        const res = await safeFetch('/api/tasks', { tasks: [] });
        TaskBoard.render(res.tasks || []);
    },

    async refreshPhases() {
        const res = await safeFetch('/api/phases', { phases: [] });
        TaskBoard.updatePhases(res.phases || []);
    },

    async refreshAgents() {
        const res = await safeFetch('/api/agents', { agents: [] });
        AgentPanel.render(res.agents || []);
    },

    async refreshGit() {
        const [branchesRes, logRes] = await Promise.all([
            safeFetch('/api/git/branches', { branches: [] }),
            safeFetch('/api/git/log', { log: [] }),
        ]);
        GitPanel.render(branchesRes.branches || [], logRes.log || []);
    },

    startPolling() {
        // Periodic refresh for tasks, phases, and git (in case WS events are missed)
        setInterval(() => {
            this.refreshTasks();
            this.refreshPhases();
            this.refreshGit();
        }, 10000);
    }
};

document.addEventListener('DOMContentLoaded', () => App.init());
