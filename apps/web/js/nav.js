/**
 * 全局导航：学生端 / 并发联调 / 管理端
 * + dhConfirm / dhPrompt（替代 window.confirm / prompt）
 */
(function () {
  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function dhConfirm(message, opts) {
    const o = opts || {};
    const title = o.title || '请确认';
    const okText = o.okText || '确定';
    const cancelText = o.cancelText || '取消';
    const danger = !!o.danger;

    return new Promise((resolve) => {
      const el = document.createElement('div');
      el.className = 'modal-overlay open';
      el.setAttribute('role', 'dialog');
      el.setAttribute('aria-modal', 'true');
      el.innerHTML = `
        <div class="modal-dialog" style="max-width:420px;">
          <h3 style="margin-bottom:8px;">${escapeHtml(title)}</h3>
          <p style="color:var(--text-muted); font-size:0.9rem; line-height:1.55; white-space:pre-wrap;">${escapeHtml(message)}</p>
          <div class="modal-actions">
            <button type="button" class="btn btn-ghost btn-sm" data-dh-cancel>${escapeHtml(cancelText)}</button>
            <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'} btn-sm" data-dh-ok>${escapeHtml(okText)}</button>
          </div>
        </div>`;

      const done = (val) => {
        el.classList.remove('open');
        setTimeout(() => el.remove(), 280);
        document.removeEventListener('keydown', onKey);
        resolve(val);
      };
      const onKey = (e) => {
        if (e.key === 'Escape') done(false);
        if (e.key === 'Enter') done(true);
      };

      el.addEventListener('click', (e) => { if (e.target === el) done(false); });
      el.querySelector('[data-dh-cancel]').addEventListener('click', () => done(false));
      el.querySelector('[data-dh-ok]').addEventListener('click', () => done(true));
      document.addEventListener('keydown', onKey);
      document.body.appendChild(el);
      el.querySelector('[data-dh-ok]')?.focus();
    });
  }

  function dhPrompt(message, defaultValue, opts) {
    const o = opts || {};
    const title = o.title || '请输入';
    const okText = o.okText || '确定';
    const cancelText = o.cancelText || '取消';
    const initial = defaultValue == null ? '' : String(defaultValue);

    return new Promise((resolve) => {
      const el = document.createElement('div');
      el.className = 'modal-overlay open';
      el.setAttribute('role', 'dialog');
      el.setAttribute('aria-modal', 'true');
      el.innerHTML = `
        <div class="modal-dialog" style="max-width:420px;">
          <h3 style="margin-bottom:8px;">${escapeHtml(title)}</h3>
          <p style="color:var(--text-muted); font-size:0.9rem; line-height:1.55; white-space:pre-wrap;">${escapeHtml(message)}</p>
          <input type="text" data-dh-input class="form-input" style="width:100%;margin:12px 0;padding:10px 14px;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-family:inherit;" value="${escapeHtml(initial)}" />
          <div class="modal-actions">
            <button type="button" class="btn btn-ghost btn-sm" data-dh-cancel>${escapeHtml(cancelText)}</button>
            <button type="button" class="btn btn-primary btn-sm" data-dh-ok>${escapeHtml(okText)}</button>
          </div>
        </div>`;
      const input = el.querySelector('[data-dh-input]');
      const done = (val) => {
        el.classList.remove('open');
        setTimeout(() => el.remove(), 280);
        document.removeEventListener('keydown', onKey);
        resolve(val);
      };
      const onKey = (e) => {
        if (e.key === 'Escape') done(null);
        if (e.key === 'Enter') done(input.value);
      };
      el.addEventListener('click', (e) => { if (e.target === el) done(null); });
      el.querySelector('[data-dh-cancel]').addEventListener('click', () => done(null));
      el.querySelector('[data-dh-ok]').addEventListener('click', () => done(input.value));
      document.addEventListener('keydown', onKey);
      document.body.appendChild(el);
      input.focus();
      input.select();
    });
  }

  function pathMatches(href) {
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    if (href === '/' || href === '/index.html') {
      return path === '/' || /\/index\.html$/i.test(path);
    }
    if (href === '/concurrent') return path === '/concurrent' || /\/concurrent\.html$/i.test(path);
    if (href === '/monitor') return path === '/monitor' || /\/monitor\.html$/i.test(path);
    if (href === '/admin') return path === '/admin' || /\/admin\.html$/i.test(path);
    return path === href || path.endsWith(href);
  }

  function ensureAppBottomNav() {
    if (document.getElementById('appBottomNav')) return;
    const items = [
      { href: '/', label: '学生端', ico: '💬' },
      { href: '/concurrent', label: '并发', ico: '⧉' },
      { href: '/monitor', label: '总控', ico: '◎' },
      { href: '/admin', label: '管理', ico: '⚙️' },
    ];
    const nav = document.createElement('nav');
    nav.id = 'appBottomNav';
    nav.className = 'app-bottom-nav';
    nav.setAttribute('aria-label', '主导航');
    nav.innerHTML = items.map((it) => {
      const cls = pathMatches(it.href) ? 'active' : '';
      return `<a href="${it.href}" class="${cls}"><span class="app-bnav-ico" aria-hidden="true">${it.ico}</span><span class="app-bnav-lbl">${it.label}</span></a>`;
    }).join('');
    document.body.appendChild(nav);
  }

  function markActiveNav() {
    document.querySelectorAll('.nav-links a').forEach((a) => {
      a.classList.toggle('active', pathMatches(a.getAttribute('href') || ''));
    });
  }

  function init() {
    ensureAppBottomNav();
    markActiveNav();
    window.dhConfirm = dhConfirm;
    window.dhPrompt = dhPrompt;
  }

  window.dhConfirm = dhConfirm;
  window.dhPrompt = dhPrompt;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
