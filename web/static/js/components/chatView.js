const ChatView = {
    container: null,
    input: null,
    sendBtn: null,
    _thinkingEl: null,

    init(containerId, inputId, sendBtnId) {
        this.container = document.getElementById(containerId);
        this.input = document.getElementById(inputId);
        this.sendBtn = document.getElementById(sendBtnId);

        this.sendBtn?.addEventListener('click', () => this.send());
        this.input?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.send();
            }
        });

        // File upload
        this.fileInput = document.getElementById('chat-file-input');
        const attachBtn = document.getElementById('chat-attach');
        if (attachBtn) attachBtn.addEventListener('click', () => this.fileInput?.click());
        if (this.fileInput) this.fileInput.addEventListener('change', () => this._handleFileUpload());
    },

    async send() {
        const text = this.input?.value?.trim();
        if (!text) return;

        this.addMessage('You', text, 'user');
        this.input.value = '';
        this._showThinking();

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text }),
            });
            if (!res.ok) {
                this._hideThinking();
                this.addMessage('System', `Error: ${res.status} ${res.statusText}`, 'agent');
            }
        } catch (err) {
            this._hideThinking();
            this.addMessage('System', 'Failed to send message. Is the server running?', 'agent');
        }
    },

    addMessage(sender, content, type, metadata) {
        if (!this.container) return;
        // Remove thinking indicator when a response arrives
        if (type === 'agent') {
            this._hideThinking();
        }
        const div = document.createElement('div');
        div.className = `chat-message chat-message--${type}`;

        const senderEl = document.createElement('div');
        senderEl.className = 'chat-message__sender';
        senderEl.textContent = sender;
        div.appendChild(senderEl);

        const bodyEl = document.createElement('div');
        bodyEl.className = 'chat-message__body';
        bodyEl.innerHTML = this._renderMarkdown(content);
        div.appendChild(bodyEl);

        // Render suggested answer buttons if present
        const suggestions = metadata?.suggested_answers;
        if (type === 'agent' && Array.isArray(suggestions) && suggestions.length > 0) {
            const btnsDiv = document.createElement('div');
            btnsDiv.className = 'chat-suggestions';
            suggestions.slice(0, 3).forEach(answer => {
                const btn = document.createElement('button');
                btn.className = 'chat-suggestion-btn';
                btn.textContent = answer;
                btn.addEventListener('click', () => {
                    // Disable all suggestion buttons in this group
                    btnsDiv.querySelectorAll('.chat-suggestion-btn').forEach(b => {
                        b.disabled = true;
                        b.classList.add('chat-suggestion-btn--used');
                    });
                    btn.classList.add('chat-suggestion-btn--selected');
                    // Send the selected answer
                    this.input.value = answer;
                    this.send();
                });
                btnsDiv.appendChild(btn);
            });
            div.appendChild(btnsDiv);
        }

        this.container.appendChild(div);
        this.container.scrollTop = this.container.scrollHeight;
    },

    _showThinking() {
        if (!this.container || this._thinkingEl) return;
        const div = document.createElement('div');
        div.className = 'chat-message chat-message--agent chat-thinking';
        div.innerHTML = `
            <div class="chat-message__sender">MANNY</div>
            <div class="thinking-dots">Thinking<span>.</span><span>.</span><span>.</span></div>
        `;
        this._thinkingEl = div;
        this.container.appendChild(div);
        this.container.scrollTop = this.container.scrollHeight;
    },

    _hideThinking() {
        if (this._thinkingEl) {
            this._thinkingEl.remove();
            this._thinkingEl = null;
        }
    },

    async _handleFileUpload() {
        const file = this.fileInput?.files?.[0];
        if (!file) return;

        this.addMessage('You', `Uploading: ${file.name}...`, 'user');
        this._showThinking();

        const formData = new FormData();
        formData.append('file', file);
        formData.append('context', 'chat');

        try {
            const res = await fetch('/api/upload', { method: 'POST', body: formData });
            if (!res.ok) {
                this._hideThinking();
                const err = await res.json();
                this.addMessage('System', `Upload failed: ${err.error}`, 'agent');
            }
        } catch (err) {
            this._hideThinking();
            this.addMessage('System', `Upload failed: ${err.message}`, 'agent');
        }

        this.fileInput.value = '';
    },

    _escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    },

    _renderMarkdown(text) {
        if (typeof marked !== 'undefined') {
            try {
                return marked.parse(text || '');
            } catch (e) {
                return this._escapeHtml(text || '');
            }
        }
        return this._escapeHtml(text || '');
    }
};
