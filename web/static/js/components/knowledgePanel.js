const KnowledgePanel = {
    container: null,
    documents: [],
    categories: ['specs', 'design', 'architecture', 'planning', 'history'],
    _selectedDocId: null,

    init(containerId) {
        this.container = document.getElementById(containerId);
        this._bindViewerEvents();
    },

    async load() {
        const res = await safeFetch('/api/knowledge', { documents: [] });
        this.documents = res.documents || [];
        this.render();
    },

    render() {
        if (!this.container) return;

        if (this.documents.length === 0) {
            this.container.innerHTML = `
                <div class="kb-empty">
                    <div class="kb-empty__text">No documents yet</div>
                    <div class="kb-empty__hint">Agents will create specs, design docs, and plans as they work</div>
                </div>
            `;
            return;
        }

        // Group by category
        const grouped = {};
        for (const cat of this.categories) {
            const catDocs = this.documents.filter(d => d.category === cat);
            if (catDocs.length > 0) {
                grouped[cat] = catDocs;
            }
        }

        const html = Object.entries(grouped).map(([cat, docs]) => `
            <div class="kb-category">
                <div class="kb-category__title">${cat}</div>
                ${docs.map(d => `
                    <div class="kb-doc" data-doc-id="${d.id}">
                        <div class="kb-doc__title">${this._escapeHtml(d.title)}</div>
                        <div class="kb-doc__meta">
                            <span class="kb-doc__author">${d.created_by || 'unknown'}</span>
                            <span class="kb-doc__time">${this._timeAgo(d.updated_at || d.created_at)}</span>
                        </div>
                    </div>
                `).join('')}
            </div>
        `).join('');

        this.container.innerHTML = html;

        // Bind click to open viewer modal
        this.container.querySelectorAll('.kb-doc').forEach(el => {
            el.addEventListener('click', () => {
                const docId = el.dataset.docId;
                this._openViewer(docId);
            });
        });
    },

    // ── Viewer Modal ──

    _bindViewerEvents() {
        document.getElementById('kb-viewer-close')?.addEventListener('click', () => this._closeViewer());

        const overlay = document.getElementById('kb-viewer-modal');
        overlay?.addEventListener('click', (e) => {
            if (e.target === overlay) this._closeViewer();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this._selectedDocId) this._closeViewer();
        });
    },

    async _openViewer(docId) {
        this._selectedDocId = docId;
        const modal = document.getElementById('kb-viewer-modal');
        modal?.classList.add('active');

        this._renderSidebar(docId);
        await this._loadAndRenderDoc(docId);
    },

    _closeViewer() {
        this._selectedDocId = null;
        const modal = document.getElementById('kb-viewer-modal');
        modal?.classList.remove('active');
    },

    _renderSidebar(selectedId) {
        const sidebar = document.getElementById('kb-viewer-sidebar');
        if (!sidebar) return;

        if (this.documents.length === 0) {
            sidebar.innerHTML = '<div class="kb-viewer__empty">No documents</div>';
            return;
        }

        const grouped = {};
        for (const cat of this.categories) {
            const catDocs = this.documents.filter(d => d.category === cat);
            if (catDocs.length > 0) grouped[cat] = catDocs;
        }

        const html = Object.entries(grouped).map(([cat, docs]) => `
            <div class="kb-sidebar-category">${cat}</div>
            ${docs.map(d => `
                <div class="kb-sidebar-item ${d.id === selectedId ? 'kb-sidebar-item--active' : ''}"
                     data-doc-id="${d.id}">
                    ${this._escapeHtml(d.title)}
                </div>
            `).join('')}
        `).join('');

        sidebar.innerHTML = html;

        // Bind sidebar clicks
        sidebar.querySelectorAll('.kb-sidebar-item').forEach(el => {
            el.addEventListener('click', () => {
                const docId = el.dataset.docId;
                if (docId !== this._selectedDocId) {
                    this._selectedDocId = docId;
                    this._renderSidebar(docId);
                    this._loadAndRenderDoc(docId);
                }
            });
        });
    },

    async _loadAndRenderDoc(docId) {
        const canvas = document.getElementById('kb-viewer-canvas');
        if (!canvas) return;

        canvas.innerHTML = '<div class="kb-viewer__empty">Loading...</div>';

        const res = await safeFetch(`/api/knowledge/${docId}`, {});
        if (!res || !res.document) {
            canvas.innerHTML = '<div class="kb-viewer__empty">Document not found</div>';
            return;
        }

        const doc = res.document;
        const content = res.content || 'No content';

        // Render markdown content
        const renderedContent = this._renderMarkdown(content);

        canvas.innerHTML = `
            <div class="kb-viewer__doc-header">
                <h1 class="kb-viewer__doc-title">${this._escapeHtml(doc.title)}</h1>
                <div class="kb-viewer__doc-meta">
                    <span class="kb-viewer__doc-category">${doc.category}</span>
                    <span>by ${this._escapeHtml(doc.created_by || 'unknown')}</span>
                    <span>Created ${this._formatTime(doc.created_at)}</span>
                    <span>Updated ${this._formatTime(doc.updated_at)}</span>
                </div>
            </div>
            <div class="kb-viewer__doc-content kb-markdown">${renderedContent}</div>
        `;
    },

    _renderMarkdown(text) {
        if (!text) return '';
        if (typeof marked !== 'undefined') {
            try {
                marked.setOptions({
                    breaks: true,
                    gfm: true,
                });
                return marked.parse(text);
            } catch (e) {
                console.error('Markdown parse error:', e);
                return this._escapeHtml(text);
            }
        }
        // Fallback if marked.js not loaded
        return this._escapeHtml(text);
    },

    addDocument(doc) {
        const exists = this.documents.find(d => d.id === doc.id);
        if (exists) {
            Object.assign(exists, doc);
        } else {
            this.documents.push(doc);
        }
        this.render();
    },

    _escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    _timeAgo(isoStr) {
        if (!isoStr) return '';
        const diff = Date.now() - new Date(isoStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        const hours = Math.floor(mins / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        return `${days}d ago`;
    },

    _formatTime(isoStr) {
        if (!isoStr) return '';
        try {
            return new Date(isoStr).toLocaleString();
        } catch {
            return isoStr;
        }
    },
};
