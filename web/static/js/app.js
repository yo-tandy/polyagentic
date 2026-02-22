// Polyagentic Dashboard - Main Application

async function safeFetch(url, fallback = {}) {
    try {
        const res = await fetch(url);
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

    async init() {
        ProjectSelector.init();
        AgentPanel.init('agents-list');
        TaskBoard.init('task-board');
        ChatView.init('chat-messages', 'chat-input', 'chat-send');
        ActivityLog.init('activity-log');
        GitPanel.init('git-info');
        KnowledgePanel.init('knowledge-list');
        TeamConfig.init();

        await this.loadInitialState();
        this.connectWebSocket();
        this.startPolling();
    },

    async loadInitialState() {
        const [agentsRes, tasksRes, activityRes, branchesRes, logRes, chatRes] = await Promise.all([
            safeFetch('/api/agents', { agents: [] }),
            safeFetch('/api/tasks', { tasks: [] }),
            safeFetch('/api/activity', { activity: [] }),
            safeFetch('/api/git/branches', { branches: [] }),
            safeFetch('/api/git/log', { log: [] }),
            safeFetch('/api/chat/history', { messages: [] }),
        ]);

        AgentPanel.render(agentsRes.agents || []);
        TaskBoard.render(tasksRes.tasks || []);
        ActivityLog.render(activityRes.activity || []);
        GitPanel.render(branchesRes.branches || [], logRes.log || []);

        // Restore chat history
        for (const msg of (chatRes.messages || [])) {
            const type = msg.sender === 'user' ? 'user' : 'agent';
            const name = msg.sender === 'user' ? 'You' : (msg.sender || 'Agent');
            ChatView.addMessage(name, msg.content, type, msg.metadata);
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
                AgentPanel.updateStatus(event.data.agent_id, event.data.status);
                break;

            case 'chat_response':
                ChatView.addMessage(
                    event.data.sender || 'Agent',
                    event.data.content,
                    'agent',
                    event.data.metadata
                );
                break;

            case 'new_message':
                ActivityLog.addEntry(event.data);
                this.refreshTasks();
                break;

            case 'task_update':
                this.refreshTasks();
                break;

            case 'git_activity':
                this.refreshGit();
                break;

            case 'knowledge_updated':
                KnowledgePanel.load();
                break;

            case 'agent_added':
                this.refreshAgents();
                break;

            case 'project_switched':
                // Full reload on project switch
                location.reload();
                break;
        }
    },

    async refreshTasks() {
        const res = await safeFetch('/api/tasks', { tasks: [] });
        TaskBoard.render(res.tasks || []);
        // Also refresh the task detail modal if it's open
        if (TaskBoard._currentTaskId) {
            TaskBoard._refreshTaskDetail();
        }
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
        // Periodic refresh for tasks and git (in case WS events are missed)
        setInterval(() => {
            this.refreshTasks();
            this.refreshGit();
        }, 10000);
    }
};

document.addEventListener('DOMContentLoaded', () => App.init());
