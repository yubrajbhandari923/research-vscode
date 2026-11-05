import { useEffect, useMemo, useState } from 'react';
import { SettingRow } from './Controls';
import { Banner, Dialog, callPin, Diff, EmptyState, Icon, PageHeader, Section, State, useAsync } from './common';
import { Doctor } from './Doctor';
import type { Host } from './host';
import type { Preview, SettingOption, SettingsPayload } from './types';

const PROJECT_FILE = '.research/config.yaml';
const CONFLICT = /changed on disk/;

export function SettingsPage({ host, initialCategory }: { host: Host; initialCategory?: string }) {
  const saved = host.getState();
  const [cat, setCat] = useState<string>(initialCategory || saved.cat || 'policy');
  const [query, setQuery] = useState<string>(saved.query || '');
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [review, setReview] = useState<{ preview: Preview; scope: string } | null>(null);
  const [advanced, setAdvanced] = useState<boolean>(!!saved.advanced);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string; conflict?: boolean } | null>(null);
  const [removing, setRemoving] = useState(false);
  const a = useAsync<SettingsPayload>(() => host.call('settings'), []);
  const diag = useAsync<any>(() => host.call('config_check'), []);
  const about = useAsync<any>(() => host.call('ping'), []);

  const remember = (c: string, q: string) => host.setState({ ...host.getState(), cat: c, query: q });
  const pendingKeys = Object.keys(draft);
  const reloadAll = () => { setDraft({}); setErrors({}); setReview(null); setRemoving(false); setNotice(null); a.reload(); diag.reload(); };

  // An edit made in a text editor while this view is open shows up when the window regains focus (unsaved changes are never discarded silently).
  useEffect(() => {
    const on = () => { if (!Object.keys(draft).length && !review) { a.reload(); diag.reload(); } };
    window.addEventListener('focus', on);
    return () => window.removeEventListener('focus', on);
  }, [draft, review]); // eslint-disable-line react-hooks/exhaustive-deps

  const options = (d: SettingsPayload) => {
    const q = query.trim().toLowerCase();
    return d.options.filter((o) => q ? [o.key, o.label, o.help, o.why].join(' ').toLowerCase().includes(q) : o.category === cat && (advanced || !o.advanced));
  };
  const expected = (scope: string) => (scope === 'machine' ? a.data?.digests.machine : a.data?.digests.project) || undefined;
  const fail = (e: any) => { const text = String(e?.message || e); setNotice({ ok: false, text, conflict: CONFLICT.test(text) }); };

  const change = async (o: SettingOption, v: any) => {
    setDraft((d) => ({ ...d, [o.key]: v }));
    try {
      const p: Preview = await host.call('settings_preview', { changes: { [o.key]: v }, scope: 'project', expected: expected('project') });
      setErrors((e) => { const n = { ...e }; if (p.errors[o.key]) n[o.key] = p.errors[o.key]; else delete n[o.key]; return n; });
    } catch (e: any) { if (CONFLICT.test(String(e.message))) fail(e); else setErrors((x) => ({ ...x, [o.key]: String(e.message) })); }
  };

  const openReview = async (scope: string, changes = draft, remove = false) => {
    setBusy(true);
    try { setReview({ preview: await host.call('settings_preview', { changes, scope, remove, expected: expected(scope) }), scope }); }
    catch (e: any) { fail(e); }
    finally { setBusy(false); }
  };
  const resetOne = (o: SettingOption) => openReview('project', { [o.key]: null }, true).then(() => setRemoving(true));

  const apply = async (reason: string, confirm: boolean) => {
    if (!review) return;
    setBusy(true);
    try {
      const changes = removing ? Object.fromEntries(Object.keys(review.preview.values).map((k) => [k, null])) : draft;
      const r = await callPin(host, 'settings_apply', { changes, scope: review.scope, reason: reason || undefined, confirm, remove: removing, expected: expected(review.scope) }, 'Changing a protected policy setting needs your PIN.');
      setNotice({ ok: true, text: r.changed ? `Saved to ${review.scope === 'machine' ? 'the machine' : 'this project'} configuration (${r.path ? r.path.split(/[\\/]/).slice(-2).join('/') : 'config.yaml'}). Agent policy file regenerated.` : 'Nothing changed.' });
      setDraft({}); setReview(null); setRemoving(false); a.reload(); diag.reload();
    } catch (e: any) { fail(e); setReview(null); setRemoving(false); }
    finally { setBusy(false); }
  };

  if (a.error && !a.data) return (
    <div className="settings">
      <PageHeader title="Settings" crumbs={['Settings']} />
      <Banner kind="bad"><b>The configuration could not be read.</b> <span className="dim">{a.error}</span>
        <div className="row" style={{ marginTop: 8 }}><button type="button" onClick={() => host.openFile(PROJECT_FILE)}><Icon name="file" /> Open configuration file</button><button type="button" onClick={reloadAll}>Try again</button></div>
        <p className="meta" style={{ marginTop: 6 }}>Nothing was changed. Fix the file in the editor; this view reloads when you come back.</p></Banner>
    </div>);

  return (
    <div className="settings">
      <State a={a}>{(d) => {
        const catLabel = query ? 'Search results' : d.categories.find((c) => c.id === cat)?.label || '';
        const dg = diag.data;
        const issues = dg ? [...dg.errors.map((e: any) => ({ k: 'bad', t: `${e.key}: ${e.problem}` })), ...dg.unknown.map((u: any) => ({ k: 'warn', t: `${u.key} is not a known setting (kept as written)` })), ...dg.warnings.map((w: string) => ({ k: 'warn', t: w }))] : [];
        return (
          <>
            <PageHeader crumbs={['Settings', catLabel]} title="Settings"
              sub={<>Files are the source of truth: this view edits <code>{d.project_config_rel}</code> in place and keeps your comments. Edit the YAML directly any time.</>}
              actions={<><button type="button" onClick={() => host.openFile(d.project_config_rel)}><Icon name="file" /> Open configuration file</button>
                {d.machine_config && <button type="button" onClick={() => host.openFile(d.machine_config!)} title={d.machine_config}>Machine file</button>}
                <button type="button" className="ghost icon-btn" aria-label="Reload settings from disk" title="Reload from disk" onClick={reloadAll}><Icon name="refresh" /></button></>} />
            <p className="meta" data-testid="about" style={{ margin: '0 0 8px' }}>Research OS <b>{about.data?.version || '…'}</b>
              {about.data?.python && <> · core running from <code title={about.data.python}>{String(about.data.python).split('/').slice(-4).join('/')}</code></>}
              {' '}· the version is the same in the extension and its bundled core; compare it with the latest release on GitHub (yubrajbhandari923/research-vscode) to confirm you are up to date.</p>
            <div className="cfgbar">
              <label className="search"><span className="sr">Search settings</span><Icon name="search" />
                <input id="setting-search" type="search" placeholder="Search settings (e.g. budget, review, sandbox)" value={query}
                  onChange={(e) => { setQuery(e.target.value); remember(cat, e.target.value); }} /></label>
              <div className="precedence" title="Later layers override earlier ones">Precedence <b>default</b><span className="arrow">→</span><b>machine</b><span className="arrow">→</span><b>project</b></div>
              <span className="spacer" /><span className="meta">Effective-settings fingerprint <code>{d.fingerprint}</code></span>
            </div>
            {issues.length > 0 && <Banner kind={issues.some((i) => i.k === 'bad') ? 'bad' : 'warn'}><b>{issues.length} issue{issues.length === 1 ? '' : 's'} in the configuration file.</b> Nothing was rewritten.
              <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>{issues.slice(0, 5).map((i, n) => <li key={n}>{i.t}</li>)}</ul>{issues.length > 5 && <span className="meta">and {issues.length - 5} more (run <code>research config check</code>)</span>}</Banner>}
            {notice && <Banner kind={notice.ok ? 'ok' : 'bad'} onDismiss={() => setNotice(null)}>{notice.text}{notice.conflict && <> <button type="button" className="link" onClick={reloadAll}>Reload from disk</button> (unsaved changes here will be discarded)</>}</Banner>}
            <div className="settings-layout">
              <nav aria-label="Settings categories" className="cats">
                <div className="grp">Project</div>
                <button type="button" aria-current={!query && cat === 'health' ? 'true' : undefined} className={!query && cat === 'health' ? 'on' : ''}
                  onClick={() => { setQuery(''); setCat('health'); remember('health', ''); }}>Project health</button>
                <div className="grp">Configuration</div>
                {d.categories.map((c) => {
                  const n = d.options.filter((o) => o.category === c.id && (advanced || !o.advanced)).length;
                  return n ? <button key={c.id} type="button" aria-current={!query && cat === c.id ? 'true' : undefined} className={!query && cat === c.id ? 'on' : ''}
                    onClick={() => { setQuery(''); setCat(c.id); remember(c.id, ''); }}>{c.label}<span className="n">{n}</span></button> : null;
                })}
                <label className="check cats-note"><input type="checkbox" checked={advanced} onChange={(e) => { setAdvanced(e.target.checked); host.setState({ ...host.getState(), advanced: e.target.checked }); }} /> Show advanced settings</label>
                <p className="meta cats-note">Keys without a form stay editable in the YAML file.</p>
              </nav>
              <section aria-label="Settings" className="list">
                {!query && cat === 'health' && <Section id="health" title="Project health"><Doctor host={host} embedded /></Section>}
                {!query && cat === 'governance' && <ApprovalPin host={host} />}
                {!(cat === 'health' && !query) && (
                  <Section id="opts" title={catLabel} count={options(d).length} boxed>
                    {options(d).length === 0
                      ? <EmptyState inline icon="search" title={query ? `No settings match “${query}”.` : 'No settings in this group.'}>{query ? 'Try a shorter word, or edit the YAML file directly for keys that have no form.' : 'Tick “Show advanced settings” to see more.'}</EmptyState>
                      : options(d).map((o) => {
                        const dep = o.depends_on ? d.options.find((x) => x.key === o.depends_on) : null;
                        const depOff = dep && dep.type === 'bool' && !(draft[dep.key] ?? dep.value);
                        return <SettingRow key={o.key} option={o} draft={draft[o.key]} error={errors[o.key]} onChange={(v) => change(o, v)} onOpenFile={(f) => host.openFile(f)}
                          onReset={() => resetOne(o)} onDoc={(p) => host.openDoc(p)} disabledReason={depOff ? `Applies only while “${dep!.label}” is on.` : undefined} />;
                      })}
                  </Section>)}
                {pendingKeys.length > 0 && (
                  <div className="savebar" role="region" aria-label="Unsaved changes">
                    <Icon name="edit" /><span>{pendingKeys.length} unsaved change{pendingKeys.length > 1 ? 's' : ''}</span>
                    <span className="spacer" />
                    <button type="button" onClick={() => { setDraft({}); setErrors({}); }}>Discard</button>
                    <button type="button" className="primary" disabled={busy || Object.keys(errors).length > 0} onClick={() => { setRemoving(false); openReview('project'); }}>Review changes…</button>
                  </div>
                )}
              </section>
            </div>
            {review && <ReviewDialog preview={review.preview} initialScope={review.scope} removing={removing} busy={busy}
              onScope={(s) => openReview(s, removing ? Object.fromEntries(Object.keys(review.preview.values).map((k) => [k, null])) : draft, removing)}
              onClose={() => { setReview(null); setRemoving(false); }} onApply={apply} />}
          </>
        );
      }}</State>
    </div>
  );
}

function ReviewDialog({ preview: p, initialScope, removing, busy, onScope, onClose, onApply }: {
  preview: Preview; initialScope: string; removing: boolean; busy: boolean; onScope: (s: string) => void; onClose: () => void; onApply: (reason: string, confirm: boolean) => void;
}) {
  const [reason, setReason] = useState('');
  const [sure, setSure] = useState(false);
  const bad = Object.entries(p.errors);
  const needsSure = p.needs_confirmation;
  const blocked = bad.length > 0 || (needsSure && !sure) || busy;
  const keys = useMemo(() => Object.keys(p.values), [p.values]);
  return (
    <Dialog title={removing ? 'Remove override' : 'Review changes'} onClose={onClose} actions={<>
      <button type="button" onClick={onClose}>Cancel</button>
      <button type="button" className="primary" data-autofocus disabled={blocked} onClick={() => onApply(reason, sure)}>{removing ? 'Remove override' : 'Save changes'}</button></>}>
      <p>{keys.length} setting{keys.length === 1 ? '' : 's'}: {keys.map((k, i) => <span key={k}>{i > 0 && ', '}<code>{k}</code></span>)}</p>
      <div className="row"><label htmlFor="scope">Save to</label>
        <select id="scope" value={initialScope} onChange={(e) => onScope(e.target.value)}>
          <option value="project">This project (.research/config.yaml)</option><option value="machine">This machine (all projects)</option></select></div>
      {bad.map(([k, m]) => <div key={k} className="field-error" role="alert"><code>{k}</code> {m}</div>)}
      <Diff text={p.diff} />
      {p.affects.length > 0 && <p className="muted small">Also updates: {p.affects.join(', ')}</p>}
      {p.policy_keys.length > 0 && <p className="help-warn">Policy settings affect what agents are allowed to do. Future dispatches pick this up; sessions already running keep the policy recorded when they started.</p>}
      {needsSure && <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I understand this change can discard or expose work.</label>}
      <label htmlFor="why">Reason <span className="muted">(optional, kept in the audit log)</span></label>
      <input id="why" type="text" value={reason} onChange={(e) => setReason(e.target.value)} />
    </Dialog>
  );
}

/** Set or change the approval PIN. It guards against accidental autonomous changes; it is not a defence against a hostile agent. */
export function ApprovalPin({ host }: { host: Host }) {
  const g = useAsync<any>(() => host.call('governance'), []);
  const [cur, setCur] = useState('');
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const isSet = !!g.data?.pin?.set;
  const save = async () => {
    if (a.length < 4) return setMsg({ ok: false, text: 'Use at least 4 characters.' });
    if (a !== b) return setMsg({ ok: false, text: 'The two PINs differ.' });
    try { await host.call('approval_set_pin', { new: a, old: isSet ? cur : undefined }); setMsg({ ok: true, text: isSet ? 'PIN changed.' : 'PIN set.' }); setCur(''); setA(''); setB(''); g.reload(); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return (
    <Section id="pin" title="Approval PIN" boxed hint="Approving an agent's proposal, changing policy and promoting to main ask for this PIN. It guards against accidental autonomous changes, is stored on this machine only (never in the project or git), and is not a defence against a hostile agent.">
      <p>Status: {isSet ? <b>set</b> : <b>not set</b>}{g.data?.pin?.locked_for ? ` · locked for ${g.data.pin.locked_for}s after wrong attempts` : ''}</p>
      <form onSubmit={(e) => { e.preventDefault(); save(); }}>
        {isSet && <><label htmlFor="pin-cur">Current PIN</label><input id="pin-cur" type="password" autoComplete="off" value={cur} onChange={(e) => setCur(e.target.value)} /></>}
        <label htmlFor="pin-new">{isSet ? 'New PIN' : 'PIN'}</label><input id="pin-new" type="password" autoComplete="off" value={a} onChange={(e) => setA(e.target.value)} />
        <label htmlFor="pin-new2">Repeat PIN</label><input id="pin-new2" type="password" autoComplete="off" value={b} onChange={(e) => setB(e.target.value)} />
        <div className="row"><button type="submit" className="primary" disabled={!a}>{isSet ? 'Change PIN' : 'Set PIN'}</button></div>
      </form>
      {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
    </Section>);
}
