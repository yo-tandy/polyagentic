// Login page logic
(function () {
    const params = new URLSearchParams(window.location.search);
    const errorEl = document.getElementById('error-message');
    const stepLogin = document.getElementById('step-login');
    const stepSignup = document.getElementById('step-signup');
    const signupToken = params.get('token');

    // Show error if present
    const error = params.get('error');
    if (error) {
        errorEl.textContent = decodeErrorMessage(error);
        errorEl.style.display = 'block';
    }

    // If signup flow, show org selection step
    if (params.get('signup') === 'true' && signupToken) {
        stepLogin.style.display = 'none';
        stepSignup.style.display = 'block';
    }

    // Create org button
    document.getElementById('create-org-btn')?.addEventListener('click', async () => {
        const orgName = document.getElementById('org-name-input').value.trim();
        if (!orgName) {
            showError('Please enter an organization name.');
            return;
        }
        await completeSignup('create_org', { org_name: orgName });
    });

    // Join org button
    document.getElementById('join-org-btn')?.addEventListener('click', async () => {
        const inviteCode = document.getElementById('invite-code-input').value.trim();
        if (!inviteCode) {
            showError('Please enter an invite code.');
            return;
        }
        await completeSignup('join_org', { invite_code: inviteCode });
    });

    async function completeSignup(action, extra) {
        try {
            const res = await fetch('/auth/complete-signup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    signup_token: signupToken,
                    action,
                    ...extra,
                }),
            });
            const data = await res.json();
            if (!res.ok) {
                showError(data.error || 'Signup failed.');
                return;
            }
            // Set cookie from token and redirect
            if (data.token) {
                document.cookie = `session_token=${data.token}; path=/; SameSite=Lax`;
            }
            window.location.href = data.redirect || '/';
        } catch (err) {
            showError('Network error. Please try again.');
        }
    }

    function showError(msg) {
        errorEl.textContent = msg;
        errorEl.style.display = 'block';
    }

    function decodeErrorMessage(code) {
        const messages = {
            'no_code': 'Authentication was cancelled.',
            'token_exchange_failed': 'Failed to authenticate with Google. Please try again.',
            'userinfo_failed': 'Could not retrieve your Google profile.',
            'access_denied': 'Access was denied.',
        };
        return messages[code] || `Authentication error: ${code}`;
    }
})();
