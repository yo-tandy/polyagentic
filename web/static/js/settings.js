// Polyagentic Settings Page

const Settings = {
    user: null,
    org: null,

    async init() {
        this.setupTabs();
        await this.loadUser();
        await Promise.all([
            this.loadOrg(),
            this.loadMembers(),
            this.loadInvites(),
            this.loadApiKeys(),
        ]);
        await this.loadTemplates();
        this.bindEvents();
    },

    // ── Tab switching ──

    setupTabs() {
        document.querySelectorAll('.settings-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));
                tab.classList.add('active');
                const panel = document.getElementById(`panel-${tab.dataset.tab}`);
                if (panel) panel.classList.add('active');
            });
        });
    },

    // ── Data loading ──

    async safeFetch(url, fallback = {}) {
        try {
            const res = await fetch(url);
            if (res.status === 401) {
                window.location.href = '/auth/login';
                return fallback;
            }
            if (!res.ok) return fallback;
            return await res.json();
        } catch {
            return fallback;
        }
    },

    async loadUser() {
        const raw = await this.safeFetch('/auth/me', null);
        const user = raw?.user || raw;
        if (!user || !user.id) return;
        this.user = user;

        // Header
        const nameEl = document.getElementById('header-user-name');
        const avatarEl = document.getElementById('header-avatar');
        if (nameEl) nameEl.textContent = user.name || user.email;
        if (avatarEl && user.picture_url) {
            avatarEl.src = user.picture_url;
            avatarEl.style.display = 'inline-block';
        }

        // Profile tab
        document.getElementById('profile-name').value = user.name || '';
        document.getElementById('profile-email').value = user.email || '';
        document.getElementById('profile-org').value = user.org_id || '';
    },

    async loadOrg() {
        const data = await this.safeFetch('/api/orgs/current', {});
        this.org = data;
        const nameInput = document.getElementById('org-name');
        if (nameInput && data.name) nameInput.value = data.name;
        // Update profile org field
        if (data.name) {
            document.getElementById('profile-org').value = data.name;
        }
    },

    async loadMembers() {
        const data = await this.safeFetch('/api/orgs/members', { members: [] });
        const container = document.getElementById('members-container');
        const members = data.members || [];

        if (members.length === 0) {
            container.innerHTML = '<div class="settings-empty">No members found</div>';
            return;
        }

        let html = `<table class="members-table">
            <thead><tr><th>Name</th><th>Email</th><th>Joined</th></tr></thead>
            <tbody>`;
        for (const m of members) {
            const joined = m.created_at
                ? new Date(m.created_at).toLocaleDateString()
                : '—';
            html += `<tr>
                <td>${this.esc(m.name)}</td>
                <td>${this.esc(m.email)}</td>
                <td>${joined}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    },

    async loadInvites() {
        const data = await this.safeFetch('/api/orgs/invites', { invites: [] });
        const container = document.getElementById('invites-container');
        const invites = data.invites || [];

        if (invites.length === 0) {
            container.innerHTML = '<div class="settings-empty">No active invite links</div>';
            return;
        }

        let html = '';
        for (const inv of invites) {
            const expires = inv.expires_at
                ? new Date(inv.expires_at).toLocaleDateString()
                : 'Never';
            const uses = inv.max_uses ? `${inv.use_count}/${inv.max_uses}` : `${inv.use_count} uses`;
            html += `<div class="invite-item">
                <div>
                    <span class="invite-item__token" title="Click to copy" data-token="${this.esc(inv.token)}">
                        ${this.esc(inv.token.substring(0, 12))}...
                    </span>
                    <span class="invite-item__meta">${uses} · Expires: ${expires}</span>
                </div>
                <div class="invite-item__actions">
                    <button class="settings-btn settings-btn--small settings-btn--secondary copy-invite"
                            data-token="${this.esc(inv.token)}">Copy</button>
                    <button class="settings-btn settings-btn--small settings-btn--danger delete-invite"
                            data-id="${inv.id}">Delete</button>
                </div>
            </div>`;
        }
        container.innerHTML = html;

        // Bind copy/delete handlers
        container.querySelectorAll('.copy-invite').forEach(btn => {
            btn.addEventListener('click', () => {
                navigator.clipboard.writeText(btn.dataset.token);
                this.showStatus('invite-status', 'Copied to clipboard', 'success');
            });
        });
        container.querySelectorAll('.delete-invite').forEach(btn => {
            btn.addEventListener('click', () => this.deleteInvite(btn.dataset.id));
        });
    },

    async loadApiKeys() {
        // Load org-scoped config entries to check which keys are set
        const data = await this.safeFetch('/api/config/entries?scope=org', { entries: [] });
        const entries = data.entries || [];
        const keyMap = {
            'ANTHROPIC_API_KEY': 'key-anthropic',
            'OPENAI_API_KEY': 'key-openai',
            'GOOGLE_API_KEY': 'key-google',
        };
        for (const entry of entries) {
            const inputId = keyMap[entry.key];
            if (inputId) {
                const input = document.getElementById(inputId);
                if (input && entry.value) {
                    // Show masked version
                    input.placeholder = entry.value.substring(0, 8) + '...' + '••••';
                }
            }
        }
    },

    // ── Event binding ──

    bindEvents() {
        // Save org name
        document.getElementById('save-org-name')?.addEventListener('click', () => this.saveOrgName());

        // Save API keys
        document.querySelectorAll('[data-key]').forEach(btn => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.key;
                const input = document.getElementById(btn.dataset.input);
                if (input && input.value.trim()) {
                    this.saveApiKey(key, input.value.trim());
                }
            });
        });

        // Create invite
        document.getElementById('create-invite-btn')?.addEventListener('click', () => this.createInvite());
    },

    // ── Actions ──

    async saveOrgName() {
        const name = document.getElementById('org-name')?.value?.trim();
        if (!name) return;
        try {
            const res = await fetch('/api/orgs/current', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            if (res.ok) {
                this.showStatus('org-name-status', 'Saved', 'success');
            } else {
                this.showStatus('org-name-status', 'Failed to save', 'error');
            }
        } catch {
            this.showStatus('org-name-status', 'Failed to save', 'error');
        }
    },

    async saveApiKey(key, value) {
        try {
            const res = await fetch('/api/config/entries', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scope: 'org',
                    scope_id: this.user?.org_id || 'default',
                    key: key,
                    value: value,
                    value_type: 'secret',
                    description: `${key} (org-scoped)`,
                }),
            });
            if (res.ok) {
                this.showStatus('api-key-status', `${key} saved`, 'success');
                // Clear input and update placeholder
                const inputId = {
                    'ANTHROPIC_API_KEY': 'key-anthropic',
                    'OPENAI_API_KEY': 'key-openai',
                    'GOOGLE_API_KEY': 'key-google',
                }[key];
                if (inputId) {
                    const input = document.getElementById(inputId);
                    if (input) {
                        input.value = '';
                        input.placeholder = value.substring(0, 8) + '...••••';
                    }
                }
            } else {
                this.showStatus('api-key-status', 'Failed to save', 'error');
            }
        } catch {
            this.showStatus('api-key-status', 'Failed to save', 'error');
        }
    },

    async createInvite() {
        try {
            const res = await fetch('/api/orgs/invites', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            if (res.ok) {
                this.showStatus('invite-status', 'Invite created', 'success');
                await this.loadInvites();
            } else {
                const data = await res.json().catch(() => ({}));
                this.showStatus('invite-status', data.error || 'Failed', 'error');
            }
        } catch {
            this.showStatus('invite-status', 'Failed to create invite', 'error');
        }
    },

    async deleteInvite(id) {
        try {
            const res = await fetch(`/api/orgs/invites/${id}`, { method: 'DELETE' });
            if (res.ok) {
                this.showStatus('invite-status', 'Invite deleted', 'success');
                await this.loadInvites();
            }
        } catch {
            this.showStatus('invite-status', 'Failed to delete', 'error');
        }
    },

    // ── Agent Repository ──

    async loadTemplates() {
        const data = await this.safeFetch('/api/templates', { templates: [] });
        const container = document.getElementById('repo-templates-container');
        const templates = data.templates || [];

        if (templates.length === 0) {
            container.innerHTML = '<div class="settings-empty">No agent templates in the repository</div>';
            return;
        }

        let html = `<table class="members-table">
            <thead><tr><th>Name</th><th>Title</th><th>Scope</th><th>Model</th><th>Actions</th></tr></thead>
            <tbody>`;
        for (const t of templates) {
            const orgLabel = this.org?.name ? this.esc(this.org.name) : 'Org';
            const scopeBadge = t.scope === 'global'
                ? '<span class="scope-badge scope-badge--global">Global</span>'
                : `<span class="scope-badge scope-badge--org">${orgLabel}</span>`;
            html += `<tr data-template-id="${this.esc(t.id)}">
                <td>${this.esc(t.name)}</td>
                <td>${this.esc(t.title)}</td>
                <td>${scopeBadge}</td>
                <td>${this.esc(t.model || 'sonnet')}</td>
                <td class="template-actions">
                    <button class="settings-btn settings-btn--small settings-btn--secondary edit-template"
                            data-id="${this.esc(t.id)}">Edit</button>
                    <button class="settings-btn settings-btn--small settings-btn--danger delete-template"
                            data-id="${this.esc(t.id)}">Delete</button>
                </td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;

        // Bind edit handlers
        container.querySelectorAll('.edit-template').forEach(btn => {
            btn.addEventListener('click', () => this.editTemplate(btn.dataset.id));
        });
        // Bind delete handlers
        container.querySelectorAll('.delete-template').forEach(btn => {
            btn.addEventListener('click', () => this.deleteTemplate(btn.dataset.id));
        });
    },

    async editTemplate(id) {
        const data = await this.safeFetch(`/api/templates/${id}`, null);
        if (!data) return;

        const row = document.querySelector(`tr[data-template-id="${id}"]`);
        if (!row) return;

        // Replace table row with edit form
        const editRow = document.createElement('tr');
        editRow.dataset.templateId = id;
        editRow.innerHTML = `
            <td colspan="5" class="template-edit-cell">
                <div class="template-edit-form">
                    <div class="template-edit-row">
                        <div class="form-group" style="flex:1">
                            <label>Name</label>
                            <input type="text" class="settings-field__input" id="edit-tmpl-name" value="${this.esc(data.name || '')}">
                        </div>
                        <div class="form-group" style="flex:1">
                            <label>Title</label>
                            <input type="text" class="settings-field__input" id="edit-tmpl-title" value="${this.esc(data.title || '')}">
                        </div>
                        <div class="form-group" style="flex:0 0 200px">
                            <label>Scope</label>
                            <select class="settings-field__input" id="edit-tmpl-scope">
                                <option value="org" ${data.scope === 'org' ? 'selected' : ''}>My organization${this.org?.name ? ' (' + this.esc(this.org.name) + ')' : ''}</option>
                                <option value="global" ${data.scope === 'global' ? 'selected' : ''}>Global</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Personality</label>
                        <textarea class="settings-field__input" id="edit-tmpl-personality" rows="3">${this.esc(data.personality || '')}</textarea>
                    </div>
                    <div class="template-edit-actions">
                        <button class="settings-btn settings-btn--primary" id="save-tmpl-btn">Save</button>
                        <button class="settings-btn settings-btn--secondary" id="cancel-tmpl-btn">Cancel</button>
                    </div>
                </div>
            </td>`;
        row.replaceWith(editRow);

        document.getElementById('save-tmpl-btn').addEventListener('click', async () => {
            const name = document.getElementById('edit-tmpl-name')?.value?.trim();
            const title = document.getElementById('edit-tmpl-title')?.value?.trim();
            const personality = document.getElementById('edit-tmpl-personality')?.value?.trim();
            const scope = document.getElementById('edit-tmpl-scope')?.value;
            if (!name || !title) return;
            try {
                const res = await fetch(`/api/templates/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, title, personality, scope }),
                });
                if (res.ok) {
                    await this.loadTemplates();
                }
            } catch (err) {
                console.error('Failed to update template:', err);
            }
        });

        document.getElementById('cancel-tmpl-btn').addEventListener('click', () => {
            this.loadTemplates();
        });
    },

    async deleteTemplate(id) {
        if (!confirm('Delete this agent template? This cannot be undone.')) return;
        try {
            const res = await fetch(`/api/templates/${id}`, { method: 'DELETE' });
            if (res.ok) {
                await this.loadTemplates();
            }
        } catch (err) {
            console.error('Failed to delete template:', err);
        }
    },

    // ── Utilities ──

    showStatus(id, message, type) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = message;
        el.className = `settings-status ${type}`;
        setTimeout(() => {
            el.className = 'settings-status';
        }, 3000);
    },

    esc(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    },
};

document.addEventListener('DOMContentLoaded', () => Settings.init());
