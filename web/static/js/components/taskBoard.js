const TaskBoard = {
    container: null,
    columns: ['draft', 'pending', 'in_progress', 'paused', 'review', 'done'],
    columnLabels: {
        draft: 'Draft',
        pending: 'Pending',
        in_progress: 'In Progress',
        paused: 'Paused',
        review: 'Review',
        done: 'Done',
    },
    _highlightedAgent: null,
    _currentTaskId: null,
    _refreshInterval: null,
    _lastTaskJson: null,
    _phases: [],
    _currentPhaseFilter: null,
    _currentSort: 'priority',
    _lastRenderedTasks: null,

    init(containerId) {
        this.container = document.getElementById(containerId);
        this._renderSkeleton();
        this._bindModalEvents();
    },

    _renderSkeleton() {
        if (!this.container) return;
        this.container.innerHTML = `
            <div class="phase-bar" id="phase-bar"></div>
            <div class="board-controls">
                <label class="board-controls__label">Sort:</label>
                <select class="board-controls__select" id="task-sort-select">
                    <option value="priority">Priority</option>
                    <option value="created">Newest first</option>
                    <option value="created_asc">Oldest first</option>
                    <option value="updated">Recently updated</option>
                    <option value="phase">Phase</option>
                    <option value="assignee">Agent</option>
                </select>
            </div>
            <div class="task-columns">
                ${this.columns.map(col => `
                    <div class="task-column" data-column="${col}">
                        <div class="task-column__title">${this.columnLabels[col]}</div>
                        <div class="task-column__cards" id="tasks-${col}"></div>
                    </div>
                `).join('')}
            </div>
        `;
        document.getElementById('task-sort-select').addEventListener('change', (e) => {
            this._currentSort = e.target.value;
            if (this._lastRenderedTasks) this.render(this._lastRenderedTasks);
        });
    },

    // ── Phase Bar ──

    updatePhases(phases) {
        this._phases = phases || [];
        this._renderPhaseBar();
    },

    _renderPhaseBar() {
        const bar = document.getElementById('phase-bar');
        if (!bar) return;
        if (this._phases.length === 0) {
            bar.style.display = 'none';
            return;
        }
        bar.style.display = 'flex';

        const allBtn = `<button class="phase-pill ${!this._currentPhaseFilter ? 'phase-pill--active' : ''}"
                         onclick="TaskBoard._filterByPhase(null)">All</button>`;

        const phaseBtns = this._phases.map(p => {
            const active = this._currentPhaseFilter === p.id ? 'phase-pill--active' : '';
            const statusDot = `<span class="phase-pill__status phase-status--${p.status}"></span>`;
            return `<button class="phase-pill ${active}" onclick="TaskBoard._filterByPhase('${p.id}')">
                        ${this._escapeHtml(p.name)} ${statusDot}
                    </button>`;
        }).join('');

        // Show approval buttons for phases awaiting user action
        const actionablePhase = this._phases.find(p =>
            p.status === 'awaiting_approval' || p.status === 'review');
        let approvalHtml = '';
        if (actionablePhase) {
            const isApproval = actionablePhase.status === 'awaiting_approval';
            const approveAction = isApproval ? 'approve' : 'approve-review';
            const rejectAction = isApproval ? 'reject' : 'reject-review';
            const label = isApproval ? 'Plan' : 'Review';
            approvalHtml = `
                <div class="phase-actions">
                    <span class="phase-actions__label">${this._escapeHtml(actionablePhase.name)}:</span>
                    <button class="btn btn--sm btn--approve" onclick="TaskBoard._approvePhase('${actionablePhase.id}', '${approveAction}')">
                        Approve ${label}
                    </button>
                    <button class="btn btn--sm btn--reject" onclick="TaskBoard._rejectPhase('${actionablePhase.id}', '${rejectAction}')">
                        Request Changes
                    </button>
                </div>`;
        }

        bar.innerHTML = allBtn + phaseBtns + approvalHtml;
    },

    _filterByPhase(phaseId) {
        this._currentPhaseFilter = phaseId;
        this._renderPhaseBar();
        if (this._lastRenderedTasks) {
            this.render(this._lastRenderedTasks);
        }
    },

    async _approvePhase(phaseId, action) {
        try {
            await fetch(`/api/phases/${phaseId}/${action}`, { method: 'POST' });
        } catch (e) {
            console.error('Failed to approve phase:', e);
        }
    },

    async _rejectPhase(phaseId, action) {
        const feedback = prompt('What changes are needed?');
        if (feedback === null) return;
        try {
            await fetch(`/api/phases/${phaseId}/${action}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ feedback }),
            });
        } catch (e) {
            console.error('Failed to reject phase:', e);
        }
    },

    // ── Task Rendering ──

    render(tasks) {
        this._lastRenderedTasks = tasks;

        // Apply phase filter
        const filtered = this._currentPhaseFilter
            ? tasks.filter(t => t.phase_id === this._currentPhaseFilter)
            : tasks;

        this.columns.forEach(col => {
            const el = document.getElementById(`tasks-${col}`);
            if (!el) return;
            const colTasks = filtered
                .filter(t => t.status === col)
                .sort((a, b) => this._compareTasks(a, b));
            el.innerHTML = colTasks.map(t => {
                const assigneeText = t.assignee || (t.role ? `role: ${t.role}` : 'unassigned');
                const labelsHtml = (t.labels && t.labels.length)
                    ? `<div class="task-card__labels">${t.labels.map(l => `<span class="task-label">${this._escapeHtml(l)}</span>`).join('')}</div>`
                    : '';
                const outcomeHtml = (t.status === 'done' && t.outcome)
                    ? `<span class="task-outcome outcome--${t.outcome}">${t.outcome}</span>`
                    : '';
                const categoryBadge = t.category === 'project'
                    ? '<span class="task-badge task-badge--project">PRJ</span>'
                    : '';
                const estimateBadge = t.estimate
                    ? `<span class="task-badge task-badge--estimate">${t.estimate}sp</span>`
                    : '';
                return `
                    <div class="task-card" data-task-id="${t.id}" data-assignee="${t.assignee || ''}" data-priority="${t.priority || 3}">
                        <div class="task-card__header">
                            <span class="task-card__priority priority--${t.priority || 3}">P${t.priority || 3}</span>
                            ${categoryBadge}
                            ${estimateBadge}
                            ${outcomeHtml}
                            <span class="task-card__assignee">${this._escapeHtml(assigneeText)}</span>
                            <span class="task-card__age">${this._formatAge(t.created_at)}</span>
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
        this._lastTaskJson = null;
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
            this._lastTaskJson = null;
            return;
        }
        // Skip re-render if data hasn't changed
        const taskJson = JSON.stringify(task);
        if (taskJson === this._lastTaskJson) return;
        this._lastTaskJson = taskJson;
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
                <div class="task-detail__description kb-markdown">${this._renderMarkdown(task.description || 'No description')}</div>
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

        // Phase name lookup
        let phaseName = '';
        if (task.phase_id) {
            const phase = this._phases.find(p => p.id === task.phase_id);
            phaseName = phase ? this._escapeHtml(phase.name) : task.phase_id;
        }

        html += `
            <div class="modal__section">
                <h3 class="modal__section-title">Info</h3>
                <div class="task-detail__meta">
                    <div><strong>Category:</strong> <span class="task-badge task-badge--${task.category || 'operational'}">${(task.category || 'operational').toUpperCase()}</span></div>
                    ${phaseName ? `<div><strong>Phase:</strong> ${phaseName}</div>` : ''}
                    <div><strong>Priority:</strong> <span class="priority--${task.priority || 3}">P${task.priority || 3}</span></div>
                    ${task.estimate ? `<div><strong>Estimate:</strong> <span class="task-badge task-badge--estimate">${task.estimate}sp</span></div>` : ''}
                    <div><strong>Assignee:</strong> ${this._escapeHtml(assigneeDisplay)}</div>
                    <div><strong>Created by:</strong> ${task.created_by || 'unknown'}</div>
                    <div><strong>Reviewer:</strong> ${task.reviewer || 'Not assigned'}</div>
                    <div><strong>Created:</strong> ${this._formatTime(task.created_at)}</div>
                    <div><strong>Updated:</strong> ${this._formatTime(task.updated_at)}</div>
                    ${task.role ? `<div><strong>Role:</strong> ${this._escapeHtml(task.role)}</div>` : ''}
                    ${task.branch ? `<div><strong>Branch:</strong> ${task.branch}</div>` : ''}
                    <div><strong>Labels:</strong> ${labelsDisplay}</div>
                    ${task.status === 'done' ? `<div><strong>Outcome:</strong> ${outcomeDisplay}</div>` : ''}
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
                    <div class="task-detail__summary kb-markdown">${this._renderMarkdown(task.paused_summary)}</div>
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
                    <div class="task-detail__summary kb-markdown">${this._renderMarkdown(task.completion_summary)}</div>
                </div>
            `;
        }

        // Done: Review output
        if (task.status === 'done' && task.review_output) {
            html += `
                <div class="modal__section">
                    <h3 class="modal__section-title">Review Output</h3>
                    <div class="task-detail__review kb-markdown">${this._renderMarkdown(task.review_output)}</div>
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

        if (s === 'draft') {
            buttons.push({label: 'Move to Pending', status: 'pending'});
            buttons.push({label: 'Start', status: 'in_progress'});
        }
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

        const deleteBtn = `<button class="btn btn--sm btn--danger" onclick="TaskBoard._deleteTask('${task.id}')">Delete</button>`;

        return `
            <div class="modal__section">
                <div class="task-detail__actions">
                    ${buttons.map(b =>
                        `<button class="btn btn--sm btn--status" onclick="TaskBoard._updateTaskStatus('${task.id}', '${b.status}')">${b.label}</button>`
                    ).join('')}
                    ${deleteBtn}
                </div>
            </div>
        `;
    },

    async _deleteTask(taskId) {
        if (!confirm('Delete this task? This cannot be undone.')) return;
        try {
            const res = await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
            if (res.ok) {
                this._closeTaskDetail();
                // Trigger a refresh of the board
                if (typeof App !== 'undefined' && App.refreshTasks) App.refreshTasks();
            }
        } catch (e) {
            console.error('Failed to delete task:', e);
        }
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
                            <div class="progress-note__meta">
                                <span class="progress-note__time">${this._formatTime(n.timestamp)}</span>
                                <span class="progress-note__agent">${this._escapeHtml(n.agent || 'unknown')}</span>
                            </div>
                            <div class="progress-note__text kb-markdown">${this._renderMarkdown(n.note || '')}</div>
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

    _renderMarkdown(text) {
        if (!text) return '';
        if (typeof marked !== 'undefined') {
            try {
                marked.setOptions({ breaks: true, gfm: true });
                return marked.parse(text);
            } catch (e) {
                console.error('Markdown parse error:', e);
                return this._escapeHtml(text);
            }
        }
        return this._escapeHtml(text);
    },

    _compareTasks(a, b) {
        switch (this._currentSort) {
            case 'created':
                return (b.created_at || '').localeCompare(a.created_at || '');
            case 'created_asc':
                return (a.created_at || '').localeCompare(b.created_at || '');
            case 'updated':
                return (b.updated_at || '').localeCompare(a.updated_at || '');
            case 'phase': {
                const phaseOrder = {};
                this._phases.forEach((p, i) => { phaseOrder[p.id] = i; });
                const pa = a.phase_id ? (phaseOrder[a.phase_id] ?? 999) : 999;
                const pb = b.phase_id ? (phaseOrder[b.phase_id] ?? 999) : 999;
                return pa !== pb ? pa - pb : (a.priority || 3) - (b.priority || 3);
            }
            case 'assignee': {
                const aa = (a.assignee || 'zzz').toLowerCase();
                const ba = (b.assignee || 'zzz').toLowerCase();
                return aa !== ba ? aa.localeCompare(ba) : (a.priority || 3) - (b.priority || 3);
            }
            default: // 'priority'
                return (a.priority || 3) - (b.priority || 3);
        }
    },

    _parseUTC(isoStr) {
        // Naive ISO strings (no tz) are UTC — append Z so JS doesn't treat them as local
        if (!isoStr) return NaN;
        if (isoStr.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(isoStr)) return new Date(isoStr).getTime();
        return new Date(isoStr + 'Z').getTime();
    },

    _formatAge(isoStr) {
        if (!isoStr) return '';
        try {
            const secs = Math.floor((Date.now() - this._parseUTC(isoStr)) / 1000);
            if (secs < 60)    return '<1m';
            if (secs < 3600)  return Math.floor(secs / 60) + 'm';
            if (secs < 86400) return Math.floor(secs / 3600) + 'h';
            const days = Math.floor(secs / 86400);
            if (days < 14)    return days + 'd';
            if (days < 60)    return Math.floor(days / 7) + 'wk';
            if (days < 365)   return Math.floor(days / 30) + 'mo';
            return Math.floor(days / 365) + 'yr';
        } catch { return ''; }
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
