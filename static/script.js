/* ================================================================
   RIO — script.js v9.0
   Features: Progress bar, typing indicator, action tags, grid view,
             stats counter, export chat, data modal, smart status
   ================================================================ */

// ── Core Elements
const chatMessages    = document.getElementById('chat-messages');
const chatInput       = document.getElementById('chat-input');
const sendBtn         = document.getElementById('send-btn');
const liveViewContent = document.getElementById('live-view-content');
const agentStatus     = document.getElementById('agent-status');
const headlessToggle  = document.getElementById('headless-toggle');
const videoToggle     = document.getElementById('video-toggle');
const speedSlider     = document.getElementById('speed-slider');
const speedValue      = document.getElementById('speed-value');
const clearViewBtn    = document.getElementById('clear-view-btn');
const micBtn          = document.getElementById('mic-btn');
const topProgress     = document.getElementById('top-progress');

let ws                = null;
let isWaitingForInput = false;
let stepCounter       = 1;
let shotCounter       = 0;
let isGridView        = false;
let typingIndicator   = null;
let extractedDataCache= [];

// ── Auto-reconnect state
let reconnectAttempts = 0;
let reconnectDelay    = 1500;   // ms, doubles each time
const MAX_RECONNECT   = 5;

// ── Stat counters
function updateStats() {
    const stepsEl = document.getElementById('stat-steps');
    const shotsEl = document.getElementById('stat-shots');
    if (stepsEl) stepsEl.textContent = stepCounter - 1;
    if (shotsEl) shotsEl.textContent = shotCounter;
}

// ── Top progress bar
let progressVal = 0;
function setProgress(pct) {
    progressVal = pct;
    if (topProgress) {
        topProgress.style.width = pct + '%';
        topProgress.style.opacity = pct > 0 && pct < 100 ? '1' : '0';
    }
}

// ── Agent Status Bar
function updateAgentStatus(text, icon = '') {
    if (!agentStatus) return;
    if (!text) {
        agentStatus.style.display = 'none';
        agentStatus.innerText = '';
    } else {
        agentStatus.style.display = 'flex';
        agentStatus.innerText = icon ? `${icon} ${text}` : text;
    }
}

// ── Step Badge
function updateStepBadge(n) {
    const badge = document.getElementById('step-badge');
    if (!badge) return;
    if (!n) { badge.style.display = 'none'; return; }
    badge.style.display = 'inline-block';
    badge.innerText = `Step ${n}`;
}

// ── View subtitle
function setViewSubtitle(text) {
    const el = document.getElementById('view-subtitle');
    if (el) el.textContent = text;
}

// ── Theme Toggle
let isDark = true;
window.toggleTheme = function() {
    isDark = !isDark;
    const btn = document.getElementById('theme-toggle');
    if (isDark) {
        document.documentElement.removeAttribute('data-theme');
        if (btn) btn.innerHTML = `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>`;
    } else {
        document.documentElement.setAttribute('data-theme', 'light');
        if (btn) btn.innerHTML = `<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>`;
    }
};

// ── Toggle Grid/List View
window.toggleViewMode = function() {
    isGridView = !isGridView;
    const content = liveViewContent;
    if (!content) return;
    if (isGridView) {
        content.style.display = 'grid';
        content.style.gridTemplateColumns = 'repeat(auto-fill, minmax(280px, 1fr))';
        content.style.alignContent = 'start';
    } else {
        content.style.display = 'flex';
        content.style.gridTemplateColumns = '';
        content.style.flexDirection = 'column';
    }
    const btn = document.getElementById('view-mode-btn');
    if (btn) {
        btn.innerHTML = isGridView
            ? `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`
            : `<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>`;
    }
};

// ── Emergency Stop
window.stopAgent = function() {
    if (ws) {
        try { ws.send(JSON.stringify({ type: 'stop' })); } catch(e) {}
        // Don't close ws — just signal stop. Server will close the task loop.
    }
    updateAgentStatus('');
    updateStepBadge(null);
    removeTypingIndicator();
    setProgress(0);
    isWaitingForInput = false;
    if (chatInput) {
        chatInput.disabled = false;
        chatInput.placeholder = 'Give Rio a task...';
        chatInput.classList.remove('requires-input');
    }
    if (sendBtn) sendBtn.disabled = false;
    const stopBtn = document.getElementById('stop-btn');
    if (stopBtn) stopBtn.style.display = 'none';
    appendMessage('bot', '⛔ **Rio stopped by user.** Ready for a new task!');
};

// ── Clear Chat
window.clearChat = function() {
    if (chatMessages) chatMessages.innerHTML = '';
    stepCounter = 1; shotCounter = 0;
    updateStepBadge(null);
    updateStats();
};

// ── Export Chat as text
window.exportChat = function() {
    if (!chatMessages) return;
    const lines = [];
    chatMessages.querySelectorAll('.message').forEach(m => {
        const isUser = m.classList.contains('user');
        const text = m.querySelector('.message-content')?.innerText || '';
        lines.push(`[${isUser ? 'YOU' : 'RIO'}]\n${text}\n`);
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `rio-session-${new Date().toISOString().slice(0,10)}.txt`;
    a.click();
    showToast('Chat exported!');
};

// ── Toast notification
function showToast(msg, type = 'success') {
    const existing = document.querySelectorAll('.rio-toast');
    existing.forEach((t, i) => { t.style.bottom = (24 + (i + 1) * 60) + 'px'; });

    const toast = document.createElement('div');
    toast.className = 'rio-toast';
    const colorMap = { success: 'var(--success)', error: 'var(--danger)', info: 'var(--accent)', warning: 'var(--warning)' };
    const color = colorMap[type] || 'var(--accent)';
    const icon  = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' }[type] || 'ℹ';
    toast.style.cssText = `
        position:fixed; bottom:24px; right:24px; z-index:99999;
        background: var(--surface-1); border: 1px solid ${color};
        color: var(--text-primary); padding: 11px 16px;
        border-radius: var(--r-md); font-size: 0.8rem; font-weight: 500;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5);
        animation: toast-in 0.3s cubic-bezier(0.16,1,0.3,1); max-width: 320px;
        display: flex; align-items: center; gap: 8px;
    `;
    toast.innerHTML = `<span style="color:${color}; font-size:0.9rem; font-weight:700;">${icon}</span> ${msg}`;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.animation = 'toast-out 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 2800);
}

// ── Typing indicator
function showTypingIndicator() {
    if (typingIndicator) return;
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message bot';
    msgDiv.id = 'typing-msg';
    const av = document.createElement('div');
    av.className = 'msg-avatar';
    av.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`;
    const indicator = document.createElement('div');
    indicator.className = 'message-content typing-indicator';
    indicator.innerHTML = '<span></span><span></span><span></span>';
    msgDiv.appendChild(av);
    msgDiv.appendChild(indicator);
    if (chatMessages) {
        chatMessages.appendChild(msgDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    typingIndicator = msgDiv;
}

function removeTypingIndicator() {
    if (typingIndicator) {
        typingIndicator.remove();
        typingIndicator = null;
    }
    const old = document.getElementById('typing-msg');
    if (old) old.remove();
}

// ── Fullscreen Screenshot
window.fullscreenLatest = function() {
    const imgs = liveViewContent ? liveViewContent.querySelectorAll('img') : [];
    if (!imgs.length) { showToast('No screenshots yet', 'info'); return; }
    const last = imgs[imgs.length - 1];
    openImageOverlay(last.src);
};

function openImageOverlay(src) {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(5,5,12,0.95);backdrop-filter:blur(20px);z-index:99999;display:flex;align-items:center;justify-content:center;cursor:zoom-out;animation:msg-in 0.2s ease;';
    overlay.innerHTML = `
        <img src="${src}" style="max-width:95vw;max-height:92vh;border-radius:16px;box-shadow:0 24px 80px rgba(0,0,0,0.8);border:1px solid rgba(123,94,248,0.2);">
        <button onclick="this.parentElement.remove()" style="position:absolute;top:20px;right:24px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);color:#fff;width:36px;height:36px;border-radius:50%;font-size:1.2rem;cursor:pointer;display:flex;align-items:center;justify-content:center;">&times;</button>
        <a href="${src}" download style="position:absolute;bottom:24px;right:24px;background:rgba(123,94,248,0.2);border:1px solid rgba(123,94,248,0.4);color:#fff;padding:8px 16px;border-radius:10px;font-size:0.8rem;text-decoration:none;">⬇ Download</a>
    `;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
}

// ── Clear View Button
if (clearViewBtn && liveViewContent) {
    clearViewBtn.addEventListener('click', () => {
        liveViewContent.innerHTML = `
        <div class="live-view-placeholder">
            <div class="placeholder-icon">
                <svg width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            </div>
            <h3>Rio is ready</h3>
            <p>Start a task and watch Rio browse in real-time.</p>
        </div>`;
        shotCounter = 0;
        updateStats();
        setViewSubtitle('Waiting for task...');
    });
}

// ── Speed Slider
if (speedSlider && speedValue) {
    speedSlider.addEventListener('input', (e) => {
        speedValue.innerText = `${e.target.value}s delay`;
    });
}

// ── Voice Input
const _micBtn = document.getElementById('mic-btn');
if (_micBtn && ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SR();
    recognition.lang = 'ar-EG';
    recognition.interimResults = false;

    _micBtn.addEventListener('click', () => {
        _micBtn.classList.add('recording');
        showToast('Listening...', 'info');
        recognition.start();
    });
    recognition.onresult = (e) => {
        if (chatInput) chatInput.value = e.results[0][0].transcript;
        _micBtn.classList.remove('recording');
        showToast('Voice captured!');
    };
    recognition.onerror = () => { _micBtn.classList.remove('recording'); showToast('Voice error', 'error'); };
    recognition.onend   = () => _micBtn.classList.remove('recording');
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// MODALS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function openModal(id)  { const m = document.getElementById(id); if (m) m.classList.add('active'); }
function closeModal(id) { const m = document.getElementById(id); if (m) m.classList.remove('active'); }

document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.classList.remove('active');
    });
});

// Settings
window.openSettingsModal  = () => openModal('settings-modal');
window.closeSettingsModal = () => closeModal('settings-modal');

// Routines
window.openRoutinesModal  = () => { renderRoutines(); openModal('routines-modal'); };
window.closeRoutinesModal = () => closeModal('routines-modal');

function getRoutines()      { return JSON.parse(localStorage.getItem('rio_routines') || '[]'); }
function saveRoutines(data) { localStorage.setItem('rio_routines', JSON.stringify(data)); }

function renderRoutines() {
    const list = document.getElementById('routines-list');
    if (!list) return;
    const routines = getRoutines();
    list.innerHTML = '';
    if (routines.length === 0) {
        list.innerHTML = '<div class="empty-state">No routines yet. Add one below!</div>';
        return;
    }
    routines.forEach((r, idx) => {
        const item = document.createElement('div');
        item.className = 'routine-item';
        item.innerHTML = `
            <div style="flex:1; min-width:0;">
                <div class="routine-name">${r.title}</div>
                <div class="routine-prompt-text">${r.prompt}</div>
            </div>
            <div style="display:flex; gap:6px; flex-shrink:0;">
                <button class="btn btn-secondary run-routine-btn" data-idx="${idx}" style="padding:5px 10px; font-size:0.73rem;">▶ Run</button>
                <button class="btn del-routine-btn" data-idx="${idx}" style="padding:5px 10px; font-size:0.73rem; color:var(--danger); border-color:rgba(244,63,94,0.3); background:rgba(244,63,94,0.08);">✕</button>
            </div>
        `;
        list.appendChild(item);
    });

    list.querySelectorAll('.run-routine-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const r = getRoutines()[btn.dataset.idx];
            if (r && chatInput) { chatInput.value = r.prompt; closeModal('routines-modal'); sendMessage(); }
        });
    });
    list.querySelectorAll('.del-routine-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const routines = getRoutines(); routines.splice(btn.dataset.idx, 1);
            saveRoutines(routines); renderRoutines();
            showToast('Routine deleted', 'info');
        });
    });
}

const addRoutineBtn = document.getElementById('add-routine-btn');
if (addRoutineBtn) {
    addRoutineBtn.addEventListener('click', () => {
        const titleEl  = document.getElementById('routine-title');
        const promptEl = document.getElementById('routine-prompt');
        const title    = titleEl?.value.trim();
        const prompt   = promptEl?.value.trim();
        if (!title || !prompt) { showToast('Please fill both fields', 'warning'); return; }
        const routines = getRoutines();
        routines.push({ title, prompt });
        saveRoutines(routines);
        titleEl.value = ''; promptEl.value = '';
        renderRoutines();
        showToast('Routine saved!');
    });
}

// ── Vault Modal
window.openVaultModal = async function() {
    openModal('vault-modal');
    const tbody = document.getElementById('vault-tbody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="3" class="empty-state">Loading...</td></tr>`;
    try {
        const res  = await fetch('/api/vault');
        const data = await res.json();
        tbody.innerHTML = '';
        const entries = Object.entries(data);
        if (entries.length === 0) {
            tbody.innerHTML = `<tr><td colspan="3" class="empty-state">Vault is empty. Rio hasn't saved any accounts yet.</td></tr>`;
            return;
        }
        entries.forEach(([platform, creds]) => {
            const tr = document.createElement('tr');
            const favicon = `https://www.google.com/s2/favicons?sz=16&domain=${platform}`;
            tr.innerHTML = `
                <td style="padding-left:18px;">
                    <span class="vault-platform">
                        <img src="${favicon}" width="14" height="14" style="border-radius:3px;" onerror="this.style.display='none'">
                        ${platform}
                    </span>
                </td>
                <td><span class="vault-user">${creds.username || '—'}</span></td>
                <td>
                    <span class="vault-pass" onclick="copyToClip('${creds.password}', this)" title="Click to copy">
                        <span>••••••••</span>
                        <svg class="copy-icon" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                    </span>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch {
        tbody.innerHTML = `<tr><td colspan="3" class="empty-state" style="color:var(--danger);">Failed to load vault.</td></tr>`;
    }
};
window.closeVaultModal = () => closeModal('vault-modal');

window.copyToClip = function(text, el) {
    navigator.clipboard.writeText(text).then(() => {
        const orig = el.querySelector('span').textContent;
        el.querySelector('span').textContent = 'Copied!';
        el.style.color = 'var(--success)';
        setTimeout(() => { el.querySelector('span').textContent = orig; el.style.color = ''; }, 2000);
    });
};

// ── Sessions Modal
window.openSessionsModal = async function() {
    openModal('sessions-modal');
    const list = document.getElementById('sessions-list');
    if (!list) return;
    list.innerHTML = '<div class="empty-state">Loading sessions...</div>';
    try {
        const res  = await fetch('/api/sessions');
        const data = await res.json();
        list.innerHTML = '';
        if (data.length === 0) {
            list.innerHTML = '<div class="empty-state">No saved sessions. Use "Save" below the chat to create one.</div>';
            return;
        }
        data.forEach(s => {
            const div = document.createElement('div');
            div.className = 'session-item';
            div.innerHTML = `
                <div>
                    <div class="session-name">${s.name}</div>
                    <div class="session-date">${new Date(s.date).toLocaleString()}</div>
                </div>
                <button class="btn btn-secondary" onclick="loadSession('${s.id}')" style="font-size:0.73rem; padding:6px 12px;">Restore</button>
            `;
            list.appendChild(div);
        });
    } catch {
        list.innerHTML = '<div class="empty-state" style="color:var(--danger);">Failed to load sessions.</div>';
    }
};
window.closeSessionsModal = () => closeModal('sessions-modal');

window.saveSessionPrompt = async function() {
    const name = prompt('Name this session:');
    if (!name) return;
    try {
        const res = await fetch('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, html: chatMessages.innerHTML })
        });
        if (res.ok) showToast('Session saved!');
        else showToast('Failed to save session', 'error');
    } catch { showToast('Error saving session', 'error'); }
};

window.loadSession = async function(id) {
    try {
        const res  = await fetch('/api/sessions/' + id);
        const data = await res.json();
        if (data.html) {
            chatMessages.innerHTML = data.html;
            closeModal('sessions-modal');
            chatMessages.scrollTop = chatMessages.scrollHeight;
            showToast('Session restored!');
        }
    } catch { showToast('Error loading session', 'error'); }
};

// ── Data Modal
window.openDataModal = async function() {
    openModal('data-modal');
    const body = document.getElementById('data-modal-body');
    if (!body) return;
    body.innerHTML = '<div class="empty-state">Loading...</div>';
    try {
        const res  = await fetch('/api/data');
        const data = await res.json();
        extractedDataCache = data;
        if (!data || data.length === 0) {
            body.innerHTML = '<div class="empty-state">No data extracted yet. Run a task with save_data to collect data.</div>';
            return;
        }
        body.innerHTML = '';
        data.forEach((item, idx) => {
            const card = document.createElement('div');
            card.style.cssText = 'background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-md);padding:14px;font-size:0.82rem;';
            card.innerHTML = `
                <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                    <span style="font-weight:600;color:var(--text-primary);">Entry #${idx + 1}</span>
                    <span style="color:var(--text-tertiary);font-size:0.7rem;">${item.timestamp ? new Date(item.timestamp).toLocaleString() : ''}</span>
                </div>
                <pre style="background:var(--bg);border:1px solid var(--border);border-radius:var(--r-sm);padding:10px;overflow-x:auto;font-size:0.75rem;font-family:'JetBrains Mono',monospace;">${JSON.stringify(item, null, 2)}</pre>
            `;
            body.appendChild(card);
        });
    } catch {
        body.innerHTML = '<div class="empty-state" style="color:var(--danger);">Failed to load data. Make sure the /api/data endpoint is available.</div>';
    }
};
window.closeDataModal = () => closeModal('data-modal');

window.downloadData = function() {
    if (!extractedDataCache.length) { showToast('No data to download', 'warning'); return; }
    const blob = new Blob([JSON.stringify(extractedDataCache, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `rio-data-${new Date().toISOString().slice(0,10)}.json`;
    a.click();
    showToast('Data downloaded!');
};

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// WEBSOCKET & MESSAGE HANDLING
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function connect() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);

    ws.onmessage = (event) => {
        const data       = JSON.parse(event.data);
        const contentStr = data.content || '';
        const stopBtn    = document.getElementById('stop-btn');

        removeTypingIndicator();

        // Progress bar logic
        if (contentStr.includes('Planning')) setProgress(10);
        else if (contentStr.includes('Step')) {
            const m = contentStr.match(/\[Step (\d+)\]/);
            if (m) {
                const step = parseInt(m[1]);
                setProgress(Math.min(10 + step * 5, 90));
            }
        }
        else if (contentStr.includes('✅') || contentStr.includes('Task completed')) setProgress(100);
        else if (contentStr.includes('stopped by user')) setProgress(0);

        // Status bar
        if      (contentStr.includes('Starting agent') || contentStr.includes('Planning'))  { updateAgentStatus('Planning task...'); if (stopBtn) stopBtn.style.display='flex'; }
        else if (contentStr.includes('Navigating') || contentStr.includes('goto'))           updateAgentStatus('Navigating...');
        else if (contentStr.includes('Clicking'))                                             updateAgentStatus('Clicking...');
        else if (contentStr.includes('Typing') || contentStr.includes('Selecting'))          updateAgentStatus('Typing...');
        else if (contentStr.includes('Scrolling'))                                            updateAgentStatus('Scrolling...');
        else if (contentStr.includes('Thinking') || contentStr.includes('LLM'))              updateAgentStatus('Thinking...');
        else if (contentStr.includes('Capturing') || contentStr.toLowerCase().includes('screenshot')) {
            updateAgentStatus('Screenshot...');
            setViewSubtitle('Capturing browser state...');
        }
        else if (contentStr.includes('✅') || contentStr.includes('Task completed') || contentStr.includes('stopped by user')) {
            updateAgentStatus('');
            if (stopBtn) stopBtn.style.display = 'none';
            setViewSubtitle('Task completed');
            setTimeout(() => setProgress(0), 1500);
        }

        const stepMatch = contentStr.match(/\[Step (\d+)\]/);
        if (stepMatch) updateStepBadge(parseInt(stepMatch[1]));

        appendMessage('bot', data.content, data.requires_input, data.image);

        if (data.requires_input && chatInput) {
            isWaitingForInput = true;
            chatInput.placeholder = 'Rio is waiting for your reply...';
            chatInput.classList.add('requires-input');
            chatInput.focus();
        }
    };

    ws.onopen = () => {
        reconnectAttempts = 0;
        reconnectDelay    = 1500;
        // Re-enable input on reconnect
        if (sendBtn) sendBtn.disabled = false;
        if (chatInput) {
            chatInput.disabled = false;
            chatInput.placeholder = 'Give Rio a task...';
        }
    };

    ws.onclose = (evt) => {
        removeTypingIndicator();
        setProgress(0);
        const stopBtn = document.getElementById('stop-btn');
        if (stopBtn) stopBtn.style.display = 'none';
        updateAgentStatus('');
        isWaitingForInput = false;

        // Was this intentional (code 1000) or a crash?
        const intentional = evt.code === 1000 || evt.wasClean;

        if (intentional || reconnectAttempts >= MAX_RECONNECT) {
            // Clean stop: just unlock the UI, don't reconnect
            if (sendBtn) sendBtn.disabled = false;
            if (chatInput) {
                chatInput.disabled = false;
                chatInput.placeholder = 'Give Rio a task...';
                chatInput.classList.remove('requires-input');
            }
            if (!intentional) {
                appendMessage('bot', '🔌 Connection lost after several attempts. Refreshing...');
                setTimeout(() => location.reload(), 2000);
            }
            return;
        }

        // Auto-reconnect with exponential backoff
        reconnectAttempts++;
        const delay = Math.min(reconnectDelay, 10000);
        reconnectDelay = Math.floor(reconnectDelay * 1.6);
        showToast(`🔌 Reconnecting... (${reconnectAttempts}/${MAX_RECONNECT})`, 'warning');

        if (chatInput) {
            chatInput.disabled = false;
            chatInput.placeholder = 'Reconnecting... you can still type';
            chatInput.classList.remove('requires-input');
        }
        if (sendBtn) sendBtn.disabled = false;

        setTimeout(() => connect(), delay);
    };

    ws.onerror = () => {
        // onerror always fires before onclose — let onclose handle it
    };
}

// ── Detect action type for badge
function detectActionType(text) {
    if (!text) return null;
    if (text.includes('Navigating') || text.includes('goto') || text.includes('navigate'))        return ['nav', '→'];
    if (text.includes('Clicking') || text.includes('click'))                                       return ['click', '⚡'];
    if (text.includes('Typing') || text.includes('type') || text.includes('Filling'))              return ['type', '✏'];
    if (text.includes('screenshot') || text.includes('Capturing'))                                 return ['shot', '📸'];
    if (text.includes('✅') || text.includes('Task completed') || text.includes('done'))           return ['done', '✅'];
    if (text.includes('⚠') || text.includes('Error') || text.includes('REJECTION'))               return ['error', '⚠'];
    return null;
}

function appendMessage(sender, text, requiresInput = false, imageUrl = null) {
    if (!chatMessages) return;

    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${sender}`;

    if (sender === 'bot') {
        const av = document.createElement('div');
        av.className = 'msg-avatar';
        av.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`;
        msgDiv.appendChild(av);
    }

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    if (requiresInput) contentDiv.classList.add('requires-input');

    if (sender === 'bot') {
        const t = text || '';
        if (t.includes('[Step') || t.includes('Memory Updated') || t.includes('🔐') || t.includes('📧') || t.includes('📬')) {
            contentDiv.classList.add('action-highlight');
        }
    }

    let displayTxt = text;
    if (sender === 'bot') {
        // Detect action type and add color tag
        const actionInfo = detectActionType(text || '');
        if (actionInfo) {
            const [type, icon] = actionInfo;
            const tag = `<span class="action-tag ${type}">${icon} ${type.toUpperCase()}</span>\n\n`;
            const rendered = typeof marked !== 'undefined' ? marked.parse(`**[${stepCounter}]** ${text || ''}`) : text;
            contentDiv.innerHTML = tag + rendered;
        } else {
            displayTxt = `**[${stepCounter}]** ${text || ''}`;
            if (typeof marked !== 'undefined') {
                contentDiv.innerHTML = marked.parse(displayTxt);
            } else {
                contentDiv.innerText = displayTxt;
            }
        }
        stepCounter++;
        updateStats();

        // Style links
        contentDiv.querySelectorAll('a').forEach(l => {
            l.target = '_blank';
            l.style.color = 'var(--accent)';
            l.style.textDecoration = 'none';
        });

        // Code copy buttons
        contentDiv.querySelectorAll('pre').forEach(pre => {
            pre.style.position = 'relative';
            const copyBtn = document.createElement('button');
            copyBtn.textContent = 'Copy';
            copyBtn.style.cssText = `
                position:absolute; top:8px; right:8px;
                background:var(--surface-3); border:1px solid var(--border);
                color:var(--text-secondary); border-radius:var(--r-xs);
                padding:3px 9px; font-size:0.7rem; cursor:pointer; font-family:inherit;
                transition: all 0.18s;
            `;
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(pre.innerText).then(() => {
                    copyBtn.textContent = '✓ Copied';
                    copyBtn.style.color = 'var(--success)';
                    setTimeout(() => { copyBtn.textContent = 'Copy'; copyBtn.style.color = ''; }, 2000);
                });
            });
            pre.appendChild(copyBtn);
        });
    } else {
        contentDiv.innerText = displayTxt;
    }

    msgDiv.appendChild(contentDiv);
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Screenshot → Live View pane
    if (imageUrl && liveViewContent) {
        const placeholder = liveViewContent.querySelector('.live-view-placeholder');
        if (placeholder) placeholder.remove();

        const container = document.createElement('div');
        container.className = 'screenshot-container';

        const meta = document.createElement('div');
        meta.className = 'screenshot-meta';
        const time = new Date().toLocaleTimeString();

        // Try to get action name for the screenshot meta
        const actionInfo = detectActionType(text || '');
        const actionBadge = actionInfo
            ? `<span class="action-name">${actionInfo[1]} ${actionInfo[0].toUpperCase()}</span>`
            : '';

        meta.innerHTML = `
            <span class="step-label">Step ${stepCounter - 1}</span>
            ${actionBadge}
            <span>${time}</span>
        `;

        const img = document.createElement('img');
        img.src = imageUrl;
        img.style.cursor = 'zoom-in';
        img.loading = 'lazy';
        img.addEventListener('click', () => openImageOverlay(imageUrl));

        container.appendChild(meta);
        container.appendChild(img);
        liveViewContent.appendChild(container);
        liveViewContent.scrollTop = liveViewContent.scrollHeight;

        shotCounter++;
        updateStats();
        setViewSubtitle(`Step ${stepCounter - 1} · ${time}`);
    }
}

function sendMessage() {
    if (!chatInput) return;
    const text = chatInput.value.trim();
    if (!text) return;

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        showToast('Not connected. Reconnecting...', 'warning');
        connect();
        setTimeout(() => sendMessage(), 800);
        return;
    }

    appendMessage('user', text);
    showTypingIndicator();
    setProgress(5);

    ws.send(JSON.stringify({
        content:      text,
        api_mode:     document.getElementById('api-selector')?.value ?? 'auto',
        headless:     headlessToggle?.checked ?? false,
        record_video: videoToggle?.checked ?? false,
        agent_speed:  parseInt(speedSlider?.value ?? 3)
    }));

    chatInput.value = '';
    chatInput.style.height = 'auto';
    chatInput.classList.remove('requires-input');

    if (isWaitingForInput) {
        isWaitingForInput = false;
        chatInput.placeholder = 'Give Rio a task...';
    }
}

if (sendBtn) sendBtn.addEventListener('click', sendMessage);

if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    chatInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
}

// ── Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
        document.querySelectorAll('[style*="position:fixed"][style*="z-index:99999"]').forEach(o => {
            if (o.tagName !== 'DIV' || o.classList.contains('rio-toast')) return;
            o.remove();
        });
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        if (chatInput) chatInput.focus();
    }
});

// ── Boot
connect();

// ── Ollama Auto-Detect: Show option in dropdown if server found it
(async () => {
    try {
        const res = await fetch('/api/models');
        if (!res.ok) return;
        const models = await res.json();
        const ollamaModel = models.find(m => m.label && m.label.includes('Ollama'));
        if (ollamaModel) {
            const opt = document.getElementById('ollama-option');
            if (opt) {
                opt.textContent = ollamaModel.label;
                opt.value = ollamaModel.value;
                opt.style.display = '';  // make it visible
            }
        }
    } catch (e) {
        // Ollama not available or /api/models not ready — ignore silently
    }
})();
