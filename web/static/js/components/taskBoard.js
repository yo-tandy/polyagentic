const TaskBoard = {
    container: null,
    columns: ['pending', 'in_progress', 'paused', 'review', 'done'],
    columnLabels: {
        pending: 'Pending',
        in_progress: 'In Progress',
        paused: 'Paused',
        review: 'Review',
        done: 'Done',
    },
    _highlightedAgent: null,
    _currentTaskId: null,
    _refreshInterval: null,

    init(containerId) {
        this.container = document.getElementById(containerId);
        this._renderSkeleton();
        this._bindModalEvents();
    },

    _renderSkeleton() {
        if (!this.container) return;
        this.container.innerHTML = `
            <div class="task-columns">
                ${this.columns.map(col => `
                    <div class="task-column" data-column="${col}">
                        <div class="task-column__title">${this.columnLabels[col]}</div>
                        <div class="task-column__cards" id="tasks-${col}"></div>
                    </div>
                `).join('')}
            </div>
        `;
    },

    render(tasks) {
        this.columns.forEach(col => {
            const el = document.getElementById(`tasks-${col}`);
            if (!el) return;
            const colTasks = tasks
                .filter(t => t.status === col)
                .sort((a, b) => (a.priority || 3) - (b.priority || 3));
            el.innerHTML = colTasks.map(t => {
                const assigneeText = t.assignee || (t.role ? `role: ${t.role}` : 'unassigned');
                const labelsHtml = (t.labels && t.labels.length)
                    ? `<div class="task-card__labels">${t.labels.map(l => `<span class="task-label">${this._escapeHtml(l)}</span>`).join('')}</div>`
                    : '';
                const outcomeHtml = (t.status === 'done' && t.outcome)
                    ? `<span class="task-outcome outcome--${t.outcome}">${t.outcome}</span>`
                    : '';
                return `
                    <div class="task-card" data-task-id="${t.id}" data-assignee="${t.assignee || ''}" data-priority="${t.priority || 3}">
                        <div class="task-card__header">
                            <span class="task-card__priority priority--${t.priority || 3}">P${t.priority || 3}</span>
                            ${outcomeHtml}
                            <span class="task-card__assignee">${this._escapeHtml(assigneeText)}</span>
                        </div>
                        <div class="task-card__title">${this._escapeHtml(t.title)}</div>
                        ${labelsHtml}
                    </div>
                `;
            }).join('');
        });

        // Re-apply highlight if an agent is selected
        if (this._highlightedAgent) {
            this.highlightByAssignee(this._highlightedAgent);
        }
    },

    highlightByAssignee(agentId) {
        this._highlightedAgent = agentId;
        if (!this.container) return;
        const cards = this.container.querySelectorAll('.task-card');
        cards.forEach(card => {
            if (card.dataset.assignee === agentId) {
                card.classList.add('task-card--highlighted');
                card.classList.remove('task-card--dimmed');
            } else {
                card.classList.add('task-card--dimmed');
                card.classList.remove('task-card--highlighted');
            }
        });
    },

    clearHighlight() {
        this._highlightedAgent = null;
        if (!this.container) return;
        const cards = this.container.querySelectorAll('.task-card');
        cards.forEach(card => {
            card.classList.remove('task-card--highlighted', 'task-card--dimmed');
        });
    },

    // ── Task Detail Modal ──

    _bindModalEvents() {
        // Event delegation: clicks on task cards
        this.container?.addEventListener('click', (e) => {
            const card = e.target.closest('.task-card');
            if (!card) return;
            const taskId = card.dataset.taskId;
            if (taskId) this._openTaskDetail(taskId);
        });

        // Close button
        document.getElementById('task-detail-close')?.addEventListener('click', () => this._closeTaskDetail());

        // Click overlay to close
        const overlay = document.getElementById('task-detail-modal');
        overlay?.addEventListener('click', (e) => {
            if (e.target === overlay) this._closeTaskDetail();
        });

        // ESC key to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this._currentTaskId) this._closeTaskDetail();
        });
    },

    async _openTaskDetail(taskId) {
        this._currentTaskId = taskId;
        const modal = document.getElementById('task-detail-modal');
        modal?.classList.add('active');

        // Show loading state
        const body = document.getElementById('task-detail-body');
        if (body) body.innerHTML = '<p style="color: var(--text-muted); padding: 20px;">Loading...</p>';

        // Fetch and render
        await this._refreshTaskDetail();

        // Auto-refresh every 5 seconds while open
        this._refreshInterval = setInterval(() => this._refreshTaskDetail(), 5000);
    },

    _closeTaskDetail() {
        this._currentTaskId = null;
        const modal = document.getElementById('task-detail-modal');
        modal?.classList.remove('active');
        if (this._refreshInterval) {
            clearInterval(this._refreshInterval);
            this._refreshInterval = null;
        }
    },

    async _refreshTaskDetail() {
        if (!this._currentTaskId) return;
        const task = await safeFetch(`/api/tasks/${this._currentTaskId}`, null);
        if (!task || task.error) {
            const body = document.getElementById('task-detail-body');
            if (body) body.innerHTML = '<p style="color: var(--red);">Task not found.</p>';
            return;
        }
        this._renderTaskDetail(task);
    },

    _renderTaskDetail(task) {
        // Title and status badge
        const titleEl = document.getElementById('task-detail-title');
        const statusEl = document.getElementById('task-detail-status');
        if (titleEl) titleEl.textContent = task.title;
        if (statusEl) {
            statusEl.textContent = task.status.replace('_', ' ');
            statusEl.className = `task-detail__status status--${task.status}`;
        }

        const body = document.getElementById('task-detail-body');
        if (!body) return;

        let html = '';

        // Always: Task description
        html += `
            <div class="modal__section">
                <h3 class="modal__section-title">Description</h3>
                <div class="task-detail__description">${this._escapeHtml(task.description || 'No description')}</div>
            </div>
        `;

        // Metadata
        const assigneeDisplay = task.assignee || (task.role ? `role: ${task.role}` : 'Unassigned');
        const labelsDisplay = (task.labels && task.labels.length)
            ? task.labels.map(l => `<span class="task-label">${this._escapeHtml(l)}</span>`).join(' ')
            : 'None';
        const outcomeDisplay = task.outcome
            ? `<span class="task-outcome outcome--${task.outcome}">${task.outcome}</span>`
            : 'N/A';

        html += `
            <div class="modal__section">
                <h3 class="modal__section-title">Info</h3>
                <div class="task-detail__meta">
                    <div><strong>Priority:</strong> <span class="priority--${task.priority || 3}">P${task.priority || 3}</span></div>
                    <div><strong>Assignee:</strong> ${this._escapeHtml(assigneeDisplay)}</div>
                    <div><strong>Created by:</strong> ${task.created_by || 'unknown'}</div>
                    <div><strong>Reviewer:</strong> ${task.reviewer || 'Not assigned'}</div>
                    <div><strong>Created:</strong> ${this._formatTime(task.created_at)}</div>
                    <div><strong>Updated:</strong> ${this._formatTime(task.updated_at)}</div>
                    ${task.role ? `<div><strong>Role:</strong> ${this._escapeHtml(task.role)}</div>` : ''}
                    ${task.branch ? `<div><strong>Branch:</strong> ${task.branch}</div>` : ''}
                    <div><strong>Labels:</strong> ${labelsDisplay}</div>
                    <div><strong>Outcome:</strong> ${outcomeDisplay}</div>
                    ${task.id ? `<div><strong>ID:</strong> ${task.id}</div>` : ''}
                </div>
            </div>
        `;

        // Status action buttons
        html += this._renderStatusActions(task);

        // Paused: show paused summary
        if (task.status === 'paused' && task.paused_summary) {
            html += `
                <div class="modal__section">
                    <h3 class="modal__section-title">Paused State</h3>
                    <div class="task-detail__summary">${this._escapeHtml(task.paused_summary)}</div>
                </div>
            `;
        }

        // In-progress: Progress notes (current)
        const notes = task.progress_notes || [];
        if (task.status === 'in_progress' && notes.length > 0) {
            html += this._renderProgressNotes('Current Progress', notes);
        }

        // Review/Done: Completion summary
        if ((task.status === 'review' || task.status === 'done') && task.completion_summary) {
            html += `
                <div class="modal__section">
                    <h3 class="modal__section-title">Completion Summary</h3>
                    <div class="task-detail__summary">${this._escapeHtml(task.completion_summary)}</div>
                </div>
            `;
        }

        // Done: Review output
        if (task.status === 'done' && task.review_output) {
            html += `
                <div class="modal__section">
                    <h3 class="modal__section-title">Review Output</h3>
                    <div class="task-detail__review">${this._escapeHtml(task.review_output)}</div>
                </div>
            `;
        }

        // Review/Done: Progress history (collapsed)
        if ((task.status === 'review' || task.status === 'done') && notes.length > 0) {
            html += this._renderProgressNotes('Progress History', notes);
        }

        body.innerHTML = html;
    },

    _renderStatusActions(task) {
        const buttons = [];
        const s = task.status;

        if (s === 'pending') {
            buttons.push({label: 'Start', status: 'in_progress'});
        }
        if (s === 'in_progress') {
            buttons.push({label: 'Move to Review', status: 'review'});
            buttons.push({label: 'Pause', status: 'paused'});
        }
        if (s === 'paused') {
            buttons.push({label: 'Resume', status: 'in_progress'});
            buttons.push({label: 'Back to Pending', status: 'pending'});
        }
        if (s === 'review') {
            buttons.push({label: 'Mark Done', status: 'done'});
            buttons.push({label: 'Back to In Progress', status: 'in_progress'});
        }
        if (s === 'done') {
            buttons.push({label: 'Reopen', status: 'pending'});
        }

        if (buttons.length === 0) return '';

        return `
            <div class="modal__section">
                <div class="task-detail__actions">
                    ${buttons.map(b =>
                        `<button class="btn btn--sm btn--status" onclick="TaskBoard._updateTaskStatus('${task.id}', '${b.status}')">${b.label}</button>`
                    ).join('')}
                </div>
            </div>
        `;
    },

    async _updateTaskStatus(taskId, newStatus) {
        try {
            const res = await fetch(`/api/tasks/${taskId}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({status: newStatus}),
            });
            if (res.ok) {
                await this._refreshTaskDetail();
            }
        } catch (e) {
            console.error('Failed to update task status:', e);
        }
    },

    _renderProgressNotes(title, notes) {
        return `
            <div class="modal__section">
                <h3 class="modal__section-title">${title}</h3>
                <div class="task-detail__progress">
                    ${notes.map(n => `
                        <div class="progress-note">
                            <span class="progress-note__time">${this._formatTime(n.timestamp)}</span>
                            <span class="progress-note__agent">${this._escapeHtml(n.agent || 'unknown')}</span>
                            <span class="progress-note__text">${this._escapeHtml(n.note || '')}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    },

    _escapeHtml(text) {
        if (!text) return '';
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    },

    _formatTime(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr);
            return d.toLocaleString();
        } catch {
            return isoStr;
        }
    },
};
