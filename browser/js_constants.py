"""JavaScript constants injected into the browser page for form and CAPTCHA detection."""

# Detects all visible form fields, labels, options, and the submit button.
# Handles: input, textarea, select, radio groups, hidden file inputs, iframe fields.
FORM_DETECTION_JS = """() => {
    const fields = [];
    const seen = new Set();

    // Main field selector
    const els = document.querySelectorAll(
        'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select'
    );

    for (let i = 0; i < els.length; i++) {
        const el = els[i];
        const rect = el.getBoundingClientRect();

        // Skip invisible elements (except file inputs which are often hidden)
        if (el.offsetParent === null && el.type !== 'file') continue;
        if (rect.width === 0 && rect.height === 0 && el.type !== 'file') continue;

        const id = el.id || el.name || ('field_' + i);
        if (seen.has(id)) continue;
        seen.add(id);

        // Detect label
        let label = '';
        let labelEl = el.labels && el.labels[0];
        if (!labelEl) labelEl = el.closest('label');
        if (!labelEl) {
            const wrapper = el.closest('[class*=field], [class*=question], [class*=form-group]');
            if (wrapper) labelEl = wrapper.querySelector('label, [class*=label]');
        }
        if (labelEl) label = labelEl.textContent.trim();
        if (!label) label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
        label = label.substring(0, 200);

        const field = {
            id: id,
            name: el.name || '',
            label: label,
            type: el.type || el.tagName.toLowerCase(),
            required: el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || '',
            options: null,
            rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height)
            }
        };

        // Select options
        if (el.tagName === 'SELECT') {
            field.options = Array.from(el.options)
                .map(o => ({ label: o.textContent.trim(), value: o.value }))
                .filter(o => o.value);
        }

        // Radio group options
        if (el.type === 'radio') {
            const group = document.querySelectorAll('input[name="' + el.name + '"]');
            field.options = Array.from(group).map(r => ({
                label: (r.labels && r.labels[0] ? r.labels[0].textContent : r.value).trim(),
                value: r.value
            }));
        }

        fields.push(field);
    }

    // Capture hidden file inputs (Greenhouse uses these for drag-drop resume upload)
    const fileInputs = document.querySelectorAll('input[type=file]');
    for (const fi of fileInputs) {
        const id = fi.id || fi.name || 'file_input';
        if (!seen.has(id)) {
            seen.add(id);
            fields.push({
                id: id,
                name: fi.name || '',
                label: fi.getAttribute('aria-label') || 'File Upload',
                type: 'file',
                required: fi.required,
                value: '',
                options: null,
                rect: { x: 0, y: 0, w: 0, h: 0 }
            });
        }
    }

    // Find submit button
    const submitBtn = document.querySelector(
        'button[type=submit], input[type=submit], ' +
        'button:not([type])[class*=submit], button:not([type])[class*=apply]'
    ) || document.querySelector('button:not([type])');

    return {
        fields: fields,
        submit_button: submitBtn ? {
            text: submitBtn.textContent.trim(),
            selector: submitBtn.id ? '#' + submitBtn.id : null
        } : null,
        page_title: document.title,
        page_url: window.location.href
    };
}"""


# Detects CAPTCHA type and sitekey on the current page.
# Checks: hCaptcha, reCAPTCHA v2/v3, Cloudflare Turnstile.
CAPTCHA_DETECTION_JS = """() => {
    const r = {};

    // hCaptcha (check first — some sites use both)
    const hc = document.querySelector('.h-captcha, [data-hcaptcha-sitekey]');
    if (hc) {
        r.type = 'hcaptcha';
        r.sitekey = hc.dataset.sitekey || hc.dataset.hcaptchaSitekey || '';
        return r;
    }

    // reCAPTCHA v2
    const rc2 = document.querySelector('.g-recaptcha, [data-sitekey]');
    if (rc2) {
        r.type = 'recaptcha_v2';
        r.sitekey = rc2.dataset.sitekey || '';
        return r;
    }

    // reCAPTCHA v3 (invisible)
    const rcScript = document.querySelector('script[src*="recaptcha/api.js?render="]');
    if (rcScript) {
        const match = rcScript.src.match(/render=([^&]+)/);
        if (match) {
            r.type = 'recaptcha_v3';
            r.sitekey = match[1];
            return r;
        }
    }

    // Cloudflare Turnstile
    const cf = document.querySelector('.cf-turnstile');
    if (cf) {
        r.type = 'turnstile';
        r.sitekey = cf.dataset.sitekey || '';
        return r;
    }

    return r;
}"""


# Detects if the page is a login page (for platforms requiring manual auth).
LOGIN_DETECTION_JS = """() => {
    const url = window.location.href.toLowerCase();
    const html = document.body ? document.body.innerText.toLowerCase() : '';
    const loginKeywords = ['sign in', 'log in', 'login', 'sso', 'authenticate'];
    const hasPasswordField = !!document.querySelector('input[type=password]');
    const hasLoginKeyword = loginKeywords.some(k => html.includes(k) || url.includes(k));
    return {
        login_required: hasPasswordField && hasLoginKeyword,
        url: window.location.href
    };
}"""
