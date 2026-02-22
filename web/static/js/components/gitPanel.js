const GitPanel = {
    container: null,

    init(containerId) {
        this.container = document.getElementById(containerId);
    },

    render(branches, log) {
        if (!this.container) return;
        let html = '<div style="margin-bottom: 12px;">';
        html += '<div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Branches</div>';
        html += (branches || []).map(b => `
            <div class="git-branch">
                <span class="git-branch__icon">&#9679;</span>
                ${this._escapeHtml(b)}
            </div>
        `).join('');
        html += '</div>';

        html += '<div>';
        html += '<div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Recent Commits</div>';
        html += (log || []).slice(0, 10).map(c => `
            <div class="git-commit">
                <span class="git-commit__hash">${c.short_hash}</span>
                ${this._escapeHtml(c.message)}
            </div>
        `).join('');
        html += '</div>';

        this.container.innerHTML = html;
    },

    _escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
};
