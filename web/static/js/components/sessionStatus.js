// Session Status Modal Component

const SessionStatus = {
    modal: null,
    listEl: null,

    init() {
        this.modal = document.getElementById('session-status-modal');
        this.listEl = document.getElementById('session-status-list');

        document.getElementById('session-status-btn')?.addEventListener('click', () => this.open());
        document.getElementById('session-status-close')?.addEventListener('click', () => this.close());
        this.modal?.addEventListener('click', (e) => {
            if (e.target === this.modal) this.close();
        });
    },

    open() {
        this.modal?.classList.add('active');
        this.loadSessions();
    },

    close() {
        this.modal?.classList.remove('active');
    },

    async loadSessions() {
        const res = await safeFetch('/api/sessions', { sessions: [] });
        this.renderSessions(res.sessions || []);
    },

    renderSessions(sessions) {
        if (!this.listEl) return;

        if (sessions.length === 0) {
            this.listEl.innerHTML = '<div class="session-empty">No sessions recorded yet.</div>';
            return;
        }

        // Determine bulk action state: if any session-based agent is active, show "Pause All"
        const sessionAgents = sessions.filter(s => s.use_session !== false);
        const anyActive = sessionAgents.some(s => s.state === 'active');
        const bulkLabel = anyActive ? 'Pause All' : 'Resume All';
        const bulkAction = anyActive ? 'pause-all' : 'resume-all';

        const bulkBar = sessionAgents.length > 0
            ? `<div class="session-bulk-bar">
                <button class="btn btn--sm session-bulk-btn" id="session-bulk-action" data-action="${bulkAction}">${bulkLabel}</button>
               </div>`
            : '';

        this.listEl.innerHTML = bulkBar + sessions.map(s => this._renderSession(s)).join('');

        // Bind bulk action button
        const bulkBtn = this.listEl.querySelector('#session-bulk-action');
        if (bulkBtn) {
            bulkBtn.addEventListener('click', () => {
                this._handleBulkAction(bulkBtn.dataset.action, bulkBtn);
            });
        }

        // Bind action buttons
        this.listEl.querySelectorAll('.session-action-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this._handleAction(btn.dataset.agentId, btn.dataset.action, btn);
            });
        });

        // Bind model selectors
        this.listEl.querySelectorAll('.session-model-select').forEach(sel => {
            sel.addEventListener('change', () => {
                this._handleModelChange(sel.dataset.agentId, sel.value, sel);
            });
        });

        // Bind warning bar click → toggle error details
        this.listEl.querySelectorAll('.session-card__warning').forEach(warn => {
            warn.addEventListener('click', () => {
                const errorEl = warn.nextElementSibling;
                if (errorEl && errorEl.classList.contains('session-card__error')) {
                    const hidden = errorEl.style.display === 'none';
                    errorEl.style.display = hidden ? 'block' : 'none';
                    warn.textContent = warn.textContent.replace(/[▸▾]/, hidden ? '▾' : '▸');
                }
            });
        });
    },

    _renderSession(s) {
        const isStateless = s.use_session === false;
        const stateLabel = isStateless ? 'stateless' : s.state;
        const stateClass = isStateless ? 'session-state--stateless' : `session-state--${s.state}`;
        const avgDuration = s.avg_duration_ms ? `${(s.avg_duration_ms / 1000).toFixed(1)}s` : '-';
        const totalDuration = s.total_duration_ms ? `${(s.total_duration_ms / 1000).toFixed(1)}s` : '-';
        const sessionIdShort = isStateless
            ? 'none (stateless)'
            : (s.session_id ? s.session_id.substring(0, 12) + '...' : 'none');

        const resetBtn = `<button class="btn btn--sm btn--muted session-action-btn" data-agent-id="${s.agent_id}" data-action="reset">Reset</button>`;

        let actions = '';
        if (isStateless) {
            actions = resetBtn;
        } else if (s.state === 'active') {
            actions = `
                <button class="btn btn--sm session-action-btn" data-agent-id="${s.agent_id}" data-action="pause">Pause</button>
                <button class="btn btn--sm btn--danger session-action-btn" data-agent-id="${s.agent_id}" data-action="kill">Kill</button>
                ${resetBtn}
            `;
        } else if (s.state === 'paused') {
            actions = `
                <button class="btn btn--sm btn--primary session-action-btn" data-agent-id="${s.agent_id}" data-action="resume">Resume</button>
                <button class="btn btn--sm btn--danger session-action-btn" data-agent-id="${s.agent_id}" data-action="kill">Kill</button>
                ${resetBtn}
            `;
        } else if (s.state === 'killed') {
            actions = `
                <span class="session-killed-label">Killed — will use fresh session</span>
                ${resetBtn}
            `;
        }

        return `
            <div class="session-card" data-agent-id="${s.agent_id}">
                <div class="session-card__header">
                    <div class="session-card__agent">${this._escapeHtml(s.agent_name)}</div>
                    <span class="session-card__state ${stateClass}">${stateLabel}</span>
                </div>
                <div class="session-card__stats">
                    <div class="session-stat">
                        <span class="session-stat__label">Requests</span>
                        <span class="session-stat__value">${s.request_count}</span>
                    </div>
                    <div class="session-stat">
                        <span class="session-stat__label">Errors</span>
                        <span class="session-stat__value ${s.error_count > 0 ? 'session-stat--error' : ''}">${s.error_count}</span>
                    </div>
                    <div class="session-stat">
                        <span class="session-stat__label">Avg Time</span>
                        <span class="session-stat__value">${avgDuration}</span>
                    </div>
                    <div class="session-stat">
                        <span class="session-stat__label">Total Time</span>
                        <span class="session-stat__value">${totalDuration}</span>
                    </div>
                </div>
                ${!isStateless && s.consecutive_errors > 0 ? `
                    <div class="session-card__warning" style="cursor:pointer" title="Click to toggle error details">
                        ${s.consecutive_errors} consecutive error(s) ▸
                    </div>
                    <div class="session-card__error" style="display:none">
                        <strong>Last error:</strong> ${s.last_error ? this._escapeHtml(s.last_error) : '<em>Details lost (server restarted)</em>'}
                    </div>
                ` : (s.last_error ? `
                    <div class="session-card__error">
                        <strong>Last error:</strong> ${this._escapeHtml(s.last_error)}
                    </div>
                ` : '')}
                <div class="session-card__meta">
                    <span>Session: ${sessionIdShort}</span>
                    <label class="session-model">
                        <span class="session-model__label">Model:</span>
                        <select class="session-model-select" data-agent-id="${s.agent_id}">
                            ${['sonnet', 'opus', 'haiku'].map(m =>
                                `<option value="${m}"${m === s.model ? ' selected' : ''}>${m}</option>`
                            ).join('')}
                        </select>
                    </label>
                </div>
                <div class="session-card__actions">${actions}</div>
            </div>
        `;
    },

    async _handleBulkAction(action, btn) {
        btn.disabled = true;
        const origText = btn.textContent;
        btn.textContent = '...';
        try {
            const res = await fetch(`/api/sessions/${action}`, { method: 'POST' });
            if (res.ok) {
                await this.loadSessions();
            } else {
                const data = await res.json();
                console.error(`Bulk ${action} failed:`, data.error);
            }
        } catch (err) {
            console.error('Bulk action error:', err);
        }
        btn.disabled = false;
        btn.textContent = origText;
    },

    async _handleAction(agentId, action, btn) {
        btn.disabled = true;
        const origText = btn.textContent;
        btn.textContent = '...';
        try {
            const res = await fetch(`/api/sessions/${agentId}/${action}`, { method: 'POST' });
            if (res.ok) {
                await this.loadSessions();
            } else {
                const data = await res.json();
                console.error(`Session ${action} failed:`, data.error);
            }
        } catch (err) {
            console.error('Session action error:', err);
        }
        btn.disabled = false;
        btn.textContent = origText;
    },

    async _handleModelChange(agentId, model, sel) {
        sel.disabled = true;
        try {
            const res = await fetch(`/api/sessions/${agentId}/model`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model }),
            });
            if (!res.ok) {
                const data = await res.json();
                console.error('Model change failed:', data.error);
                await this.loadSessions(); // revert to actual value
            }
        } catch (err) {
            console.error('Model change error:', err);
            await this.loadSessions();
        }
        sel.disabled = false;
    },

    handleSessionUpdate(data) {
        // Refresh modal if it's currently open
        if (this.modal?.classList.contains('active')) {
            this.loadSessions();
        }
    },

    _escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
};
