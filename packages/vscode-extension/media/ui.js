// Research Panel webview client. No framework; event delegation + small components.
(function () {
  const vscode = acquireVsCodeApi();
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const app = document.getElementById('app');
  let INDEX = [];
  try { INDEX = JSON.parse(document.getElementById('index')?.textContent || '[]'); } catch (e) { INDEX = []; }

  const post = (m) => vscode.postMessage(m);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function toast(text) {
    const t = document.createElement('div');
    t.className = 'toast fade-in';
    t.textContent = text;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 1600);
  }

  // ------------------------------------------------------------------ click delegation
  document.addEventListener('click', (ev) => {
    const menuBtn = ev.target.closest('[data-menu]');
    $$('.menu .pop').forEach((p) => { if (!menuBtn || p !== menuBtn.nextElementSibling) p.classList.add('hidden'); });
    if (menuBtn) { menuBtn.nextElementSibling.classList.toggle('hidden'); ev.preventDefault(); return; }

    const cmd = ev.target.closest('[data-cmd]');
    if (cmd) {
      ev.preventDefault(); ev.stopPropagation();
      let args = [];
      try { args = JSON.parse(cmd.getAttribute('data-args') || '[]'); } catch (e) { /* */ }
      if (cmd.getAttribute('data-cmd') === 'research.copyText') { post({ t: 'copy', text: args[0] }); toast('Copied'); return; }
      post({ t: 'cmd', command: cmd.getAttribute('data-cmd'), args });
      return;
    }
    const file = ev.target.closest('[data-file]');
    if (file) { ev.preventDefault(); ev.stopPropagation(); post({ t: 'file', path: file.getAttribute('data-file') }); return; }
    const open = ev.target.closest('[data-open]');
    if (open) {
      ev.preventDefault(); ev.stopPropagation();
      post({ t: 'open', id: open.getAttribute('data-open'), side: ev.altKey || ev.metaKey || ev.ctrlKey });
      return;
    }
    if (ev.target.closest('a[href]')) return; // external links handled by VS Code
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && ev.target.classList?.contains('item')) ev.target.click();
    if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') { const f = $('#form'); if (f) { ev.preventDefault(); submit(f); } }
  });

  // ------------------------------------------------------------------ messages from extension
  window.addEventListener('message', (ev) => {
    const m = ev.data;
    if (m.t === 'render') {
      const y = window.scrollY;
      const openMenus = [];
      app.innerHTML = m.html;
      if (m.index) INDEX = m.index;
      window.scrollTo(0, y);
      initForms();
    } else if (m.t === 'toast') toast(m.text);
    else if (m.t === 'formError') {
      const f = $('#form');
      if (f) { $('.err', f).textContent = m.message; setBusy(f, false); }
    } else if (m.t === 'busy') { const f = $('#form'); if (f) setBusy(f, m.on); }
  });

  // ------------------------------------------------------------------ forms
  function setBusy(f, on) {
    const b = $('button[type=submit]', f);
    if (b) { b.disabled = on; b.innerHTML = on ? '<span class="spinner"></span> Saving…' : b.dataset.label || b.innerHTML; }
  }

  function coerce(s) {
    const t = s.trim();
    if (t === '') return '';
    if (/^(true|false)$/i.test(t)) return t.toLowerCase() === 'true';
    if (/^(null|none)$/i.test(t)) return null;
    if (/^[-+]?\d+$/.test(t)) return parseInt(t, 10);
    if (/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(t)) return parseFloat(t);
    if (/^[\[{]/.test(t)) { try { return JSON.parse(t); } catch (e) { /* fallthrough */ } }
    return s;
  }

  function collect(f) {
    const out = {};
    $$('[data-field]', f).forEach((el) => {
      const name = el.getAttribute('data-field');
      const type = el.getAttribute('data-type');
      if (type === 'text') out[name] = el.value.trim() === '' ? null : el.value;
      else if (type === 'number') out[name] = el.value === '' ? null : Number(el.value);
      else if (type === 'check') out[name] = el.checked;
      else if (type === 'list') out[name] = el.value.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
      else if (type === 'seg') out[name] = el.getAttribute('data-value') || null;
      else if (type === 'entity') { const v = JSON.parse(el.getAttribute('data-value') || '[]'); out[name] = v[0] || null; }
      else if (type === 'entities') out[name] = JSON.parse(el.getAttribute('data-value') || '[]');
      else if (type === 'kv') {
        const o = {};
        $$('.kvrow', el).forEach((r) => { const k = $('[data-k]', r).value.trim(); if (k) o[k] = coerce($('[data-v]', r).value); });
        out[name] = o;
      }
    });
    return out;
  }

  function submit(f) {
    const values = collect(f);
    const missing = $$('[data-required]', f).filter((el) => !el.value.trim());
    if (missing.length) {
      $('.err', f).textContent = 'Please fill in the required fields.';
      missing[0].focus();
      return;
    }
    $('.err', f).textContent = '';
    setBusy(f, true);
    let context = null;
    try { context = JSON.parse(f.getAttribute('data-context') || 'null'); } catch (e) { /* */ }
    post({ t: 'submit', kind: f.getAttribute('data-kind'), values, context });
  }

  function autosize(t) { t.style.height = 'auto'; t.style.height = Math.min(480, t.scrollHeight + 2) + 'px'; }

  const TYPE_ICON = { question: 'question', experiment: 'beaker', run: 'play-circle', finding: 'lightbulb', decision: 'law', checkpoint: 'bookmark', artifact: 'file', note: 'note' };

  function initPicker(p) {
    const multi = p.getAttribute('data-type') === 'entities';
    const filter = (p.getAttribute('data-filter') || '').split(',').filter(Boolean);
    const input = $('input', p);
    const dd = $('.dd', p);
    const chips = $('.chips', p);
    let value = JSON.parse(p.getAttribute('data-value') || '[]');
    let act = 0;
    let opts = [];
    const find = (id) => INDEX.find((i) => i.id === id);
    const save = () => { p.setAttribute('data-value', JSON.stringify(value)); renderChips(); };
    function renderChips() {
      chips.innerHTML = value.map((id) => {
        const it = find(id);
        return `<span class="chip"><i class="codicon codicon-${TYPE_ICON[it?.type] || 'circle-outline'}"></i><span class="id">${esc(id)}</span><span class="t">${esc(it?.title || '')}</span><button type="button" data-rm="${esc(id)}" title="Remove"><i class="codicon codicon-close"></i></button></span>`;
      }).join('');
      $$('[data-rm]', chips).forEach((b) => b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); value = value.filter((x) => x !== b.getAttribute('data-rm')); save(); }));
      if (!multi) input.placeholder = value.length ? 'Replace…' : input.placeholder;
    }
    function show() {
      const q = input.value.trim().toLowerCase();
      opts = INDEX.filter((i) => (!filter.length || filter.includes(i.type)) && !value.includes(i.id) &&
        (!q || i.id.toLowerCase().includes(q) || (i.title || '').toLowerCase().includes(q))).slice(0, 40);
      act = 0;
      dd.innerHTML = opts.length ? opts.map((o, i) => `<div class="opt${i === act ? ' act' : ''}" data-i="${i}"><i class="codicon codicon-${TYPE_ICON[o.type] || 'circle-outline'}"></i><span class="id">${esc(o.id)}</span><span class="ttl">${esc(o.title || '')}</span>${o.status ? `<span class="muted small" style="margin-left:auto">${esc(o.status)}</span>` : ''}</div>`).join('')
        : `<div class="muted small" style="padding:6px 8px">No matches${filter.length ? ' (' + filter.join(', ') + ')' : ''}</div>`;
      dd.classList.remove('hidden');
      $$('.opt', dd).forEach((el) => el.addEventListener('mousedown', (e) => { e.preventDefault(); pick(opts[+el.getAttribute('data-i')]); }));
    }
    function pick(o) {
      if (!o) return;
      value = multi ? [...value, o.id] : [o.id];
      input.value = '';
      save();
      if (multi) show(); else dd.classList.add('hidden');
    }
    input.addEventListener('focus', show);
    input.addEventListener('input', show);
    input.addEventListener('blur', () => setTimeout(() => dd.classList.add('hidden'), 120));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        act = Math.max(0, Math.min(opts.length - 1, act + (e.key === 'ArrowDown' ? 1 : -1)));
        $$('.opt', dd).forEach((el, i) => el.classList.toggle('act', i === act));
        $$('.opt', dd)[act]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter' && !(e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        if (opts[act]) pick(opts[act]);
        else if (/^[A-Z]+-\d+$/i.test(input.value.trim())) pick({ id: input.value.trim().toUpperCase() });
      } else if (e.key === 'Backspace' && !input.value && value.length) { value = value.slice(0, -1); save(); }
      else if (e.key === 'Escape') dd.classList.add('hidden');
    });
    renderChips();
  }

  function initForms() {
    const f = $('#form');
    if (!f || f.dataset.ready) return;
    f.dataset.ready = '1';
    const sb = $('button[type=submit]', f);
    if (sb) sb.dataset.label = sb.innerHTML;
    f.addEventListener('submit', (e) => { e.preventDefault(); submit(f); });
    $$('[data-cancel]', f).forEach((b) => b.addEventListener('click', () => post({ t: 'cancel' })));
    $$('textarea[data-autosize]', f).forEach((t) => { autosize(t); t.addEventListener('input', () => autosize(t)); });
    $$('.picker', f).forEach(initPicker);
    $$('.seg', f).forEach((s) => {
      $$('button', s).forEach((b) => b.addEventListener('click', () => {
        const v = b.getAttribute('data-v');
        const was = s.getAttribute('data-value');
        const nv = was === v && s.getAttribute('data-field') !== 'mode' ? '' : v;
        s.setAttribute('data-value', nv);
        $$('button', s).forEach((x) => x.classList.toggle('on', x.getAttribute('data-v') === nv));
        if (s.getAttribute('data-field') === 'mode') f.classList.toggle('mode-slurm', nv === 'slurm');
      }));
      if (s.getAttribute('data-field') === 'mode') f.classList.toggle('mode-slurm', s.getAttribute('data-value') === 'slurm');
    });
    $$('.kv', f).forEach((kv) => {
      const base = JSON.parse(kv.getAttribute('data-base') || '{}');
      const mark = (row) => {
        const k = $('[data-k]', row).value.trim();
        const changed = k in base && JSON.stringify(coerce($('[data-v]', row).value)) !== JSON.stringify(base[k]);
        row.classList.toggle('changed', changed);
      };
      kv.addEventListener('input', (e) => { const r = e.target.closest('.kvrow'); if (r) mark(r); });
      kv.addEventListener('click', (e) => {
        if (e.target.closest('[data-kv-del]')) { e.preventDefault(); e.target.closest('.kvrow').remove(); }
        if (e.target.closest('[data-kv-add]')) {
          e.preventDefault();
          const row = document.createElement('div');
          row.className = 'kvrow';
          row.innerHTML = '<input type="text" class="mono" data-k placeholder="name"><input type="text" class="mono" data-v placeholder="value"><button type="button" class="icon" data-kv-del title="Remove"><i class="codicon codicon-close"></i></button>';
          kv.insertBefore(row, e.target.closest('[data-kv-add]'));
          $('[data-k]', row).focus();
        }
      });
    });
    const af = $('[autofocus]', f) || $('input[type=text], textarea', f);
    if (af) setTimeout(() => af.focus(), 50);
  }

  initForms();
  post({ t: 'ready' });
})();
