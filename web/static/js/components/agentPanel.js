const AgentPanel = {
    container: null,
    selectedAgent: null,
    _agents: [],

    init(containerId) {
        this.container = document.getElementById(containerId);

        // Memory modal close handler
        const closeBtn = document.getElementById('memory-modal-close');
        if (closeBtn) closeBtn.addEventListener('click', () => this._hideMemoryModal());

        const overlay = document.getElementById('memory-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this._hideMemoryModal();
            });
        }

        // Add-to-repo modal close handler
        const repoCloseBtn = document.getElementById('add-to-repo-close');
        if (repoCloseBtn) repoCloseBtn.addEventListener('click', () => this._hideRepoModal());

        const repoOverlay = document.getElementById('add-to-repo-modal');
        if (repoOverlay) {
            repoOverlay.addEventListener('click', (e) => {
                if (e.target === repoOverlay) this._hideRepoModal();
            });
        }

        const repoSaveBtn = document.getElementById('repo-add-save');
        if (repoSaveBtn) repoSaveBtn.addEventListener('click', () => this._saveToRepo());

        // Diagnostics modal close handler
        const diagCloseBtn = document.getElementById('diagnostics-modal-close');
        if (diagCloseBtn) diagCloseBtn.addEventListener('click', () => this._hideDiagnosticsModal());

        const diagOverlay = document.getElementById('diagnostics-modal');
        if (diagOverlay) {
            diagOverlay.addEventListener('click', (e) => {
                if (e.target === diagOverlay) this._hideDiagnosticsModal();
            });
        }
    },

    render(agents) {
        if (!this.container) return;
        this._agents = agents;
        this.container.innerHTML = agents.map(a => `
            <div class="agent-card" data-agent-id="${a.id}">
                <div class="agent-card__header">
                    <div class="agent-card__info">
                        <div class="agent-card__name">
                            ${a.name}${(!a.is_fixed && !a.in_repository) ? '<span class="agent-card__diamond" title="Not in repository — click to add">&#x25C6;</span>' : ''}
                        </div>
                        <div class="agent-card__role">${a.role}</div>
                    </div>
                    <div class="agent-card__actions">
                        <button class="agent-card__chat-btn" data-agent-id="${a.id}" title="Chat with agent">C</button>
                        <button class="agent-card__memory-btn" data-agent-id="${a.id}" title="View agent memory">M</button>
                        <button class="agent-card__status-btn" data-agent-id="${a.id}" title="Request status report">?</button>
                    </div>
                </div>
                <span class="agent-card__status status--${a.status}">${a.status}${a.activity ? `<span class="agent-card__activity activity--${a.activity}">${AgentPanel._activityIcon(a.activity)}</span>` : ''}</span>
                ${a.last_error ? `<div class="agent-card__error" title="${a.last_error.replace(/"/g, '&quot;')}">${a.last_error}</div>` : ''}
            </div>
        `).join('');

        // Bind click handlers for agent selection
        this.container.querySelectorAll('.agent-card').forEach(card => {
            card.addEventListener('click', (e) => {
                // Don't trigger selection if clicking buttons or diamond
                if (e.target.closest('.agent-card__status-btn') || e.target.closest('.agent-card__memory-btn') || e.target.closest('.agent-card__chat-btn') || e.target.closest('.agent-card__error') || e.target.closest('.agent-card__diamond')) return;
                const agentId = card.dataset.agentId;
                // If agent is pending re-auth, show the re-auth modal
                const badge = card.querySelector('.agent-card__status');
                if (badge && badge.classList.contains('status--pending-reauth')) {
                    App.showReauthModal();
                    return;
                }
                this._toggleSelect(agentId);
            });
        });

        // Bind status button handlers — opens diagnostics modal
        this.container.querySelectorAll('.agent-card__status-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const agentId = btn.dataset.agentId;
                this._showDiagnostics(agentId);
            });
        });

        // Bind memory button handlers
        this.container.querySelectorAll('.agent-card__memory-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const agentId = btn.dataset.agentId;
                this._showMemory(agentId);
            });
        });

        // Bind chat button handlers
        this.container.querySelectorAll('.agent-card__chat-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const agentId = btn.dataset.agentId;
                this._startChat(agentId, btn);
            });
        });

        // Bind blue diamond handlers
        this.container.querySelectorAll('.agent-card__diamond').forEach(diamond => {
            diamond.addEventListener('click', (e) => {
                e.stopPropagation();
                const card = diamond.closest('.agent-card');
                const agentId = card.dataset.agentId;
                const agent = this._agents.find(a => a.id === agentId);
                if (agent) this._showAddToRepoDialog(agent);
            });
        });

        // Bind error bar expand/collapse (inline styles — immune to CSS caching)
        this.container.querySelectorAll('.agent-card__error').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const expanded = el.dataset.expanded === '1';
                el.dataset.expanded = expanded ? '0' : '1';
                el.style.whiteSpace = expanded ? 'nowrap' : 'normal';
                el.style.overflow = expanded ? 'hidden' : 'visible';
                el.style.textOverflow = expanded ? 'ellipsis' : 'unset';
                el.style.wordBreak = expanded ? '' : 'break-word';
            });
        });

        // Re-apply selection state
        if (this.selectedAgent) {
            const card = this.container.querySelector(`[data-agent-id="${this.selectedAgent}"]`);
            if (card) card.classList.add('agent-card--selected');
        }
    },

    _toggleSelect(agentId) {
        // Deselect previous
        if (this.selectedAgent) {
            const prev = this.container.querySelector(`[data-agent-id="${this.selectedAgent}"]`);
            if (prev) prev.classList.remove('agent-card--selected');
        }

        if (this.selectedAgent === agentId) {
            // Clicking same agent deselects
            this.selectedAgent = null;
            TaskBoard.clearHighlight();
        } else {
            this.selectedAgent = agentId;
            const card = this.container.querySelector(`[data-agent-id="${agentId}"]`);
            if (card) card.classList.add('agent-card--selected');
            TaskBoard.highlightByAssignee(agentId);
        }
    },

    async _requestStatus(agentId, btn) {
        btn.disabled = true;
        btn.textContent = '...';
        try {
            await fetch(`/api/agents/${agentId}/status-request`, { method: 'POST' });
        } catch (err) {
            console.error('Status request failed:', err);
        }
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = '?';
        }, 3000);
    },

    async _startChat(agentId, btn) {
        btn.disabled = true;
        btn.textContent = '...';
        try {
            const res = await fetch('/api/conversations/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_id: agentId }),
            });
            const data = await res.json();
            if (data.error) {
                console.error('Failed to start chat:', data.error);
            } else if (data.existing) {
                // Already open — focus the existing tab directly
                ConversationWindow.show(data);
            }
            // For new conversations, ConversationWindow.show() is triggered
            // by the conversation_started WS event
        } catch (err) {
            console.error('Failed to start chat:', err);
        }
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = 'C';
        }, 2000);
    },

    async _showMemory(agentId) {
        const titleEl = document.getElementById('memory-modal-title');
        const personalityEl = document.getElementById('memory-personality');
        const projectEl = document.getElementById('memory-project');

        if (titleEl) titleEl.textContent = `Memory: ${agentId}`;
        if (personalityEl) personalityEl.textContent = 'Loading...';
        if (projectEl) projectEl.textContent = 'Loading...';

        // Show modal
        const modal = document.getElementById('memory-modal');
        if (modal) modal.classList.add('active');

        // Fetch memory
        try {
            const res = await safeFetch(`/api/memory/${agentId}`, {});
            if (personalityEl) {
                personalityEl.textContent = res.personality || 'No personality memory recorded.';
            }
            if (projectEl) {
                projectEl.textContent = res.project || 'No project memory recorded.';
            }
        } catch (err) {
            console.error('Failed to load memory:', err);
            if (personalityEl) personalityEl.textContent = 'Failed to load.';
            if (projectEl) projectEl.textContent = 'Failed to load.';
        }
    },

    _hideMemoryModal() {
        const modal = document.getElementById('memory-modal');
        if (modal) modal.classList.remove('active');
    },

    // ── Agent Diagnostics Modal ──

    async _showDiagnostics(agentId) {
        const overlay = document.getElementById('diagnostics-modal');
        const titleEl = document.getElementById('diagnostics-modal-title');
        const bodyEl = document.getElementById('diagnostics-modal-body');

        const agent = this._agents.find(a => a.id === agentId);
        const displayName = agent ? agent.name : agentId;

        if (titleEl) titleEl.textContent = `Diagnostics: ${displayName}`;
        if (bodyEl) bodyEl.innerHTML = '<div class="diagnostics-loading">Loading...</div>';
        if (overlay) overlay.classList.add('active');

        try {
            const data = await safeFetch(`/api/agents/${agentId}/diagnostics`, {});
            if (bodyEl) {
                bodyEl.innerHTML = this._renderDiagnostics(data, agentId);

                // Bind "Ask for Status" button inside the modal
                const askBtn = bodyEl.querySelector('.diagnostics-ask-btn');
                if (askBtn) {
                    askBtn.addEventListener('click', async () => {
                        askBtn.disabled = true;
                        askBtn.textContent = 'Requesting...';
                        try {
                            await fetch(`/api/agents/${agentId}/status-request`, { method: 'POST' });
                            askBtn.textContent = 'Requested!';
                        } catch (err) {
                            askBtn.textContent = 'Failed';
                        }
                        setTimeout(() => {
                            askBtn.disabled = false;
                            askBtn.textContent = 'Ask for Status Report';
                        }, 3000);
                    });
                }
            }
        } catch (err) {
            console.error('Diagnostics load failed:', err);
            if (bodyEl) bodyEl.innerHTML = '<div class="diagnostics-loading">Failed to load diagnostics.</div>';
        }
    },

    _renderDiagnostics(data, agentId) {
        const esc = (t) => {
            const d = document.createElement('div');
            d.textContent = t || '';
            return d.innerHTML;
        };
        const linkify = (t) => typeof linkifyTaskIds === 'function' ? linkifyTaskIds(esc(t)) : esc(t);
        const timeAgo = (iso) => {
            if (!iso) return '-';
            const d = new Date(iso);
            const s = Math.floor((Date.now() - d.getTime()) / 1000);
            if (s < 0) return 'just now';
            if (s < 60) return `${s}s ago`;
            if (s < 3600) return `${Math.floor(s / 60)}m ago`;
            if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
            return `${Math.floor(s / 86400)}d ago`;
        };
        const priorityLabel = (p) => {
            const map = { 1: 'Critical', 2: 'High', 3: 'Medium', 4: 'Low', 5: 'Backlog' };
            return map[p] || `P${p}`;
        };
        const renderMd = (text) => {
            if (!text) return '';
            let html;
            if (typeof marked !== 'undefined') {
                try { html = marked.parse(text); } catch (e) { html = esc(text); }
            } else {
                html = esc(text);
            }
            return typeof linkifyTaskIds === 'function' ? linkifyTaskIds(html) : html;
        };

        // Status header
        let html = `
            <div class="diag-section">
                <div class="diag-status-row">
                    <span class="diag-label">Status:</span>
                    <span class="agent-card__status status--${data.status}">${data.status}</span>
                    ${data.activity ? `<span class="diag-activity">(${esc(data.activity)})</span>` : ''}
                </div>
                <div class="diag-status-row">
                    <span class="diag-label">Role:</span>
                    <span>${esc(data.role) || '-'}</span>
                </div>
                <div class="diag-status-row">
                    <span class="diag-label">Current Task:</span>
                    <span>${data.current_task_id ? linkify(data.current_task_id) : 'none'}</span>
                </div>
                <div class="diag-status-row">
                    <span class="diag-label">Messages Processed:</span>
                    <span>${data.messages_processed || 0}</span>
                </div>
                <div class="diag-status-row">
                    <span class="diag-label">Model:</span>
                    <span>${esc(data.model) || '-'}</span>
                </div>
            </div>
        `;

        // Current Task Details
        if (data.current_task) {
            const ct = data.current_task;
            html += `<div class="diag-section">
                <h3 class="diag-section-title">Current Task Details</h3>
                <div class="diag-task-detail">
                    <div class="diag-task-detail__header">
                        <span class="diag-task-detail__title">${esc(ct.title)}</span>
                        <span class="diag-task-detail__meta">
                            <span class="diag-tag diag-tag--status diag-tag--${ct.status}">${ct.status}</span>
                            <span class="diag-tag diag-tag--priority diag-tag--p${ct.priority}">${priorityLabel(ct.priority)}</span>
                            ${ct.estimate ? `<span class="diag-tag">${ct.estimate} pts</span>` : ''}
                            ${ct.category !== 'operational' ? `<span class="diag-tag">${esc(ct.category)}</span>` : ''}
                        </span>
                    </div>`;
            if (ct.description) {
                html += `<div class="diag-task-detail__desc">${esc(ct.description.substring(0, 200))}${ct.description.length > 200 ? '…' : ''}</div>`;
            }
            // Progress notes (scope analysis, plan, etc.)
            if (ct.progress_notes && ct.progress_notes.length > 0) {
                html += `<div class="diag-progress-notes">
                    <div class="diag-progress-notes__label">Progress Notes</div>`;
                for (const note of ct.progress_notes) {
                    html += `<div class="diag-progress-note">
                        <div class="diag-progress-note__header">
                            <span class="diag-progress-note__agent">${esc(note.agent || '')}</span>
                            <span class="diag-progress-note__time">${timeAgo(note.timestamp)}</span>
                        </div>
                        <div class="diag-progress-note__body">${renderMd(note.note || '')}</div>
                    </div>`;
                }
                html += `</div>`;
            }
            html += `</div></div>`;
        }

        // Working Box
        html += `<div class="diag-section">
            <h3 class="diag-section-title">Working Box</h3>`;
        if (data.workingbox_task) {
            const wb = data.workingbox_task;
            html += `<div class="diag-task-item">
                <span class="diag-task-type">${esc(wb.type || 'task')}</span>
                <span class="diag-task-title">${esc(wb.task_title || wb.task_id || 'untitled')}</span>
                <span class="diag-task-sender">from ${esc(wb.sender)}</span>
                <span class="diag-task-time">${timeAgo(wb.created_at)}</span>
            </div>`;
        } else {
            html += `<div class="diag-empty">Empty</div>`;
        }
        html += `</div>`;

        // Inbox Tasks
        const inboxCount = data.inbox_tasks ? data.inbox_tasks.length : 0;
        html += `<div class="diag-section">
            <h3 class="diag-section-title">Inbox Tasks (${inboxCount})</h3>`;
        if (inboxCount > 0) {
            for (const t of data.inbox_tasks) {
                html += `<div class="diag-task-item">
                    <span class="diag-task-title">${esc(t.task_title || t.task_id || 'untitled')}</span>
                    <span class="diag-task-sender">from ${esc(t.sender)}</span>
                    <span class="diag-task-time">${timeAgo(t.created_at)}</span>
                </div>`;
            }
        } else {
            html += `<div class="diag-empty">No tasks in inbox</div>`;
        }
        html += `</div>`;

        // Assigned Tasks (other tasks this agent owns, beyond current)
        if (data.assigned_tasks && data.assigned_tasks.length > 0) {
            html += `<div class="diag-section">
                <h3 class="diag-section-title">Assigned Tasks (${data.assigned_tasks.length})</h3>`;
            for (const t of data.assigned_tasks) {
                html += `<div class="diag-task-item">
                    <span class="diag-tag diag-tag--status diag-tag--${t.status}">${t.status}</span>
                    <span class="diag-task-title">${linkify(t.id)}</span>
                    <span class="diag-task-title">${esc(t.title)}</span>
                </div>`;
            }
            html += `</div>`;
        }

        // Session Stats
        html += `<div class="diag-section">
            <h3 class="diag-section-title">Session Stats</h3>`;
        if (data.session_stats) {
            const ss = data.session_stats;
            html += `<div class="diag-stats-grid">
                <div class="diag-stat"><span class="diag-stat-label">Requests</span><span class="diag-stat-value">${ss.request_count || 0}</span></div>
                <div class="diag-stat"><span class="diag-stat-label">Errors</span><span class="diag-stat-value ${ss.error_count > 0 ? 'diag-stat--error' : ''}">${ss.error_count || 0}</span></div>
                <div class="diag-stat"><span class="diag-stat-label">Avg Time</span><span class="diag-stat-value">${ss.avg_duration_ms ? (ss.avg_duration_ms / 1000).toFixed(1) + 's' : '-'}</span></div>
                <div class="diag-stat"><span class="diag-stat-label">Total Cost</span><span class="diag-stat-value">$${(ss.total_cost_usd || 0).toFixed(4)}</span></div>
                <div class="diag-stat"><span class="diag-stat-label">Tokens In</span><span class="diag-stat-value">${(ss.total_input_tokens || 0).toLocaleString()}</span></div>
                <div class="diag-stat"><span class="diag-stat-label">Tokens Out</span><span class="diag-stat-value">${(ss.total_output_tokens || 0).toLocaleString()}</span></div>
            </div>`;
            if (ss.last_error) {
                html += `<div class="diag-last-error"><strong>Last error:</strong> ${esc(ss.last_error)}</div>`;
            }
        } else {
            html += `<div class="diag-empty">No session data</div>`;
        }
        html += `</div>`;

        // Recent Activity
        const actCount = data.recent_activity ? data.recent_activity.length : 0;
        html += `<div class="diag-section">
            <h3 class="diag-section-title">Recent Activity (${actCount})</h3>`;
        if (actCount > 0) {
            html += `<div class="diag-activity-list">`;
            for (const a of data.recent_activity.slice().reverse()) {
                const direction = a.sender === agentId ? 'out' : 'in';
                const arrow = direction === 'out' ? '&rarr;' : '&larr;';
                const other = direction === 'out' ? a.recipient : a.sender;
                html += `<div class="diag-activity-item diag-activity--${direction}">
                    <span class="diag-activity-time">${timeAgo(a.timestamp)}</span>
                    <span class="diag-activity-dir">${arrow} ${esc(other)}</span>
                    <span class="diag-activity-type">${esc(a.type)}</span>
                    <span class="diag-activity-preview">${linkify((a.content_preview || '').substring(0, 80))}</span>
                </div>`;
            }
            html += `</div>`;
        } else {
            html += `<div class="diag-empty">No recent activity</div>`;
        }
        html += `</div>`;

        // Ask for Status button
        html += `<div class="diag-actions">
            <button class="btn btn--primary diagnostics-ask-btn">Ask for Status Report</button>
        </div>`;

        return html;
    },

    _hideDiagnosticsModal() {
        const modal = document.getElementById('diagnostics-modal');
        if (modal) modal.classList.remove('active');
    },

    // ── Add-to-Repository Dialog ──

    async _showAddToRepoDialog(agent) {
        const nameEl = document.getElementById('repo-add-name');
        const titleEl = document.getElementById('repo-add-title');
        const personalityEl = document.getElementById('repo-add-personality');
        const scopeEl = document.getElementById('repo-add-scope');
        const agentIdEl = document.getElementById('repo-add-agent-id');

        if (nameEl) nameEl.value = agent.name || '';
        if (titleEl) titleEl.value = agent.role || '';
        if (agentIdEl) agentIdEl.value = agent.id;

        // Populate scope dropdown with org name
        if (scopeEl) {
            scopeEl.value = 'org';
            try {
                const orgRes = await safeFetch('/api/orgs/current', {});
                const orgOpt = scopeEl.querySelector('option[value="org"]');
                if (orgOpt && orgRes.name) {
                    orgOpt.textContent = `My organization (${orgRes.name})`;
                }
            } catch { /* keep default label */ }
        }

        // Pre-fill personality from memory API
        if (personalityEl) {
            personalityEl.value = 'Loading...';
            try {
                const res = await safeFetch(`/api/memory/${agent.id}`, {});
                personalityEl.value = res.personality || '';
            } catch {
                personalityEl.value = '';
            }
        }

        const modal = document.getElementById('add-to-repo-modal');
        if (modal) modal.classList.add('active');
    },

    _hideRepoModal() {
        const modal = document.getElementById('add-to-repo-modal');
        if (modal) modal.classList.remove('active');
    },

    async _saveToRepo() {
        const name = document.getElementById('repo-add-name')?.value?.trim();
        const title = document.getElementById('repo-add-title')?.value?.trim();
        const personality = document.getElementById('repo-add-personality')?.value?.trim();
        const scope = document.getElementById('repo-add-scope')?.value || 'org';
        const sourceAgentId = document.getElementById('repo-add-agent-id')?.value;

        if (!name || !title) return;

        const saveBtn = document.getElementById('repo-add-save');
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }

        try {
            const res = await fetch('/api/templates', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name, title, personality, scope,
                    source_agent_id: sourceAgentId,
                }),
            });
            if (res.ok) {
                this._hideRepoModal();
                // Refresh agents to clear the diamond
                if (typeof App !== 'undefined' && App.refreshAgents) {
                    App.refreshAgents();
                }
            } else {
                const data = await res.json().catch(() => ({}));
                console.error('Failed to save template:', data.error || res.status);
            }
        } catch (err) {
            console.error('Failed to save template:', err);
        }

        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save to Repository'; }
    },

    _activityIcon(activity) {
        const icons = {
            'model': ' \u2726',      // ✦ four-pointed star (thinking/waiting on model)
            'processing': ' \u2699', // ⚙ gear (processing response)
        };
        return icons[activity] || '';
    },

    updateStatus(agentId, status, lastError, activity) {
        const card = this.container?.querySelector(`[data-agent-id="${agentId}"]`);
        if (!card) return;
        const badge = card.querySelector('.agent-card__status');
        if (badge) {
            badge.className = `agent-card__status status--${status}`;
            let activityHtml = '';
            if (activity) {
                activityHtml = `<span class="agent-card__activity activity--${activity}">${this._activityIcon(activity)}</span>`;
            }
            badge.innerHTML = status + activityHtml;
        }
        // Update error bar
        let errorEl = card.querySelector('.agent-card__error');
        if (lastError) {
            if (!errorEl) {
                errorEl = document.createElement('div');
                errorEl.className = 'agent-card__error';
                errorEl.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const expanded = errorEl.dataset.expanded === '1';
                    errorEl.dataset.expanded = expanded ? '0' : '1';
                    errorEl.style.whiteSpace = expanded ? 'nowrap' : 'normal';
                    errorEl.style.overflow = expanded ? 'hidden' : 'visible';
                    errorEl.style.textOverflow = expanded ? 'ellipsis' : 'unset';
                    errorEl.style.wordBreak = expanded ? '' : 'break-word';
                });
                card.appendChild(errorEl);
            }
            errorEl.textContent = lastError;
            errorEl.title = lastError;
        } else if (errorEl && status !== 'error') {
            errorEl.remove();
        }
    }
};
