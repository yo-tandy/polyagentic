const ProjectsDashboard = {
    _modal: null,
    _body: null,

    init() {
        this._modal = document.getElementById('projects-dashboard-modal');
        this._body = document.getElementById('projects-dashboard-body');

        const openBtn = document.getElementById('projects-dashboard-btn');
        if (openBtn) openBtn.addEventListener('click', () => this.show());

        const closeBtn = document.getElementById('projects-dashboard-close');
        if (closeBtn) closeBtn.addEventListener('click', () => this.hide());

        if (this._modal) {
            this._modal.addEventListener('click', (e) => {
                if (e.target === this._modal) this.hide();
            });
        }
    },

    async show() {
        if (!this._modal) return;
        this._modal.classList.add('active');
        this._body.innerHTML = '<div class="diagnostics-loading">Loading dashboard...</div>';
        await this._loadData();
    },

    hide() {
        if (this._modal) this._modal.classList.remove('active');
    },

    async _loadData() {
        try {
            const data = await safeFetch('/api/projects/dashboard', { projects: [] });
            this._render(data.projects || []);
        } catch (e) {
            this._body.innerHTML = '<div class="diagnostics-loading">Failed to load dashboard</div>';
        }
    },

    _render(projects) {
        if (!projects.length) {
            this._body.innerHTML = '<p>No projects found.</p>';
            return;
        }

        const rows = projects.map(p => this._renderRow(p)).join('');
        this._body.innerHTML = `
            <div class="pd-grid">
                <div class="pd-grid__header">
                    <span class="pd-col pd-col--name">Project</span>
                    <span class="pd-col pd-col--status">Status</span>
                    <span class="pd-col pd-col--agents">Agents</span>
                    <span class="pd-col pd-col--stats">Past Hour</span>
                    <span class="pd-col pd-col--stats">Past Day</span>
                    <span class="pd-col pd-col--stats">Overall</span>
                    <span class="pd-col pd-col--actions">Actions</span>
                </div>
                ${rows}
            </div>
        `;

        // Wire stop buttons
        this._body.querySelectorAll('.pd-stop-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const pid = btn.dataset.projectId;
                btn.disabled = true;
                btn.textContent = 'Stopping...';
                try {
                    const res = await fetch(`/api/projects/${pid}/stop`, { method: 'POST' });
                    if (res.ok) {
                        await this._loadData();
                        // Refresh the project selector too
                        if (typeof ProjectSelector !== 'undefined') {
                            ProjectSelector.loadProjects();
                        }
                    } else {
                        const data = await res.json().catch(() => ({}));
                        btn.textContent = data.error || 'Failed';
                        setTimeout(() => { btn.textContent = 'Stop'; btn.disabled = false; }, 2000);
                    }
                } catch (e) {
                    btn.textContent = 'Error';
                    setTimeout(() => { btn.textContent = 'Stop'; btn.disabled = false; }, 2000);
                }
            });
        });
    },

    _renderRow(p) {
        const statusClass = p.is_running ? 'pd-status--running' : 'pd-status--stopped';
        const statusLabel = p.is_running ? 'Running' : 'Stopped';
        const viewedBadge = p.is_viewed ? ' <span class="pd-badge pd-badge--viewed">Viewed</span>' : '';

        const hour = p.stats?.hour || {};
        const day = p.stats?.day || {};
        const overall = p.stats?.overall || {};

        const stopBtn = p.is_running && !p.is_viewed
            ? `<button class="btn btn--danger btn--sm pd-stop-btn" data-project-id="${p.id}">Stop</button>`
            : p.is_running && p.is_viewed
                ? '<span class="pd-current-label">Current</span>'
                : '<span class="pd-stopped-label">-</span>';

        return `
            <div class="pd-grid__row ${p.is_viewed ? 'pd-grid__row--viewed' : ''}">
                <span class="pd-col pd-col--name">
                    <strong>${this._escapeHtml(p.name)}</strong>${viewedBadge}
                </span>
                <span class="pd-col pd-col--status">
                    <span class="pd-status ${statusClass}">${statusLabel}</span>
                </span>
                <span class="pd-col pd-col--agents">${p.agent_count || 0}</span>
                <span class="pd-col pd-col--stats">${this._renderStats(hour)}</span>
                <span class="pd-col pd-col--stats">${this._renderStats(day)}</span>
                <span class="pd-col pd-col--stats">${this._renderStats(overall)}</span>
                <span class="pd-col pd-col--actions">${stopBtn}</span>
            </div>
        `;
    },

    _renderStats(stats) {
        if (!stats || !stats.requests) {
            return '<span class="pd-stats-empty">-</span>';
        }
        const cost = typeof stats.cost_usd === 'number' ? `$${stats.cost_usd.toFixed(2)}` : '-';
        const errors = stats.errors || 0;
        const errClass = errors > 0 ? 'pd-stats__errors--active' : '';
        return `
            <span class="pd-stats">
                <span class="pd-stats__requests">${stats.requests} req</span>
                <span class="pd-stats__cost">${cost}</span>
                ${errors > 0 ? `<span class="pd-stats__errors ${errClass}">${errors} err</span>` : ''}
            </span>
        `;
    },

    _escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    },
};
