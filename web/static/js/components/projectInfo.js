// Project Info Modal — shows project metadata, team, stats, per-model breakdown

const ProjectInfo = {
    init() {
        document.getElementById('project-info-btn')?.addEventListener('click', () => this.show());
        document.getElementById('project-info-close')?.addEventListener('click', () => this.hide());
        document.getElementById('project-info-modal')?.addEventListener('click', (e) => {
            if (e.target.id === 'project-info-modal') this.hide();
        });
    },

    async show() {
        const modal = document.getElementById('project-info-modal');
        const body = document.getElementById('project-info-body');
        if (!modal || !body) return;

        body.innerHTML = '<div class="project-info__loading">Loading...</div>';
        modal.classList.add('active');

        try {
            const res = await fetch('/api/projects/active/info');
            if (!res.ok) throw new Error(`${res.status}`);
            const data = await res.json();
            if (data.error) {
                body.innerHTML = `<div class="project-info__loading">${this._esc(data.error)}</div>`;
                return;
            }
            this._render(body, data);
        } catch (err) {
            body.innerHTML = `<div class="project-info__loading">Failed to load project info</div>`;
        }
    },

    hide() {
        document.getElementById('project-info-modal')?.classList.remove('active');
    },

    _render(body, data) {
        const p = data.project;
        const t = data.totals;
        const team = data.team || [];
        const byModel = data.by_model || {};

        const createdDate = p.created_at ? new Date(p.created_at).toLocaleDateString('en-US', {
            year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit'
        }) : 'N/A';

        body.innerHTML = `
            <!-- Project Metadata -->
            <div class="project-info__section">
                <h3 class="project-info__section-title">Project</h3>
                <div class="project-info__meta">
                    <div class="project-info__meta-row">
                        <span class="project-info__label">Name</span>
                        <span class="project-info__value">${this._esc(p.name || p.id)}</span>
                    </div>
                    <div class="project-info__meta-row">
                        <span class="project-info__label">Status</span>
                        <span class="project-info__value project-info__status--${p.status}">${this._esc(p.status)}</span>
                    </div>
                    <div class="project-info__meta-row">
                        <span class="project-info__label">Created</span>
                        <span class="project-info__value">${createdDate}</span>
                    </div>
                    ${p.description ? `
                    <div class="project-info__meta-row project-info__meta-row--full">
                        <span class="project-info__label">Description</span>
                        <div class="project-info__description">${this._esc(p.description)}</div>
                    </div>` : ''}
                </div>
            </div>

            <!-- Totals -->
            <div class="project-info__section">
                <h3 class="project-info__section-title">Overall Stats</h3>
                <div class="project-info__stats-grid">
                    <div class="project-info__stat">
                        <span class="project-info__stat-value">${t.agents}</span>
                        <span class="project-info__stat-label">Agents</span>
                    </div>
                    <div class="project-info__stat">
                        <span class="project-info__stat-value">${t.total_requests}</span>
                        <span class="project-info__stat-label">Requests</span>
                    </div>
                    <div class="project-info__stat">
                        <span class="project-info__stat-value">${this._fmtDuration(t.total_duration_ms)}</span>
                        <span class="project-info__stat-label">Processing Time</span>
                    </div>
                    <div class="project-info__stat">
                        <span class="project-info__stat-value">$${t.total_cost_usd.toFixed(2)}</span>
                        <span class="project-info__stat-label">Total Cost</span>
                    </div>
                    <div class="project-info__stat">
                        <span class="project-info__stat-value">${this._fmtTokens(t.total_input_tokens)}</span>
                        <span class="project-info__stat-label">Input Tokens</span>
                    </div>
                    <div class="project-info__stat">
                        <span class="project-info__stat-value">${this._fmtTokens(t.total_output_tokens)}</span>
                        <span class="project-info__stat-label">Output Tokens</span>
                    </div>
                </div>
            </div>

            <!-- Per-Model Breakdown -->
            ${Object.keys(byModel).length > 0 ? `
            <div class="project-info__section">
                <h3 class="project-info__section-title">Usage by Model</h3>
                <table class="project-info__table">
                    <thead>
                        <tr>
                            <th>Model</th>
                            <th>Agents</th>
                            <th>Requests</th>
                            <th>Cost</th>
                            <th>Input Tokens</th>
                            <th>Output Tokens</th>
                            <th>Time</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${Object.entries(byModel).map(([model, s]) => `
                        <tr>
                            <td><strong>${this._esc(model)}</strong></td>
                            <td>${s.agents}</td>
                            <td>${s.requests}</td>
                            <td>$${s.cost_usd.toFixed(2)}</td>
                            <td>${this._fmtTokens(s.input_tokens)}</td>
                            <td>${this._fmtTokens(s.output_tokens)}</td>
                            <td>${this._fmtDuration(s.duration_ms)}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
            </div>` : ''}

            <!-- Team -->
            <div class="project-info__section">
                <h3 class="project-info__section-title">Team</h3>
                <table class="project-info__table">
                    <thead>
                        <tr>
                            <th>Agent</th>
                            <th>Role</th>
                            <th>Model</th>
                            <th>Requests</th>
                            <th>Errors</th>
                            <th>Cost</th>
                            <th>Tokens (in/out)</th>
                            <th>Time</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${team.map(a => `
                        <tr>
                            <td><strong>${this._esc(a.name)}</strong> <span class="project-info__agent-id">${this._esc(a.id)}</span></td>
                            <td>${this._esc(a.role)}</td>
                            <td>${this._esc(a.model)}</td>
                            <td>${a.request_count}</td>
                            <td>${a.error_count || 0}</td>
                            <td>$${a.total_cost_usd.toFixed(2)}</td>
                            <td>${this._fmtTokens(a.total_input_tokens)} / ${this._fmtTokens(a.total_output_tokens)}</td>
                            <td>${this._fmtDuration(a.total_duration_ms)}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>
            </div>
        `;
    },

    _fmtDuration(ms) {
        if (!ms || ms === 0) return '0s';
        const secs = Math.floor(ms / 1000);
        if (secs < 60) return `${secs}s`;
        const mins = Math.floor(secs / 60);
        const remSecs = secs % 60;
        if (mins < 60) return `${mins}m ${remSecs}s`;
        const hrs = Math.floor(mins / 60);
        const remMins = mins % 60;
        return `${hrs}h ${remMins}m`;
    },

    _fmtTokens(n) {
        if (!n || n === 0) return '0';
        return n.toLocaleString();
    },

    _esc(text) {
        const d = document.createElement('div');
        d.textContent = text || '';
        return d.innerHTML;
    }
};
