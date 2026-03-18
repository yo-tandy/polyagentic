const ProjectSelector = {
    selectEl: null,
    newBtn: null,
    projects: [],
    _currentProjectId: null,
    _pendingSwitchId: null,

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
                location.reload();
            } catch (e) {
                console.error('Failed to switch project:', e);
                location.reload();
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
                        location.reload();
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

    _initBeforeUnload() {
        window.addEventListener('beforeunload', (e) => {
            // Check if any projects are running
            const runningProjects = this.projects.filter(p => p.is_running);
            if (runningProjects.length > 0) {
                e.preventDefault();
                e.returnValue = 'Agents are still running. Are you sure you want to leave?';
            }
        });
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
            location.reload();
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
    },

    hideNewProjectModal() {
        const modal = document.getElementById('new-project-modal');
        if (modal) modal.classList.remove('active');
        const form = document.getElementById('new-project-form');
        if (form) form.reset();
        const status = document.getElementById('new-project-status');
        if (status) status.textContent = '';
    },

    async createProject() {
        const nameInput = document.getElementById('new-project-name');
        const descInput = document.getElementById('new-project-description');
        const fileInput = document.getElementById('new-project-files');
        const statusEl = document.getElementById('new-project-status');

        const name = nameInput?.value?.trim();
        const description = descInput?.value?.trim();
        const files = fileInput?.files || [];

        if (!name) {
            if (statusEl) {
                statusEl.textContent = 'Project name is required';
                statusEl.className = 'form-status form-status--error';
            }
            return;
        }

        if (statusEl) {
            statusEl.textContent = 'Creating project...';
            statusEl.className = 'form-status form-status--info';
        }

        try {
            const res = await fetch('/api/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description: description || '' }),
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

            const actRes = await fetch(`/api/projects/${data.project.id}/activate`, {
                method: 'POST',
            });
            if (!actRes.ok) {
                console.error('Failed to activate project');
                location.reload();
                return;
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

            location.reload();
        } catch (err) {
            console.error('Failed to create project:', err);
            if (statusEl) {
                statusEl.textContent = 'Failed to create project';
                statusEl.className = 'form-status form-status--error';
            }
        }
    },
};
