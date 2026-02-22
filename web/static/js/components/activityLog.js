const ActivityLog = {
    container: null,

    init(containerId) {
        this.container = document.getElementById(containerId);
    },

    render(entries) {
        if (!this.container) return;
        this.container.innerHTML = entries.map(e => this._renderEntry(e)).join('');
        this.container.scrollTop = this.container.scrollHeight;
    },

    addEntry(entry) {
        if (!this.container) return;
        this.container.insertAdjacentHTML('beforeend', this._renderEntry(entry));
        this.container.scrollTop = this.container.scrollHeight;
    },

    _renderEntry(e) {
        const time = this._formatTime(e.timestamp);
        const route = `${e.sender} -> ${e.recipient}`;
        return `
            <div class="activity-entry">
                <span class="activity-entry__time">${time}</span>
                <span class="activity-entry__route">${route}</span>
                <span class="activity-entry__content">${this._escapeHtml(e.content_preview || '')}</span>
            </div>
        `;
    },

    _formatTime(ts) {
        try {
            const d = new Date(ts);
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch {
            return '';
        }
    },

    _escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
};
