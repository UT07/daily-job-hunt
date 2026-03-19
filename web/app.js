// Job Hunt — Frontend JS
// Vanilla JS, no build step. Talks to FastAPI backend.

const API_BASE = window.location.hostname === "localhost"
    ? "http://localhost:8000"
    : "";  // Same origin when deployed behind API Gateway

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getInputs() {
    const jd = document.getElementById("jd-input").value.trim();
    if (jd.length < 20) {
        showError("Please paste a job description (at least 20 characters).");
        return null;
    }
    return {
        job_description: jd,
        job_title: document.getElementById("job-title").value.trim() || "Software Engineer",
        company: document.getElementById("company").value.trim() || "Unknown",
        resume_type: document.getElementById("resume-type").value,
    };
}

function setLoading(btnId, loading) {
    const btn = document.getElementById(btnId);
    if (loading) {
        btn.disabled = true;
        btn._originalText = btn.textContent;
        btn.innerHTML = '<span class="spinner"></span> Processing...';
    } else {
        btn.disabled = false;
        btn.textContent = btn._originalText || btn.textContent;
    }
}

function scoreBadgeClass(score) {
    if (score >= 85) return "score-green";
    if (score >= 60) return "score-yellow";
    return "score-red";
}

function addResult(html) {
    const container = document.getElementById("results");
    const div = document.createElement("div");
    div.className = "fade-in";
    div.innerHTML = html;
    container.prepend(div);
}

function showError(msg) {
    addResult(`
        <div class="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
            ${msg}
        </div>
    `);
}

async function apiCall(endpoint, body) {
    const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

function copyToClipboard(text, btnEl) {
    navigator.clipboard.writeText(text).then(() => {
        const orig = btnEl.textContent;
        btnEl.textContent = "Copied!";
        setTimeout(() => { btnEl.textContent = orig; }, 1500);
    });
}

// ---------------------------------------------------------------------------
// Score Resume
// ---------------------------------------------------------------------------

async function scoreResume() {
    const inputs = getInputs();
    if (!inputs) return;

    setLoading("btn-score", true);
    try {
        const data = await apiCall("/api/score", inputs);
        addResult(`
            <div class="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Score Card — ${inputs.company}</h3>
                <div class="flex gap-6 mb-4">
                    <div class="text-center">
                        <div class="score-badge ${scoreBadgeClass(data.ats_score)}">${data.ats_score}</div>
                        <div class="text-xs text-gray-500 mt-1">ATS</div>
                    </div>
                    <div class="text-center">
                        <div class="score-badge ${scoreBadgeClass(data.hiring_manager_score)}">${data.hiring_manager_score}</div>
                        <div class="text-xs text-gray-500 mt-1">Hiring Mgr</div>
                    </div>
                    <div class="text-center">
                        <div class="score-badge ${scoreBadgeClass(data.tech_recruiter_score)}">${data.tech_recruiter_score}</div>
                        <div class="text-xs text-gray-500 mt-1">Tech Recruiter</div>
                    </div>
                    <div class="text-center">
                        <div class="score-badge ${scoreBadgeClass(data.avg_score)}" style="width:56px;height:56px;font-size:1.3rem;">${data.avg_score}</div>
                        <div class="text-xs text-gray-500 mt-1">Average</div>
                    </div>
                </div>
                <p class="text-sm text-gray-600">${data.reasoning}</p>
                <p class="text-xs text-gray-400 mt-2">Resume: ${data.matched_resume}</p>
            </div>
        `);
    } catch (e) {
        showError(`Scoring failed: ${e.message}`);
    } finally {
        setLoading("btn-score", false);
    }
}

// ---------------------------------------------------------------------------
// Tailor Resume
// ---------------------------------------------------------------------------

async function tailorResume() {
    const inputs = getInputs();
    if (!inputs) return;

    setLoading("btn-tailor", true);
    try {
        const data = await apiCall("/api/tailor", inputs);
        const link = data.drive_url
            ? `<a href="${data.drive_url}" target="_blank" class="text-blue-600 underline">Open in Google Drive</a>`
            : `<span class="text-gray-400">Drive upload unavailable</span>`;
        addResult(`
            <div class="bg-white rounded-lg shadow-sm border border-emerald-200 p-6">
                <h3 class="text-sm font-semibold text-emerald-700 uppercase tracking-wide mb-4">Tailored Resume — ${inputs.company}</h3>
                <div class="flex gap-6 mb-4">
                    <div class="text-center">
                        <div class="score-badge ${scoreBadgeClass(data.ats_score)}">${data.ats_score}</div>
                        <div class="text-xs text-gray-500 mt-1">ATS</div>
                    </div>
                    <div class="text-center">
                        <div class="score-badge ${scoreBadgeClass(data.hiring_manager_score)}">${data.hiring_manager_score}</div>
                        <div class="text-xs text-gray-500 mt-1">Hiring Mgr</div>
                    </div>
                    <div class="text-center">
                        <div class="score-badge ${scoreBadgeClass(data.tech_recruiter_score)}">${data.tech_recruiter_score}</div>
                        <div class="text-xs text-gray-500 mt-1">Tech Recruiter</div>
                    </div>
                </div>
                <p class="text-sm">${link}</p>
            </div>
        `);
    } catch (e) {
        showError(`Tailoring failed: ${e.message}`);
    } finally {
        setLoading("btn-tailor", false);
    }
}

// ---------------------------------------------------------------------------
// Cover Letter
// ---------------------------------------------------------------------------

async function generateCoverLetter() {
    const inputs = getInputs();
    if (!inputs) return;

    setLoading("btn-cover", true);
    try {
        const data = await apiCall("/api/cover-letter", inputs);
        const link = data.drive_url
            ? `<a href="${data.drive_url}" target="_blank" class="text-blue-600 underline">Open in Google Drive</a>`
            : `<span class="text-gray-400">Drive upload unavailable</span>`;
        addResult(`
            <div class="bg-white rounded-lg shadow-sm border border-purple-200 p-6">
                <h3 class="text-sm font-semibold text-purple-700 uppercase tracking-wide mb-4">Cover Letter — ${inputs.company}</h3>
                <p class="text-sm">${link}</p>
            </div>
        `);
    } catch (e) {
        showError(`Cover letter failed: ${e.message}`);
    } finally {
        setLoading("btn-cover", false);
    }
}

// ---------------------------------------------------------------------------
// LinkedIn Contacts
// ---------------------------------------------------------------------------

async function findContacts() {
    const inputs = getInputs();
    if (!inputs) return;

    setLoading("btn-contacts", true);
    try {
        const data = await apiCall("/api/contacts", inputs);
        if (!data.contacts.length) {
            addResult(`
                <div class="bg-white rounded-lg shadow-sm border border-orange-200 p-6">
                    <h3 class="text-sm font-semibold text-orange-700 uppercase tracking-wide mb-2">LinkedIn Contacts — ${inputs.company}</h3>
                    <p class="text-sm text-gray-500">No contacts found.</p>
                </div>
            `);
            return;
        }
        const contactCards = data.contacts.map((c, i) => `
            <div class="border border-gray-100 rounded-lg p-4 ${i > 0 ? 'mt-3' : ''}">
                <div class="flex items-start justify-between">
                    <div>
                        <p class="text-sm font-medium text-gray-900">${c.role}</p>
                        <p class="text-xs text-gray-500 mt-0.5">${c.why}</p>
                    </div>
                    <a href="${c.search_url}" target="_blank" class="text-xs text-blue-600 underline whitespace-nowrap ml-4">Search LinkedIn</a>
                </div>
                <div class="mt-2 bg-gray-50 rounded p-3 text-sm text-gray-700 relative">
                    <p>${c.message}</p>
                    <button onclick="copyToClipboard(\`${c.message.replace(/`/g, '\\`').replace(/\\/g, '\\\\')}\`, this)"
                        class="absolute top-2 right-2 text-xs text-blue-600 hover:text-blue-800">Copy</button>
                </div>
            </div>
        `).join("");
        addResult(`
            <div class="bg-white rounded-lg shadow-sm border border-orange-200 p-6">
                <h3 class="text-sm font-semibold text-orange-700 uppercase tracking-wide mb-4">LinkedIn Contacts — ${inputs.company}</h3>
                ${contactCards}
            </div>
        `);
    } catch (e) {
        showError(`Contact search failed: ${e.message}`);
    } finally {
        setLoading("btn-contacts", false);
    }
}
