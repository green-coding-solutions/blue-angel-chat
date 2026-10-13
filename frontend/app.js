'use strict';

const STATUS = {
    verified: {label: 'Verified', color: 'green', icon: 'check circle'},
    maybe: {label: 'Maybe', color: 'yellow', icon: 'question circle'},
    not_possible: {label: 'Not possible', color: 'red', icon: 'times circle'},
};

const VERDICT = {
    likely_eligible: {label: 'Likely eligible', color: 'green'},
    blockers_found: {label: 'Blockers found', color: 'red'},
    insufficient_information: {label: 'Insufficient information', color: 'orange'},
};

const TOOL_DISPLAY = {
    mcp__web__fetch_page: {label: 'Fetch', icon: 'globe', arg: input => input.url},
    WebSearch: {label: 'Search', icon: 'search', arg: input => input.query},
    StructuredOutput: {label: 'Writing report', icon: 'file alternate outline', arg: () => ''},
};

const state = {
    sessionId: null,
    running: false,
};

const messagesEl = document.getElementById('messages');
const form = document.getElementById('chat-form');
const input = document.getElementById('chat-input');
const sendButton = document.getElementById('send-button');
const sendLabel = document.getElementById('send-label');

// ---------- helpers ----------

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
}

function icon(name) {
    return el('i', `${name} icon`);
}

function safeHref(url) {
    try {
        const parsed = new URL(url);
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
    } catch {
        return null;
    }
}

function scrollToBottom() {
    const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 250;
    if (nearBottom) window.scrollTo({top: document.body.scrollHeight});
}

function addUserMessage(text) {
    const row = el('div', 'message-row user');
    row.append(el('div', 'ui segment bubble', text));
    messagesEl.append(row);
    window.scrollTo({top: document.body.scrollHeight});
}

function setComposerMode() {
    if (state.sessionId) {
        input.type = 'text';
        input.placeholder = 'Answer open questions or ask a follow-up…';
        sendLabel.textContent = 'Send';
        sendButton.querySelector('i').className = 'paper plane icon';
    } else {
        input.placeholder = 'https://www.example.com/product';
        sendLabel.textContent = 'Assess';
        sendButton.querySelector('i').className = 'search icon';
    }
}

function setRunning(running) {
    state.running = running;
    input.disabled = running;
    sendButton.classList.toggle('loading', running);
    sendButton.disabled = running;
    if (!running) input.focus();
}

// ---------- agent turn rendering ----------

class AgentTurn {
    constructor() {
        const row = el('div', 'message-row assistant');
        this.bubble = el('div', 'ui segment bubble');
        this.timeline = el('div', 'timeline');
        this.bubble.append(this.timeline);
        row.append(this.bubble);
        messagesEl.append(row);
        this.tools = new Map();
        this.current = null;
        this.statusStep = this.addStep('status', 'circle notch loading', 'Starting agent…');
    }

    addStep(kind, iconName, text) {
        const step = el('div', `step ${kind}`);
        const body = el('div', 'step-body', text);
        step.append(icon(iconName), body);
        this.timeline.append(step);
        scrollToBottom();
        return step;
    }

    removeStatus() {
        if (this.statusStep) {
            this.statusStep.remove();
            this.statusStep = null;
        }
    }

    startText() {
        this.removeStatus();
        this.current = this.addStep('text', 'circle small blue', '');
    }

    appendText(text) {
        if (!this.current || !this.current.classList.contains('text')) this.startText();
        this.current.querySelector('.step-body').textContent += text;
        scrollToBottom();
    }

    startThinking() {
        this.removeStatus();
        this.current = this.addStep('thinking collapsed', 'lightbulb outline grey', '');
        this.current.addEventListener('click', e => e.currentTarget.classList.toggle('collapsed'));
    }

    appendThinking(text) {
        if (!this.current || !this.current.classList.contains('thinking')) this.startThinking();
        this.current.querySelector('.step-body').textContent += text;
        scrollToBottom();
    }

    toolStart(event) {
        this.removeStatus();
        // drop empty text/thinking blocks so the timeline stays compact
        if (this.current && !this.current.querySelector('.step-body').textContent.trim()) this.current.remove();
        this.current = null;

        const display = TOOL_DISPLAY[event.name] || {label: event.name, icon: 'wrench', arg: i => JSON.stringify(i)};
        const step = el('div', 'step tool');
        const body = el('div', 'step-body');
        body.append(el('span', 'tool-name', display.label + ' '), el('span', 'tool-arg', display.arg(event.input || {}) || ''));
        const meta = el('div', 'tool-meta', 'running…');
        body.append(meta);
        step.append(icon('circle notch loading grey'), body);
        this.timeline.append(step);
        this.tools.set(event.id, {step, meta, display});
        scrollToBottom();
    }

    toolResult(event) {
        const tool = this.tools.get(event.id);
        if (!tool) return;
        const iconEl = tool.step.querySelector('i.icon');
        if (event.is_error) {
            iconEl.className = 'times circle red icon';
            tool.meta.textContent = event.preview.split('\n')[0];
            tool.meta.classList.add('error');
        } else {
            iconEl.className = `${tool.display.icon} green icon`;
            if (event.name === 'StructuredOutput') {
                tool.meta.textContent = 'complete';
                return;
            }
            const titleLine = event.preview.split('\n').find(line => line.startsWith('Title: '));
            const title = titleLine ? titleLine.slice(7) : '';
            tool.meta.textContent = `${event.chars.toLocaleString()} chars${title ? ' · ' + title : ''}`;
        }
    }

    toolProgress(event) {
        const tool = this.tools.get(event.id);
        if (tool) tool.meta.textContent = `${event.chars.toLocaleString()} chars written…`;
        scrollToBottom();
    }

    status(message) {
        if (this.statusStep) this.statusStep.querySelector('.step-body').textContent = message;
    }

    rateLimit(event) {
        const resets = event.resets_at ? new Date(event.resets_at * 1000).toLocaleString() : 'unknown';
        const box = el('div', `ui ${event.status === 'rejected' ? 'negative' : 'warning'} message`);
        box.append(el('div', 'header', event.status === 'rejected' ? 'Subscription limit reached' : 'Approaching subscription limit'));
        box.append(el('p', null, `Limit: ${event.limit_type || 'unknown'} · resets at ${resets}`));
        this.timeline.append(box);
    }

    error(event) {
        this.removeStatus();
        const box = el('div', 'ui negative message');
        box.append(el('div', 'header', 'The assessment failed'), el('p', null, event.message));
        this.bubble.append(box);
        this.stats(event);
        scrollToBottom();
    }

    stats(event) {
        if (event.duration_ms === undefined) return;
        const parts = [`${(event.duration_ms / 1000).toFixed(0)} s`, `${event.turns} turns`];
        if (event.cost_usd !== null && event.cost_usd !== undefined) {
            const label = event.auth_mode === 'subscription' ? 'API-equivalent cost' : 'cost';
            parts.push(`${label} $${event.cost_usd.toFixed(2)}`);
        }
        this.bubble.append(el('div', 'run-stats', parts.join(' · ')));
    }

    finish() {
        this.removeStatus();
        for (const {step, meta} of this.tools.values()) {
            const iconEl = step.querySelector('i.icon');
            if (iconEl.classList.contains('loading')) {
                iconEl.className = 'minus circle grey icon';
                meta.textContent = 'cancelled';
            }
        }
    }

    result(event) {
        this.removeStatus();
        this.bubble.append(renderReport(event.assessment));
        $(this.bubble).find('.ui.accordion').accordion({exclusive: false});
        this.stats(event);
    }
}

// ---------- report rendering ----------

function renderReport(assessment) {
    const report = el('div', 'ui segment report');
    const {product, overall, criteria} = assessment;

    const header = el('h2', 'ui header');
    header.append(icon('leaf green'));
    const headerContent = el('div', 'content', product.name);
    headerContent.append(el('div', 'sub header', `${product.vendor} · ${product.category}`));
    header.append(headerContent);
    report.append(header);

    const meta = el('div', 'product-meta');
    const verdict = VERDICT[overall.verdict] || {label: overall.verdict, color: 'grey'};
    meta.append(el('span', `ui ${verdict.color} label`, verdict.label));
    for (const platform of product.platforms) meta.append(el('span', 'ui basic label', platform));
    const productLink = safeHref(product.url);
    if (productLink) {
        const a = el('a', 'ui basic label', product.url);
        a.href = productLink;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        meta.append(a);
    }
    report.append(meta);
    report.append(el('p', null, product.summary));
    report.append(el('p', null, overall.summary));

    const counts = {verified: 0, maybe: 0, not_possible: 0};
    for (const c of criteria) counts[c.status] = (counts[c.status] || 0) + 1;
    const stats = el('div', 'ui three tiny statistics');
    for (const key of ['verified', 'maybe', 'not_possible']) {
        const stat = el('div', `${STATUS[key].color} statistic`);
        stat.append(el('div', 'value', String(counts[key])), el('div', 'label', STATUS[key].label));
        stats.append(stat);
    }
    report.append(stats);

    if (overall.key_risks.length) {
        const risks = el('div', 'ui warning message');
        risks.append(el('div', 'header', 'Key risks'));
        const list = el('ul', 'list');
        for (const risk of overall.key_risks) list.append(el('li', null, risk));
        risks.append(list);
        report.append(risks);
    }

    let accordion = null;
    let currentSection = null;
    for (const c of criteria) {
        if (c.section !== currentSection) {
            currentSection = c.section;
            report.append(el('h4', 'ui dividing header section-header', `${c.section}. ${c.section_title}`));
            accordion = el('div', 'ui styled fluid accordion');
            report.append(accordion);
        }
        accordion.append(...renderCriterion(c));
    }
    return report;
}

function renderCriterion(c) {
    const status = STATUS[c.status] || {label: c.status, color: 'grey', icon: 'circle'};
    const title = el('div', 'title criterion-title');
    title.append(icon('dropdown'));
    const label = el('span', `ui ${status.color} label status-label`);
    label.append(icon(status.icon), document.createTextNode(status.label));
    title.append(label, el('span', 'criterion-name', `${c.id}. ${c.title}`));
    title.append(el('span', 'ui tiny basic label', `${c.confidence} confidence`));

    const content = el('div', 'content');
    content.append(el('div', 'criterion-question', c.question));
    content.append(el('p', null, c.comment));

    if (c.evidence.length) {
        content.append(el('h5', 'ui header', 'Evidence'));
        for (const ev of c.evidence) {
            const href = safeHref(ev.url);
            const link = el(href ? 'a' : 'span', 'evidence-url', ev.url);
            if (href) {
                link.href = href;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
            }
            content.append(link, el('div', 'evidence-quote', ev.quote));
        }
    }
    if (c.open_questions.length) {
        content.append(el('h5', 'ui header', 'Open questions for the applicant'));
        const list = el('div', 'ui bulleted list');
        for (const q of c.open_questions) list.append(el('div', 'item', q));
        content.append(list);
    }
    return [title, content];
}

// ---------- streaming ----------

async function streamRequest(path, payload, turn) {
    const response = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        let detail = response.statusText;
        try {
            const body = await response.json();
            detail = Array.isArray(body.detail) ? body.detail.map(d => d.msg).join(', ') : body.detail;
        } catch { /* keep statusText */ }
        turn.error({message: `Request failed (${response.status}): ${detail}`});
        return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
            const chunk = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const data = chunk.split('\n').filter(l => l.startsWith('data: ')).map(l => l.slice(6)).join('\n');
            if (data) handleEvent(JSON.parse(data), turn);
        }
    }
}

function handleEvent(event, turn) {
    switch (event.type) {
        case 'status': turn.status(event.message); break;
        case 'session':
            state.sessionId = event.session_id;
            turn.status(`Agent running (${event.model})…`);
            break;
        case 'text_start': turn.startText(); break;
        case 'text_delta': turn.appendText(event.text); break;
        case 'thinking_start': turn.startThinking(); break;
        case 'thinking_delta': turn.appendThinking(event.text); break;
        case 'tool_start': turn.toolStart(event); break;
        case 'tool_progress': turn.toolProgress(event); break;
        case 'tool_result': turn.toolResult(event); break;
        case 'rate_limit': turn.rateLimit(event); break;
        case 'result':
            state.sessionId = event.session_id;
            turn.result(event);
            break;
        case 'error': turn.error(event); break;
        case 'done': turn.finish(); break;
    }
}

async function submit(text) {
    const isFollowUp = Boolean(state.sessionId);
    let path, payload;
    if (isFollowUp) {
        path = '/api/chat';
        payload = {session_id: state.sessionId, message: text};
    } else {
        const url = /^https?:\/\//i.test(text) ? text : `https://${text}`;
        if (!safeHref(url)) {
            input.closest('.input').classList.add('error');
            return;
        }
        path = '/api/assess';
        payload = {url};
        text = url;
    }
    input.closest('.input').classList.remove('error');
    addUserMessage(text);
    input.value = '';
    setRunning(true);
    const turn = new AgentTurn();
    try {
        await streamRequest(path, payload, turn);
    } catch (err) {
        turn.error({message: `Connection lost: ${err.message}`});
    } finally {
        turn.finish();
        setRunning(false);
        setComposerMode();
    }
}

form.addEventListener('submit', e => {
    e.preventDefault();
    const text = input.value.trim();
    if (text && !state.running) submit(text);
});

document.getElementById('new-assessment').addEventListener('click', () => {
    if (state.running) return;
    state.sessionId = null;
    for (const row of [...messagesEl.children].slice(1)) row.remove();
    setComposerMode();
    input.focus();
});

fetch('/api/config').then(r => r.json()).then(cfg => {
    const label = document.getElementById('auth-label');
    label.textContent = `${cfg.model} · ${cfg.auth_mode === 'subscription' ? 'Claude subscription' : 'API key'}`;
    label.classList.add(cfg.auth_mode === 'subscription' ? 'teal' : 'blue');
    document.getElementById('criteria-label').textContent = `DE-UZ 215 · ${cfg.criteria.length} criteria`;
});

input.focus();
