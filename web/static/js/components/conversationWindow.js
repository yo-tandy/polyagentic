// Multi-tab conversation window — direct agent-user chats

const ConversationWindow = {
    _el: null,
    _conversations: {},   // conv_id → { data, messages: [], thinkingEl: null }
    _activeTabId: null,
    _minimized: false,
    _maximized: false,

    init() {
        this._el = document.getElementById('conversation-window');
    },

    /** Open (or focus) a conversation tab. */
    show(data) {
        const convId = data.id;

        if (this._conversations[convId]) {
            // Already open — just switch to that tab
            this._activeTabId = convId;
            this._minimized = false;
            this._render();
            return;
        }

        this._conversations[convId] = {
            data: data,
            messages: [],
            thinkingEl: null,
        };
        this._activeTabId = convId;
        this._minimized = false;
        this._render();
    },

    /** Close a single conversation tab (or all if no id). */
    hide(convId) {
        if (convId) {
            delete this._conversations[convId];
            if (this._activeTabId === convId) {
                const remaining = Object.keys(this._conversations);
                this._activeTabId = remaining.length > 0 ? remaining[remaining.length - 1] : null;
            }
        } else {
            this._conversations = {};
            this._activeTabId = null;
        }
        this._render();
    },

    /** Add a message to the correct conversation. */
    addMessage(sender, content, type, metadata) {
        const convId = metadata?.conversation_id;
        const conv = convId ? this._conversations[convId] : this._getActiveConv();
        if (!conv) return;

        if (type === 'agent' && conv.thinkingEl) {
            conv.thinkingEl.remove();
            conv.thinkingEl = null;
        }

        const msg = { sender, content, type, metadata };
        conv.messages.push(msg);

        // If this conversation is currently visible, append the message live
        const targetId = convId || this._activeTabId;
        if (targetId === this._activeTabId && !this._minimized) {
            const messagesEl = document.getElementById('conv-window-messages');
            if (messagesEl) {
                this._renderMessageToEl(messagesEl, msg);
                messagesEl.scrollTop = messagesEl.scrollHeight;
            }
        }

        // Flash the tab if message arrived on a background tab
        if (convId && convId !== this._activeTabId) {
            const tab = this._el?.querySelector(`.conv-tab[data-conv-id="${convId}"]`);
            if (tab) tab.classList.add('conv-tab--unread');
        }
    },

    // ── Rendering ──

    _render() {
        if (!this._el) return;
        const convIds = Object.keys(this._conversations);

        if (convIds.length === 0) {
            this._el.innerHTML = '';
            this._el.classList.remove('conv-window--active');
            return;
        }

        this._el.classList.add('conv-window--active');
        this._el.classList.toggle('conv-window--minimized', this._minimized);
        this._el.classList.toggle('conv-window--maximized', this._maximized);

        const activeConv = this._conversations[this._activeTabId];

        // Build tabs
        const tabsHtml = convIds.map(id => {
            const conv = this._conversations[id];
            const agentName = conv.data.agent_id;
            const isActive = id === this._activeTabId;
            return `<div class="conv-tab ${isActive ? 'conv-tab--active' : ''}" data-conv-id="${id}">
                <span class="conv-tab__name">${this._escapeHtml(agentName)}</span>
                <button class="conv-tab__close" data-conv-id="${id}" title="Close">&times;</button>
            </div>`;
        }).join('');

        const minIcon = this._minimized ? '▢' : '−';
        const minTitle = this._minimized ? 'Restore' : 'Minimize';
        const maxIcon = this._maximized ? '◱' : '□';
        const maxTitle = this._maximized ? 'Restore' : 'Maximize';

        let bodyHtml = '';
        if (!this._minimized && activeConv) {
            const goalsHtml = (activeConv.data.goals && activeConv.data.goals.length)
                ? `<div class="conv-window__goals">${activeConv.data.goals.map(g =>
                    `<span class="conv-window__goal">${this._escapeHtml(g)}</span>`
                  ).join('')}</div>`
                : '';

            bodyHtml = `
                ${goalsHtml}
                <div class="conv-window__messages" id="conv-window-messages"></div>
                <div class="conv-window__input">
                    <textarea id="conv-window-input" placeholder="Message ${this._escapeHtml(activeConv.data.agent_id)}..." rows="2"></textarea>
                    <button id="conv-window-send">Send</button>
                </div>
            `;
        }

        this._el.innerHTML = `
            <div class="conv-window__header">
                <div class="conv-window__tabs">${tabsHtml}</div>
                <div class="conv-window__controls">
                    <button class="conv-window__ctrl-btn" id="conv-minimize" title="${minTitle}">${minIcon}</button>
                    ${!this._minimized ? `<button class="conv-window__ctrl-btn" id="conv-maximize" title="${maxTitle}">${maxIcon}</button>` : ''}
                </div>
            </div>
            ${bodyHtml}
        `;

        this._bindEvents();

        // Populate messages for the active tab
        if (!this._minimized && activeConv) {
            const messagesEl = document.getElementById('conv-window-messages');
            if (messagesEl) {
                activeConv.messages.forEach(msg => this._renderMessageToEl(messagesEl, msg));
                messagesEl.scrollTop = messagesEl.scrollHeight;
            }
            document.getElementById('conv-window-input')?.focus();
        }
    },

    _bindEvents() {
        // Tab clicks
        this._el.querySelectorAll('.conv-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                if (e.target.closest('.conv-tab__close')) return;
                const id = tab.dataset.convId;
                tab.classList.remove('conv-tab--unread');
                this._activeTabId = id;
                this._minimized = false;
                this._render();
            });
        });

        // Tab close buttons
        this._el.querySelectorAll('.conv-tab__close').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._closeConversation(btn.dataset.convId);
            });
        });

        // Window controls
        document.getElementById('conv-minimize')?.addEventListener('click', () => {
            this._minimized = !this._minimized;
            this._render();
        });
        document.getElementById('conv-maximize')?.addEventListener('click', () => {
            this._maximized = !this._maximized;
            this._render();
        });

        // Send button + Enter key
        document.getElementById('conv-window-send')?.addEventListener('click', () => this._send());
        document.getElementById('conv-window-input')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this._send();
            }
        });
    },

    _renderMessageToEl(container, msg) {
        const div = document.createElement('div');
        div.className = `chat-message chat-message--${msg.type}`;

        const senderEl = document.createElement('div');
        senderEl.className = 'chat-message__sender';
        senderEl.textContent = msg.sender;
        div.appendChild(senderEl);

        const bodyEl = document.createElement('div');
        bodyEl.className = 'chat-message__body';
        bodyEl.innerHTML = this._renderMarkdown(msg.content);
        div.appendChild(bodyEl);

        // Suggested answers
        const suggestions = msg.metadata?.suggested_answers;
        if (msg.type === 'agent' && Array.isArray(suggestions) && suggestions.length > 0) {
            const btnsDiv = document.createElement('div');
            btnsDiv.className = 'chat-suggestions';
            suggestions.slice(0, 3).forEach(answer => {
                const btn = document.createElement('button');
                btn.className = 'chat-suggestion-btn';
                btn.textContent = answer;
                btn.addEventListener('click', () => {
                    btnsDiv.querySelectorAll('.chat-suggestion-btn').forEach(b => {
                        b.disabled = true;
                        b.classList.add('chat-suggestion-btn--used');
                    });
                    btn.classList.add('chat-suggestion-btn--selected');
                    const inputEl = document.getElementById('conv-window-input');
                    if (inputEl) inputEl.value = answer;
                    this._send();
                });
                btnsDiv.appendChild(btn);
            });
            div.appendChild(btnsDiv);
        }

        container.appendChild(div);
    },

    // ── Sending ──

    async _send() {
        const inputEl = document.getElementById('conv-window-input');
        const text = inputEl?.value?.trim();
        if (!text || !this._activeTabId) return;

        const conv = this._conversations[this._activeTabId];
        if (!conv) return;

        this.addMessage('You', text, 'user', { conversation_id: this._activeTabId });
        inputEl.value = '';
        this._showThinking();

        try {
            const res = await fetch('/api/chat/conversation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, conversation_id: this._activeTabId }),
            });
            if (!res.ok) {
                this._hideThinkingForActive();
                this.addMessage('System', `Error: ${res.status}`, 'agent', { conversation_id: this._activeTabId });
            }
        } catch (err) {
            this._hideThinkingForActive();
            this.addMessage('System', 'Failed to send message.', 'agent', { conversation_id: this._activeTabId });
        }
    },

    // ── Close ──

    async _closeConversation(convId) {
        // Fire-and-forget backend close
        fetch(`/api/conversations/${convId}/close`, { method: 'POST' }).catch(() => {});
        // Immediately remove from UI
        this.hide(convId);
    },

    // ── Thinking indicator ──

    _showThinking() {
        const messagesEl = document.getElementById('conv-window-messages');
        const conv = this._conversations[this._activeTabId];
        if (!messagesEl || !conv || conv.thinkingEl) return;

        const div = document.createElement('div');
        div.className = 'chat-message chat-message--agent chat-thinking';
        const agent = conv.data.agent_id || 'Agent';
        div.innerHTML = `
            <div class="chat-message__sender">${this._escapeHtml(agent)}</div>
            <div class="thinking-dots">Thinking<span>.</span><span>.</span><span>.</span></div>
        `;
        conv.thinkingEl = div;
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    },

    _hideThinkingForActive() {
        const conv = this._conversations[this._activeTabId];
        if (conv && conv.thinkingEl) {
            conv.thinkingEl.remove();
            conv.thinkingEl = null;
        }
    },

    // ── Helpers ──

    _getActiveConv() {
        return this._activeTabId ? this._conversations[this._activeTabId] : null;
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
    },
};
