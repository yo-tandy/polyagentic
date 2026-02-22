const TeamConfig = {
    modal: null,
    agentList: null,
    form: null,
    statusEl: null,

    init() {
        this.modal = document.getElementById('team-config-modal');
        this.agentList = document.getElementById('team-agent-list');
        this.form = document.getElementById('add-agent-form');
        this.statusEl = document.getElementById('add-agent-status');

        // Open/close
        document.getElementById('team-config-btn')?.addEventListener('click', () => this.open());
        document.getElementById('team-config-close')?.addEventListener('click', () => this.close());
        this.modal?.addEventListener('click', (e) => {
            if (e.target === this.modal) this.close();
        });

        // Form submit
        this.form?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.addAgent();
        });
    },

    open() {
        this.modal?.classList.add('active');
        this.loadAgents();
    },

    close() {
        this.modal?.classList.remove('active');
    },

    async loadAgents() {
        try {
            const res = await fetch('/api/config/agents').then(r => r.json());
            this.renderAgentList(res.agents || []);
        } catch (err) {
            console.error('Failed to load agents config:', err);
        }
    },

    renderAgentList(agents) {
        if (!this.agentList) return;
        this.agentList.innerHTML = agents.map(a => `
            <div class="team-agent" data-id="${a.id}">
                <div class="team-agent__info">
                    <span class="team-agent__name">${this._escapeHtml(a.name)}</span>
                    <span class="team-agent__role">${this._escapeHtml(a.role)}</span>
                    <span class="agent-card__status status--${a.status}">${a.status}</span>
                    ${a.is_fixed ? '<span class="team-agent__badge">fixed</span>' : ''}
                </div>
                <div class="team-agent__meta">
                    <span>Model: ${a.model}</span>
                </div>
                ${!a.is_fixed ? `<button class="btn btn--danger btn--sm" onclick="TeamConfig.removeAgent('${a.id}')">Remove</button>` : ''}
            </div>
        `).join('');
    },

    async addAgent() {
        const name = document.getElementById('agent-name')?.value?.trim();
        const role = document.getElementById('agent-role')?.value?.trim();
        const systemPrompt = document.getElementById('agent-system-prompt')?.value?.trim();
        const model = document.getElementById('agent-model')?.value;
        const tools = document.getElementById('agent-tools')?.value;

        if (!name || !role) {
            this._showStatus('Name and role are required', 'error');
            return;
        }

        if (!/^[a-z_]+$/.test(name)) {
            this._showStatus('Name must be lowercase letters and underscores only', 'error');
            return;
        }

        this._showStatus('Adding agent...', 'info');

        try {
            const res = await fetch('/api/config/agents', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    role,
                    system_prompt: systemPrompt || `You are a ${role}.`,
                    model: model || 'sonnet',
                    allowed_tools: tools || 'Bash,Edit,Write,Read,Glob,Grep',
                }),
            });

            const data = await res.json();

            if (res.ok && data.status === 'created') {
                this._showStatus(`Agent "${name}" added!`, 'success');
                this.form.reset();
                await this.loadAgents();
                // Refresh the main agents panel
                const agentsRes = await fetch('/api/agents').then(r => r.json());
                AgentPanel.render(agentsRes.agents || []);
            } else {
                this._showStatus(data.error || 'Failed to add agent', 'error');
            }
        } catch (err) {
            this._showStatus('Network error: ' + err.message, 'error');
        }
    },

    async removeAgent(agentId) {
        if (!confirm(`Remove agent "${agentId}"? This will stop the agent.`)) return;

        try {
            const res = await fetch(`/api/config/agents/${agentId}`, {
                method: 'DELETE',
            });

            const data = await res.json();

            if (res.ok && data.status === 'removed') {
                await this.loadAgents();
                // Refresh main agents panel
                const agentsRes = await fetch('/api/agents').then(r => r.json());
                AgentPanel.render(agentsRes.agents || []);
            } else {
                alert(data.error || 'Failed to remove agent');
            }
        } catch (err) {
            alert('Network error: ' + err.message);
        }
    },

    _showStatus(msg, type) {
        if (!this.statusEl) return;
        this.statusEl.textContent = msg;
        this.statusEl.className = `form-status form-status--${type}`;
        if (type === 'success') {
            setTimeout(() => {
                this.statusEl.textContent = '';
                this.statusEl.className = 'form-status';
            }, 3000);
        }
    },

    _escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
};
