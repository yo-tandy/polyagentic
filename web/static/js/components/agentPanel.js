const AgentPanel = {
    container: null,
    selectedAgent: null,

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
    },

    render(agents) {
        if (!this.container) return;
        this.container.innerHTML = agents.map(a => `
            <div class="agent-card" data-agent-id="${a.id}">
                <div class="agent-card__header">
                    <div class="agent-card__info">
                        <div class="agent-card__name">${a.name}</div>
                        <div class="agent-card__role">${a.role}</div>
                    </div>
                    <div class="agent-card__actions">
                        <button class="agent-card__chat-btn" data-agent-id="${a.id}" title="Chat with agent">C</button>
                        <button class="agent-card__memory-btn" data-agent-id="${a.id}" title="View agent memory">M</button>
                        <button class="agent-card__status-btn" data-agent-id="${a.id}" title="Request status report">?</button>
                    </div>
                </div>
                <span class="agent-card__status status--${a.status}">${a.status}</span>
                ${a.last_error ? `<div class="agent-card__error" title="${a.last_error.replace(/"/g, '&quot;')}">${a.last_error}</div>` : ''}
            </div>
        `).join('');

        // Bind click handlers for agent selection
        this.container.querySelectorAll('.agent-card').forEach(card => {
            card.addEventListener('click', (e) => {
                // Don't trigger selection if clicking buttons
                if (e.target.closest('.agent-card__status-btn') || e.target.closest('.agent-card__memory-btn') || e.target.closest('.agent-card__chat-btn') || e.target.closest('.agent-card__error')) return;
                const agentId = card.dataset.agentId;
                this._toggleSelect(agentId);
            });
        });

        // Bind status button handlers
        this.container.querySelectorAll('.agent-card__status-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const agentId = btn.dataset.agentId;
                this._requestStatus(agentId, btn);
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

    updateStatus(agentId, status, lastError) {
        const card = this.container?.querySelector(`[data-agent-id="${agentId}"]`);
        if (!card) return;
        const badge = card.querySelector('.agent-card__status');
        if (badge) {
            badge.className = `agent-card__status status--${status}`;
            badge.textContent = status;
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
