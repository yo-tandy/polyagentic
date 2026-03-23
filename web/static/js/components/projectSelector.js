const ProjectSelector = {
    selectEl: null,
    newBtn: null,
    projects: [],
    _currentProjectId: null,
    _pendingSwitchId: null,

    // Fixed agents shown in advanced settings (display name → agent_id)
    FIXED_AGENTS: [
        { id: 'manny',  label: 'Dev Manager (Manny)' },
        { id: 'jerry',  label: 'Project Manager (Jerry)' },
        { id: 'perry',  label: 'Product Manager (Perry)' },
        { id: 'innes',  label: 'Integrator (Innes)' },
        { id: 'rory',   label: 'Robot Resources (Rory)' },
    ],

    PROVIDERS: ['claude-cli', 'claude-api', 'openai', 'gemini'],

    PROVIDER_MODELS: {
        'claude-cli':  ['sonnet', 'opus', 'haiku'],
        'claude-api':  ['sonnet', 'opus', 'haiku'],
        'openai':      ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-mini', 'gpt-4.1-nano', 'o3', 'o3-mini', 'o4-mini'],
        'gemini':      ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.0-flash-lite'],
    },

    PROVIDER_DEFAULTS: {
        'claude-cli': 'sonnet',
        'claude-api': 'sonnet',
        'openai':     'gpt-4o',
        'gemini':     'gemini-2.0-flash',
    },

    init() {
        this.selectEl = document.getElementById('project-select');
        this.newBtn = document.getElementById('new-project-btn');

        if (this.newBtn) {
            this.newBtn.addEventListener('click', () => this.showNewProjectModal());
        }

        if (this.selectEl) {
            this.selectEl.addEventListener('change', () => {
                const projectId = this.selectEl.value;
                if (projectId && projectId !== this._currentProjectId) {
                    this._handleSwitch(projectId);
                }
            });
        }

        // New project modal handlers
        const closeBtn = document.getElementById('new-project-close');
        if (closeBtn) closeBtn.addEventListener('click', () => this.hideNewProjectModal());

        const overlay = document.getElementById('new-project-modal');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this.hideNewProjectModal();
            });
        }

        const form = document.getElementById('new-project-form');
        if (form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.createProject();
            });
        }

        // Switch confirmation modal handlers
        this._initSwitchConfirmModal();
        this._initInactiveConfirmModal();
        this._initReactivateBar();

        // beforeunload handler
        this._initBeforeUnload();

        this.loadProjects();
    },

    _initSwitchConfirmModal() {
        const modal = document.getElementById('switch-confirm-modal');
        const closeBtn = document.getElementById('switch-confirm-close');
        const keepBtn = document.getElementById('switch-keep-running-btn');
        const stopBtn = document.getElementById('switch-stop-btn');
        const cancelBtn = document.getElementById('switch-cancel-btn');

        if (closeBtn) closeBtn.addEventListener('click', () => this._cancelSwitch());
        if (cancelBtn) cancelBtn.addEventListener('click', () => this._cancelSwitch());
        if (modal) modal.addEventListener('click', (e) => {
            if (e.target === modal) this._cancelSwitch();
        });

        if (keepBtn) keepBtn.addEventListener('click', () => {
            this._closeSwitchModal();
            this._doActivate(this._pendingSwitchId);
        });

        if (stopBtn) stopBtn.addEventListener('click', async () => {
            this._closeSwitchModal();
            const oldId = this._currentProjectId;
            // Activate new project first (changes viewed project), then stop old
            try {
                const res = await fetch(`/api/projects/${this._pendingSwitchId}/activate`, { method: 'POST' });
                if (res.ok && oldId) {
                    await fetch(`/api/projects/${oldId}/stop`, { method: 'POST' });
                }
                this._intentionalReload();
            } catch (e) {
                console.error('Failed to switch project:', e);
                this._intentionalReload();
            }
        });
    },

    _initInactiveConfirmModal() {
        const modal = document.getElementById('inactive-confirm-modal');
        const closeBtn = document.getElementById('inactive-confirm-close');
        const activateBtn = document.getElementById('inactive-activate-btn');
        const readonlyBtn = document.getElementById('inactive-readonly-btn');
        const cancelBtn = document.getElementById('inactive-cancel-btn');

        if (closeBtn) closeBtn.addEventListener('click', () => this._cancelSwitch());
        if (cancelBtn) cancelBtn.addEventListener('click', () => this._cancelSwitch());
        if (modal) modal.addEventListener('click', (e) => {
            if (e.target === modal) this._cancelSwitch();
        });

        if (activateBtn) activateBtn.addEventListener('click', () => {
            this._closeInactiveModal();
            this._doActivate(this._pendingSwitchId);
        });

        if (readonlyBtn) readonlyBtn.addEventListener('click', () => {
            this._closeInactiveModal();
            // For now, just activate normally (full read-only view is future work)
            this._doActivate(this._pendingSwitchId);
        });
    },

    _initReactivateBar() {
        const btn = document.getElementById('reactivate-btn');
        if (btn) {
            btn.addEventListener('click', async () => {
                if (!this._currentProjectId) return;
                btn.disabled = true;
                btn.textContent = 'Activating...';
                try {
                    const res = await fetch(`/api/projects/${this._currentProjectId}/activate`, { method: 'POST' });
                    if (res.ok) {
                        this._intentionalReload();
                    } else {
                        btn.disabled = false;
                        btn.textContent = 'Re-activate Project';
                    }
                } catch (e) {
                    console.error('Failed to reactivate:', e);
                    btn.disabled = false;
                    btn.textContent = 'Re-activate Project';
                }
            });
        }
    },

    _intentionalReload() {
        this._skipUnloadWarning = true;
        window.location.reload();
    },

    _initBeforeUnload() {
        // Intentionally empty — agents keep running server-side regardless
        // of browser state, so the "leave site?" warning is unnecessary.
    },

    async _handleSwitch(targetId) {
        const targetProject = this.projects.find(p => p.id === targetId);
        const currentProject = this.projects.find(p => p.id === this._currentProjectId);

        if (!targetProject) {
            this._doActivate(targetId);
            return;
        }

        // If the target project is not running, show inactive confirmation
        if (!targetProject.is_running) {
            this._pendingSwitchId = targetId;
            const title = document.getElementById('inactive-confirm-title');
            const text = document.getElementById('inactive-confirm-text');
            if (title) title.textContent = `"${targetProject.name}" is not running`;
            if (text) text.textContent = 'Would you like to re-activate it? This will start its agents.';
            const modal = document.getElementById('inactive-confirm-modal');
            if (modal) modal.classList.add('active');
            return;
        }

        // If the current project is running, ask about keeping it running
        if (currentProject && currentProject.is_running) {
            this._pendingSwitchId = targetId;
            const title = document.getElementById('switch-confirm-title');
            const text = document.getElementById('switch-confirm-text');
            if (title) title.textContent = `Switching from "${currentProject.name}"`;
            if (text) text.textContent = 'Do you want to keep this project running in the background?';
            const modal = document.getElementById('switch-confirm-modal');
            if (modal) modal.classList.add('active');
            return;
        }

        // Default: just switch
        this._doActivate(targetId);
    },

    _cancelSwitch() {
        // Reset dropdown to current project
        if (this.selectEl && this._currentProjectId) {
            this.selectEl.value = this._currentProjectId;
        }
        this._pendingSwitchId = null;
        this._closeSwitchModal();
        this._closeInactiveModal();
    },

    _closeSwitchModal() {
        const modal = document.getElementById('switch-confirm-modal');
        if (modal) modal.classList.remove('active');
    },

    _closeInactiveModal() {
        const modal = document.getElementById('inactive-confirm-modal');
        if (modal) modal.classList.remove('active');
    },

    async _doActivate(projectId) {
        if (this.selectEl) this.selectEl.disabled = true;

        const statusEl = document.getElementById('connection-status');
        if (statusEl) {
            statusEl.textContent = 'Switching project...';
            statusEl.classList.remove('connected');
        }

        try {
            const res = await fetch(`/api/projects/${projectId}/activate`, {
                method: 'POST',
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                console.error('Failed to switch project:', data.error || res.statusText);
                if (this.selectEl) this.selectEl.disabled = false;
                if (this._currentProjectId && this.selectEl) {
                    this.selectEl.value = this._currentProjectId;
                }
                return;
            }
            this._intentionalReload();
        } catch (err) {
            console.error('Failed to switch project:', err);
            if (this.selectEl) this.selectEl.disabled = false;
        }
    },

    async loadProjects() {
        const [projectsRes, activeRes] = await Promise.all([
            safeFetch('/api/projects', { projects: [] }),
            safeFetch('/api/projects/active', {}),
        ]);

        this.projects = projectsRes.projects || [];
        const activeProject = activeRes.project || null;
        const activeId = activeProject ? activeProject.id : null;
        this._currentProjectId = activeId;

        if (this.selectEl) {
            this.selectEl.innerHTML = this.projects.map(p => {
                const selected = p.id === activeId ? 'selected' : '';
                const runIcon = p.is_running ? '\u{1F7E2}' : '\u26AA';
                return `<option value="${p.id}" ${selected}>${runIcon} ${p.name}</option>`;
            }).join('');
        }

        // Show reactivate bar if current project is not running
        const currentProject = this.projects.find(p => p.id === activeId);
        const bar = document.getElementById('reactivate-bar');
        if (bar && currentProject && !currentProject.is_running) {
            bar.style.display = 'flex';
        }
    },

    switchProject(projectId) {
        this._handleSwitch(projectId);
    },

    showNewProjectModal() {
        const modal = document.getElementById('new-project-modal');
        if (modal) modal.classList.add('active');
        const nameInput = document.getElementById('new-project-name');
        if (nameInput) nameInput.focus();
        this._populateAgentModels();
    },

    _populateAgentModels() {
        const container = document.getElementById('new-project-agent-models');
        if (!container) return;

        container.innerHTML = `
            <p class="advanced-settings__hint">Choose the provider and model for each agent. Defaults to Claude CLI / Sonnet.</p>
            ${this.FIXED_AGENTS.map(agent => `
                <div class="agent-model-row" data-agent-id="${agent.id}">
                    <span class="agent-model-row__label">${agent.label}</span>
                    <select class="agent-model-row__provider" data-agent-id="${agent.id}">
                        ${this.PROVIDERS.map(p =>
                            `<option value="${p}"${p === 'claude-cli' ? ' selected' : ''}>${p}</option>`
                        ).join('')}
                    </select>
                    <select class="agent-model-row__model" data-agent-id="${agent.id}">
                        ${this.PROVIDER_MODELS['claude-cli'].map(m =>
                            `<option value="${m}"${m === 'sonnet' ? ' selected' : ''}>${m}</option>`
                        ).join('')}
                    </select>
                </div>
            `).join('')}
        `;

        // Bind provider change → update model dropdown
        container.querySelectorAll('.agent-model-row__provider').forEach(sel => {
            sel.addEventListener('change', () => {
                const agentId = sel.dataset.agentId;
                const provider = sel.value;
                const modelSel = container.querySelector(`.agent-model-row__model[data-agent-id="${agentId}"]`);
                if (!modelSel) return;
                const models = this.PROVIDER_MODELS[provider] || this.PROVIDER_MODELS['claude-cli'];
                const defaultModel = this.PROVIDER_DEFAULTS[provider] || models[0];
                modelSel.innerHTML = models.map(m =>
                    `<option value="${m}"${m === defaultModel ? ' selected' : ''}>${m}</option>`
                ).join('');
            });
        });
    },

    _getAgentModelSettings() {
        const container = document.getElementById('new-project-agent-models');
        if (!container) return {};
        const settings = {};
        this.FIXED_AGENTS.forEach(agent => {
            const provSel = container.querySelector(`.agent-model-row__provider[data-agent-id="${agent.id}"]`);
            const modelSel = container.querySelector(`.agent-model-row__model[data-agent-id="${agent.id}"]`);
            if (provSel && modelSel) {
                const provider = provSel.value;
                const model = modelSel.value;
                // Only include non-default settings
                if (provider !== 'claude-cli' || model !== 'sonnet') {
                    settings[agent.id] = { provider, model };
                }
            }
        });
        return settings;
    },

    hideNewProjectModal() {
        const modal = document.getElementById('new-project-modal');
        if (modal) modal.classList.remove('active');
        const form = document.getElementById('new-project-form');
        if (form) form.reset();
        const status = document.getElementById('new-project-status');
        if (status) status.textContent = '';
        // Collapse advanced settings
        const details = document.getElementById('new-project-advanced');
        if (details) details.removeAttribute('open');
    },

    async createProject() {
        const nameInput = document.getElementById('new-project-name');
        const descInput = document.getElementById('new-project-description');
        const gitUrlInput = document.getElementById('new-project-git-url');
        const fileInput = document.getElementById('new-project-files');
        const statusEl = document.getElementById('new-project-status');

        const name = nameInput?.value?.trim();
        const description = descInput?.value?.trim();
        const gitUrl = gitUrlInput?.value?.trim() || '';
        const files = fileInput?.files || [];
        const agentModels = this._getAgentModelSettings();

        if (!name) {
            if (statusEl) {
                statusEl.textContent = 'Project name is required';
                statusEl.className = 'form-status form-status--error';
            }
            return;
        }

        if (statusEl) {
            statusEl.textContent = gitUrl ? 'Creating project and cloning repository...' : 'Creating project...';
            statusEl.className = 'form-status form-status--info';
        }

        try {
            const res = await fetch('/api/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    description: description || '',
                    agent_models: agentModels,
                    git_url: gitUrl,
                }),
            });

            if (!res.ok) {
                const err = await res.json();
                if (statusEl) {
                    statusEl.textContent = err.error || 'Failed to create project';
                    statusEl.className = 'form-status form-status--error';
                }
                return;
            }

            const data = await res.json();

            if (statusEl) {
                statusEl.textContent = 'Activating project...';
                statusEl.className = 'form-status form-status--info';
            }

            const actRes = await fetch(`/api/projects/${data.project.id}/activate`, {
                method: 'POST',
            });
            if (!actRes.ok) {
                console.error('Failed to activate project');
                this._intentionalReload();
                return;
            }

            // Apply agent model/provider overrides after activation
            const modelEntries = Object.entries(agentModels);
            if (modelEntries.length > 0) {
                if (statusEl) {
                    statusEl.textContent = 'Configuring agent models...';
                    statusEl.className = 'form-status form-status--info';
                }
                for (const [agentId, cfg] of modelEntries) {
                    try {
                        await fetch(`/api/sessions/${agentId}/provider`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ provider: cfg.provider }),
                        });
                    } catch (e) {
                        console.warn(`Failed to set provider for ${agentId}:`, e);
                    }
                }
            }

            if (files.length > 0) {
                if (statusEl) {
                    statusEl.textContent = `Uploading ${files.length} file(s)...`;
                    statusEl.className = 'form-status form-status--info';
                }
                for (const file of files) {
                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('context', 'project');
                    formData.append('description', `Uploaded during project creation for "${name}"`);
                    await fetch('/api/upload', { method: 'POST', body: formData });
                }
            }

            this._intentionalReload();
        } catch (err) {
            console.error('Failed to create project:', err);
            if (statusEl) {
                statusEl.textContent = 'Failed to create project';
                statusEl.className = 'form-status form-status--error';
            }
        }
    },
};
