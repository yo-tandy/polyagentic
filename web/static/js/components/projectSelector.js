const ProjectSelector = {
    selectEl: null,
    newBtn: null,
    projects: [],

    init() {
        this.selectEl = document.getElementById('project-select');
        this.newBtn = document.getElementById('new-project-btn');

        if (this.newBtn) {
            this.newBtn.addEventListener('click', () => this.showNewProjectModal());
        }

        if (this.selectEl) {
            this.selectEl.addEventListener('change', () => {
                const projectId = this.selectEl.value;
                if (projectId) {
                    this.switchProject(projectId);
                }
            });
        }

        // Modal handlers
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

        this.loadProjects();
    },

    async loadProjects() {
        const [projectsRes, activeRes] = await Promise.all([
            safeFetch('/api/projects', { projects: [] }),
            safeFetch('/api/projects/active', {}),
        ]);

        this.projects = projectsRes.projects || [];
        const activeProject = activeRes.project || null;
        const activeId = activeProject ? activeProject.id : null;

        if (this.selectEl) {
            this.selectEl.innerHTML = this.projects.map(p => {
                const selected = p.id === activeId ? 'selected' : '';
                return `<option value="${p.id}" ${selected}>${p.name}</option>`;
            }).join('');
        }
    },

    async switchProject(projectId) {
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
                console.error('Failed to switch project:', await res.text());
                if (this.selectEl) this.selectEl.disabled = false;
                return;
            }
            // Full reload to pick up new project state
            location.reload();
        } catch (err) {
            console.error('Failed to switch project:', err);
            if (this.selectEl) this.selectEl.disabled = false;
        }
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
        // Reset form
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
            // 1. Create project
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

            // 2. Activate project (without reload)
            const actRes = await fetch(`/api/projects/${data.project.id}/activate`, {
                method: 'POST',
            });
            if (!actRes.ok) {
                console.error('Failed to activate project');
                location.reload();
                return;
            }

            // 3. Upload files if any
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

            // 4. Reload to pick up new project state
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
