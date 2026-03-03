const ConversationBar = {
    _el: null,
    _conversation: null,

    init() {
        this._el = document.getElementById('conversation-bar');
    },

    show(data) {
        this._conversation = data;
        if (!this._el) return;

        const goalsHtml = (data.goals || []).map(g =>
            `<li class="conv-bar__goal">${this._escapeHtml(g)}</li>`
        ).join('');

        this._el.innerHTML = `
            <div class="conv-bar__info">
                <span class="conv-bar__agent">${this._escapeHtml(data.agent_id)}</span>
                <span class="conv-bar__title">${this._escapeHtml(data.title)}</span>
            </div>
            <ul class="conv-bar__goals">${goalsHtml}</ul>
            <button class="conv-bar__end" id="conv-end-btn">End Conversation</button>
        `;
        this._el.classList.add('conv-bar--active');

        document.getElementById('conv-end-btn')?.addEventListener('click', () => {
            this.endConversation();
        });

        // Update chat placeholder
        const input = document.getElementById('chat-input');
        if (input) {
            input.placeholder = `Talking to ${data.agent_id}...`;
        }
    },

    hide() {
        this._conversation = null;
        if (!this._el) return;
        this._el.innerHTML = '';
        this._el.classList.remove('conv-bar--active');

        // Restore chat placeholder
        const input = document.getElementById('chat-input');
        if (input) {
            input.placeholder = 'Talk to Manny...';
        }
    },

    async endConversation() {
        if (!this._conversation) return;
        try {
            await fetch(`/api/conversations/${this._conversation.id}/close`, {
                method: 'POST',
            });
        } catch (err) {
            console.error('Failed to end conversation:', err);
        }
    },

    _escapeHtml(text) {
        const d = document.createElement('div');
        d.textContent = text;
        return d.innerHTML;
    }
};
