const KnowledgePanel = {
    container: null,
    documents: [],
    categories: ['specs', 'design', 'architecture', 'planning', 'history', 'repo', 'uploaded'],
    _selectedDocId: null,
    _comments: [],
    _commentingMode: false,
    _pendingComments: [],
    _blockClickHandler: null,

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
                        <div class="kb-doc__title">
                            ${d.file_type ? `<span class="kb-doc__type">${d.file_type.toUpperCase()}</span> ` : ''}${this._escapeHtml(d.title)}
                        </div>
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
        // Exit commenting mode if active (dispatches pending comments)
        if (this._commentingMode) {
            this._toggleCommentingMode();
        }
        this._selectedDocId = null;
        this._comments = [];
        this._pendingComments = [];
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
                    // Exit commenting mode before switching
                    if (this._commentingMode) {
                        this._toggleCommentingMode();
                    }
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
        this._comments = res.comments || [];
        this._pendingComments = [];
        this._commentingMode = false;

        // Render markdown content
        const renderedContent = this._renderMarkdown(content);

        // Count open comments for badge
        const openCount = this._comments.filter(c => c.status === 'open').length;
        const commentCountBadge = openCount > 0
            ? ` <span style="font-size:10px;background:var(--orange);color:white;padding:1px 6px;border-radius:8px;margin-left:4px;">${openCount}</span>`
            : '';

        canvas.innerHTML = `
            <div class="kb-viewer__doc-header">
                <div class="kb-viewer__doc-header-row">
                    <h1 class="kb-viewer__doc-title">${this._escapeHtml(doc.title)}</h1>
                    <button class="kb-comment-toggle" id="kb-comment-toggle" title="Toggle commenting mode">
                        Comment${commentCountBadge}
                    </button>
                    <button class="btn btn--sm btn--danger" id="kb-delete-doc" title="Delete document">Delete</button>
                </div>
                <div class="kb-viewer__doc-meta">
                    <span class="kb-viewer__doc-category">${doc.category}</span>
                    <span>by ${this._escapeHtml(doc.created_by || 'unknown')}</span>
                    <span>Created ${this._formatTime(doc.created_at)}</span>
                    <span>Updated ${this._formatTime(doc.updated_at)}</span>
                    ${doc.upload_path ? `<a href="/api/uploads/${doc.id}/download" class="kb-viewer__download" download>Download original</a>` : ''}
                </div>
            </div>
            <div class="kb-viewer__doc-content kb-markdown">${renderedContent}</div>
        `;

        // Bind comment toggle
        document.getElementById('kb-comment-toggle')?.addEventListener('click', () => {
            this._toggleCommentingMode();
        });

        // Bind delete button
        document.getElementById('kb-delete-doc')?.addEventListener('click', async () => {
            if (!confirm('Delete this document? This cannot be undone.')) return;
            const res = await fetch(`/api/knowledge/${docId}`, { method: 'DELETE' });
            if (res.ok) {
                this._closeViewer();
                await this.load();
            }
        });

        // Annotate existing comments
        this._annotateComments();
    },

    // ── Commenting System ──

    _toggleCommentingMode() {
        this._commentingMode = !this._commentingMode;
        const btn = document.getElementById('kb-comment-toggle');
        const contentEl = document.querySelector('.kb-viewer__doc-content.kb-markdown');

        if (this._commentingMode) {
            if (btn) {
                btn.classList.add('kb-comment-toggle--active');
                btn.textContent = 'Done Commenting';
            }
            contentEl?.classList.add('kb-commenting-mode');
            this._enableBlockSelection();
        } else {
            if (btn) {
                btn.classList.remove('kb-comment-toggle--active');
                const openCount = this._comments.filter(c => c.status === 'open').length;
                btn.innerHTML = openCount > 0
                    ? `Comment <span style="font-size:10px;background:var(--orange);color:white;padding:1px 6px;border-radius:8px;margin-left:4px;">${openCount}</span>`
                    : 'Comment';
            }
            contentEl?.classList.remove('kb-commenting-mode');
            this._disableBlockSelection();
            // Dispatch pending comments to agents
            if (this._pendingComments.length > 0) {
                this._dispatchComments();
            }
        }
    },

    _enableBlockSelection() {
        const contentEl = document.querySelector('.kb-viewer__doc-content.kb-markdown');
        if (!contentEl) return;

        this._blockClickHandler = (e) => {
            // Find the direct child block element
            const block = e.target.closest('.kb-markdown > *');
            if (!block) return;
            // Don't trigger on comment button clicks
            if (e.target.closest('.kb-comment-btn')) return;
            e.preventDefault();
            e.stopPropagation();

            const blocks = Array.from(contentEl.children);
            const elementIndex = blocks.indexOf(block);
            const highlightedText = block.textContent.trim();

            // Highlight the selected block
            contentEl.querySelectorAll('.kb-block-selected').forEach(el =>
                el.classList.remove('kb-block-selected')
            );
            block.classList.add('kb-block-selected');

            // Show comment form
            this._showCommentForm(block, elementIndex, highlightedText);
        };

        contentEl.addEventListener('click', this._blockClickHandler);
    },

    _disableBlockSelection() {
        const contentEl = document.querySelector('.kb-viewer__doc-content.kb-markdown');
        if (!contentEl || !this._blockClickHandler) return;
        contentEl.removeEventListener('click', this._blockClickHandler);
        this._blockClickHandler = null;
        contentEl.querySelectorAll('.kb-block-selected').forEach(el =>
            el.classList.remove('kb-block-selected')
        );
        document.querySelector('.kb-comment-form')?.remove();
    },

    async _showCommentForm(targetBlock, elementIndex, highlightedText) {
        // Remove existing form
        document.querySelector('.kb-comment-form')?.remove();

        // Fetch agents for dropdown
        const agentRes = await safeFetch('/api/agents', { agents: [] });
        const agents = agentRes.agents || [];

        // Determine default agent: doc author if not user, else manny
        const doc = this.documents.find(d => d.id === this._selectedDocId);
        const docAuthor = doc?.created_by || 'unknown';
        const defaultAgent = (docAuthor !== 'user' && docAuthor !== 'unknown')
            ? docAuthor
            : 'manny';

        const agentOptions = agents.map(a =>
            `<option value="${a.id}" ${a.id === defaultAgent ? 'selected' : ''}>${this._escapeHtml(a.name || a.id)} (${a.id})</option>`
        ).join('');

        const form = document.createElement('div');
        form.className = 'kb-comment-form';

        // Position below the target block within the scrollable canvas
        const canvas = document.querySelector('.kb-viewer__canvas');
        const canvasRect = canvas.getBoundingClientRect();
        const blockRect = targetBlock.getBoundingClientRect();
        form.style.top = `${blockRect.bottom - canvasRect.top + canvas.scrollTop + 8}px`;

        const previewText = highlightedText.length > 100
            ? highlightedText.substring(0, 100) + '...'
            : highlightedText;

        form.innerHTML = `
            <div class="kb-comment-form__header">Add Comment</div>
            <div class="kb-comment-form__preview">${this._escapeHtml(previewText)}</div>
            <textarea class="kb-comment-form__input" placeholder="Enter your comment..." rows="3"></textarea>
            <div class="kb-comment-form__row">
                <label class="kb-comment-form__label">Assign to:</label>
                <select class="kb-comment-form__select">${agentOptions}</select>
            </div>
            <div class="kb-comment-form__actions">
                <button class="kb-comment-form__btn kb-comment-form__btn--cancel">Cancel</button>
                <button class="kb-comment-form__btn kb-comment-form__btn--save">Save</button>
            </div>
        `;

        // Bind save
        form.querySelector('.kb-comment-form__btn--save').addEventListener('click', async () => {
            const commentText = form.querySelector('.kb-comment-form__input').value.trim();
            const assignedTo = form.querySelector('.kb-comment-form__select').value;
            if (!commentText) return;

            const res = await fetch(`/api/knowledge/${this._selectedDocId}/comments`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    highlighted_text: highlightedText,
                    element_index: elementIndex,
                    comment_text: commentText,
                    assigned_to: assignedTo,
                }),
            });
            const data = await res.json();
            if (data.comment) {
                this._comments.push(data.comment);
                this._pendingComments.push(data.comment);
                this._annotateComments();
            }
            form.remove();
            targetBlock.classList.remove('kb-block-selected');
        });

        // Bind cancel
        form.querySelector('.kb-comment-form__btn--cancel').addEventListener('click', () => {
            form.remove();
            targetBlock.classList.remove('kb-block-selected');
        });

        canvas.appendChild(form);
        form.querySelector('.kb-comment-form__input').focus();
    },

    _annotateComments() {
        const contentEl = document.querySelector('.kb-viewer__doc-content.kb-markdown');
        if (!contentEl) return;
        const blocks = contentEl.children;

        // Clear previous annotations
        contentEl.querySelectorAll('.kb-comment-btn').forEach(b => b.remove());
        for (const block of blocks) {
            block.classList.remove('kb-commented-block', 'kb-commented-block--resolved',
                'kb-commented-block--closed', 'kb-commented-block--unverified');
        }

        // Group comments by their target block
        const blockComments = new Map(); // blockEl -> [comments]

        for (const comment of this._comments) {
            let targetEl = null;

            // Primary: match by element_index
            if (comment.element_index != null && blocks[comment.element_index]) {
                targetEl = blocks[comment.element_index];
            }

            // Fallback: match by highlighted_text substring
            if (!targetEl && comment.highlighted_text) {
                const snippet = comment.highlighted_text.substring(0, 50);
                for (const block of blocks) {
                    if (block.textContent.includes(snippet)) {
                        targetEl = block;
                        break;
                    }
                }
            }

            if (targetEl) {
                if (!blockComments.has(targetEl)) blockComments.set(targetEl, []);
                blockComments.get(targetEl).push(comment);
            }
        }

        // Annotate each block that has comments
        for (const [blockEl, comments] of blockComments) {
            const openCount = comments.filter(c => c.status === 'open').length;
            const resolvedCount = comments.filter(c => c.status === 'resolved').length;
            const unverifiedCount = comments.filter(c => c.status === 'resolved' && !c.edit_verified).length;
            const allResolved = openCount === 0 && resolvedCount > 0;
            const hasUnverified = unverifiedCount > 0;
            const allClosed = comments.every(c => c.status === 'closed');

            // Highlight the block
            blockEl.style.position = 'relative';
            if (allClosed) {
                blockEl.classList.add('kb-commented-block', 'kb-commented-block--closed');
            } else if (allResolved && !hasUnverified) {
                blockEl.classList.add('kb-commented-block', 'kb-commented-block--resolved');
            } else if (allResolved && hasUnverified) {
                blockEl.classList.add('kb-commented-block', 'kb-commented-block--unverified');
            } else {
                blockEl.classList.add('kb-commented-block');
            }

            // Create comment button
            const btn = document.createElement('button');
            btn.className = 'kb-comment-btn';

            const countClass = allResolved ? 'kb-comment-btn__count--resolved'
                : (resolvedCount > 0 && openCount > 0) ? 'kb-comment-btn__count--mixed'
                : '';

            btn.innerHTML = `<span class="kb-comment-btn__icon">\uD83D\uDCAC</span>`
                + `<span class="kb-comment-btn__count ${countClass}">${comments.length}</span>`;
            btn.title = `${comments.length} comment${comments.length > 1 ? 's' : ''} on this section`;

            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._showSectionComments(comments);
            });

            blockEl.appendChild(btn);
        }
    },

    _showSectionComments(comments) {
        // Remove any existing detail popup
        document.querySelector('.kb-comment-detail')?.remove();

        const popup = document.createElement('div');
        popup.className = 'kb-comment-detail';

        const itemsHtml = comments.map(c => {
            const isUnverified = c.status === 'resolved' && !c.edit_verified;
            const statusLabel = isUnverified ? 'unverified' : c.status;
            const statusClass = `kb-comment-detail__status--${c.status}`
                + (isUnverified ? ' kb-comment-detail__status--unverified' : '');
            const resolutionClass = 'kb-comment-detail__resolution'
                + (isUnverified ? ' kb-comment-detail__resolution--unverified' : '');

            return `
            <div class="kb-comment-detail__item" data-comment-id="${c.id}">
                <div class="kb-comment-detail__item-header">
                    <span class="kb-comment-detail__status ${statusClass}">
                        ${statusLabel}
                    </span>
                    <span class="kb-comment-detail__assignee">${this._escapeHtml(c.assigned_to)}</span>
                </div>
                <div class="kb-comment-detail__text">${this._escapeHtml(c.comment_text)}</div>
                ${c.resolution ? `<div class="${resolutionClass}">
                    <span class="kb-comment-detail__verified-badge">
                        ${c.edit_verified ? '\u2705 Edit verified' : '\u26A0\uFE0F No document edit detected'}
                    </span>
                    <strong>Resolution:</strong> ${this._escapeHtml(c.resolution)}
                </div>` : ''}
                <div class="kb-comment-detail__item-actions">
                    ${c.status === 'open' ? `<button class="kb-comment-detail__btn" data-action="close" data-id="${c.id}">Close</button>` : ''}
                    <button class="kb-comment-detail__btn kb-comment-detail__btn--delete" data-action="delete" data-id="${c.id}">Delete</button>
                </div>
            </div>`;
        }).join('');

        popup.innerHTML = `
            <div class="kb-comment-detail__title">Comments on this section (${comments.length})</div>
            ${itemsHtml}
            <div class="kb-comment-detail__close">
                <button class="kb-comment-detail__close-btn" data-action="dismiss">Close</button>
            </div>
        `;

        // Bind actions via delegation
        popup.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action]');
            if (!btn) return;
            const action = btn.dataset.action;
            const commentId = btn.dataset.id;
            if (action === 'close' && commentId) {
                this._updateCommentStatus(commentId, 'closed');
                popup.remove();
            } else if (action === 'delete' && commentId) {
                this._deleteComment(commentId);
                popup.remove();
            } else if (action === 'dismiss') {
                popup.remove();
            }
        });

        document.querySelector('.kb-viewer__canvas').appendChild(popup);
    },

    async _updateCommentStatus(commentId, status) {
        const docId = this._selectedDocId;
        await fetch(`/api/knowledge/${docId}/comments/${commentId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status }),
        });
        await this._loadAndRenderDoc(docId);
    },

    async _deleteComment(commentId) {
        const docId = this._selectedDocId;
        await fetch(`/api/knowledge/${docId}/comments/${commentId}`, { method: 'DELETE' });
        await this._loadAndRenderDoc(docId);
    },

    async _dispatchComments() {
        const docId = this._selectedDocId;
        if (!docId) return;
        try {
            await fetch(`/api/knowledge/${docId}/comments/dispatch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            this._pendingComments = [];
        } catch (err) {
            console.warn('Comment dispatch failed:', err);
        }
    },

    // ── Rendering Helpers ──

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
