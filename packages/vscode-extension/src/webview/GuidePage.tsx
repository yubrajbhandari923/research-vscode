import { useState } from 'react';
import { Badge, Banner, Dialog, EmptyState, Icon, PageHeader, Section, State, kindOf, useAsync } from './common';
import type { Host } from './host';
import type { Page } from './types';
import { ReportsSection } from './ReportsSection';

type Go = (p: Page, target?: string) => void;

/** The Scientist Guide: a Quarto book on main for communicating the science. This page never writes prose. It shows what is important,
 *  what has a draft, what the agent is allowed to write, and builds or publishes the site. */
export function GuidePage({ host, go }: { host: Host; go: Go }) {
  const a = useAsync<any>(() => host.call('guide_status'), []);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [build, setBuild] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const act = async (name: string, fn: () => Promise<any>, ok: (r: any) => string) => {
    setBusy(name);
    try { const r = await fn(); const t = ok(r); if (t) setMsg({ ok: true, text: t }); a.reload(); return r; } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(''); }
  };
  return (
    <div className="page-body">
      <State a={a}>{(s) => !s.exists ? <Create host={host} s={s} act={act} msg={msg} setMsg={setMsg} busy={busy} /> : <>
        <PageHeader title="Guide" sub="The scientific story of this project, written as a Quarto book on the main branch and published as a website. Research OS keeps it tied to the evidence; the words are yours."
          actions={<>
            <button type="button" className="primary" disabled={!!busy || !s.quarto.ok} title={s.quarto.ok ? 'Render the website (the scientific edition); nothing in it is executed' : 'Quarto is needed to build'}
              onClick={async () => setBuild(await act('build', () => host.call('guide_build', { edition: 'scientific' }), () => ''))}>
              <Icon name="play" /> {busy === 'build' ? 'Building…' : 'Build site'}</button>
            <button type="button" disabled={!s.quarto.ok} onClick={() => host.run('research.guide.preview')}>Live preview</button>
            <button type="button" disabled={!s.undrafted && !s.highlights.length} onClick={() => host.run('research.guide.draft')}>Draft with an agent…</button>
          </>} />
        {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} role="status" onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
        {!s.quarto.ok && <Banner kind="warn"><b>Quarto is not installed.</b> You can write and organise the guide without it; building the website needs it. {s.quarto.hints[0]}</Banner>}
        {!s.enabled && <Banner kind="warn">The guide is switched off in Settings; nothing is logged or suggested.</Banner>}
        {build && <BuildResult host={host} s={s} b={build} />}
        <Results host={host} s={s} act={act} busy={busy} />
        <Chapters host={host} s={s} />
        <Limits s={s} go={go} />
        <References host={host} s={s} act={act} />
        <Publishing host={host} s={s} />
        <Website host={host} s={s} act={act} busy={busy} />
        <QuartoBlock host={host} s={s} />
        <ReportsSection host={host} onGuide={() => undefined} />
      </>}</State>
    </div>
  );
}

function Create({ host, s, act, msg, setMsg, busy }: any) {
  return <>
    <PageHeader title="Guide" sub="Communicate the science: a short, readable book of your key results, with your citations, published as a website." />
    {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} role="status" onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
    <EmptyState icon="file" title="There is no Scientist Guide yet."
      actions={<><button type="button" className="primary" disabled={!!busy} onClick={() => act('init', () => host.call('guide_init'), (r: any) => `Created ${r.guide_dir}: ${r.created.length} files. Commit them on main.`)}>Create the guide</button>
        <button type="button" onClick={() => host.openDoc('docs/GUIDE.md')}>How it works</button></>}>
      It is a Quarto book in <code>{s.path}</code> on the main branch: chapters you edit, a <code>references.bib</code> for your sources, a theme you own, and a GitHub workflow that publishes it on every push.
      Important results are logged into it automatically; an agent writes a short first draft only when you ask.
    </EmptyState>
  </>;
}

function BuildResult({ host, s, b }: any) {
  const ok = b.ok;
  return (
    <Banner kind={ok ? 'ok' : 'bad'}>
      <b>{ok ? 'The site was built.' : 'The site was not built cleanly.'}</b>{' '}
      {b.html?.ok && s.built.scientific && <button type="button" className="link" onClick={() => host.openGuide(s.abs, '_output/scientific/index.html')}>Open it in the browser</button>}
      {b.validation?.errors > 0 && <div>{b.validation.errors} provenance problem(s): a claim that cites evidence that was retracted, or a number that no longer matches. Run <code>research guide validate</code>.</div>}
      {b.leaks?.length > 0 && <div>Research OS identifiers are visible to readers in {b.leaks.length} place(s), for example {b.leaks[0].identifier} in {b.leaks[0].file}.</div>}
      {b.html && !b.html.ok && <pre className="diff" tabIndex={0}>{b.html.log}</pre>}
      {(b.warnings || []).map((w: string) => <div key={w}>{w}</div>)}
    </Banner>
  );
}

function Results({ host, s, act, busy }: any) {
  const [id, setId] = useState('');
  const hl = s.highlights as any[];
  return (
    <Section id="results" title="Important results" count={hl.length}
      lead={<>Chosen by <b>{{ supported: 'independent review', starred: 'your stars', all: 'every live finding' }[s.settings.highlights as string]}</b>. Each gets a short entry in Key results when it qualifies.</>}
      actions={<button type="button" className="sm" disabled={busy === 'log'} onClick={() => act('log', () => host.call('guide_log'), (r: any) => r.logged?.length ? `Logged ${r.logged.join(', ')} into the guide` : 'Every important result is already in the guide')}>Log now</button>}>
      {hl.length === 0 ? <p className="muted">None yet. A finding becomes an important result when an independent review marks it supported, or when you star it below.</p> : (
        <div className="tbl-wrap"><table><caption className="sr">Important results and their state in the guide</caption>
          <thead><tr><th scope="col">Result</th><th scope="col">Status</th><th scope="col" className="hide-narrow">Numbers</th><th scope="col">In the guide</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
          <tbody>{hl.map((h) => (
            <tr key={h.id}>
              <td><button type="button" className="link idlink" onClick={() => host.openEntity(h.id)}>{h.id}</button> {h.starred && <span title="You starred this finding" aria-label="starred">★</span>} <b>{h.title}</b></td>
              <td><Badge kind={kindOf(h.status)}>{h.status}</Badge></td>
              <td className="hide-narrow"><Badge kind={kindOf(h.numbers)}>{NUMBERS[h.numbers] || h.numbers}</Badge></td>
              <td>{h.drafted ? <Badge kind="ok">draft written</Badge> : h.logged ? <Badge kind="pend">entry, needs a draft</Badge> : <Badge kind="">not logged</Badge>}</td>
              <td className="min">
                {h.logged && <button type="button" className="sm" onClick={() => host.openGuide(s.abs, 'results.qmd')}>Open entry</button>}
                {!h.drafted && <button type="button" className="sm" onClick={() => host.run('research.guide.draft', h.id)}>Draft…</button>}
                <button type="button" className="sm" aria-pressed={h.starred} onClick={() => act('star', () => host.call('guide_highlight', { id: h.id, on: !h.starred }), () => h.starred ? `${h.id} is no longer starred` : `${h.id} marked important`)}>{h.starred ? 'Unstar' : 'Star'}</button>
              </td>
            </tr>))}</tbody></table></div>)}
      <form className="row" style={{ marginTop: 8 }} onSubmit={(e) => { e.preventDefault(); if (id.trim()) act('star', () => host.call('guide_highlight', { id: id.trim(), on: true }), () => `${id.trim().toUpperCase()} marked important`).then(() => setId('')); }}>
        <label htmlFor="g-star" className="sr">Finding to mark as an important result</label>
        <input id="g-star" type="text" value={id} onChange={(e) => setId(e.target.value)} placeholder="F-003" size={8} />
        <button type="submit" className="sm" disabled={!id.trim()}>Mark a finding as important</button>
      </form>
      {s.draft.prepared && !s.draft.ok && <Banner kind="bad"><b>The last agent draft went over your limits.</b><ul>{s.draft.problems.map((p: string) => <li key={p}>{p}</li>)}</ul>Trim it, or raise the limit in Settings.</Banner>}
      {s.draft.prepared && s.draft.ok && s.draft.changed.length > 0 && <p className="muted small">Last agent draft stayed within the limits ({s.draft.changed.join(', ')}).</p>}
    </Section>
  );
}

const NUMBERS: Record<string, string> = { verified: 'numbers verified', no_bindings: 'no numbers bound', mismatch: 'number mismatch', missing: 'number missing', stale: 'number stale', unverifiable: 'unverifiable' };
const STATE_LABEL: Record<string, string> = { fresh: 'up to date', stale: 'evidence changed', invalid: 'needs a fix' };

function Chapters({ host, s }: any) {
  const secs = Object.entries(s.provenance.sections) as [string, any][];
  const rev = s.review || {};
  return (
    <Section id="chapters" title="Chapters" count={secs.length} lead="Click a chapter to edit it. A chapter marked human is never edited by an agent.">
      <div className="tbl-wrap"><table><caption className="sr">Chapters of the guide</caption>
        <thead><tr><th scope="col">Chapter</th><th scope="col">Owner</th><th scope="col">Evidence</th><th scope="col" className="hide-narrow">Review</th></tr></thead>
        <tbody>{secs.map(([k, v]) => (
          <tr key={k}>
            <td><button type="button" className="link" onClick={() => host.openGuide(s.abs, k)}>{k}</button></td>
            <td>{v.owner === 'human' ? <Badge kind="info">human</Badge> : <span className="muted">agent may draft</span>}</td>
            <td title={v.why.join('\n')}><Badge kind={v.state === 'fresh' ? 'ok' : v.state === 'invalid' ? 'bad' : 'warn'}>{STATE_LABEL[v.state] || v.state}</Badge>{v.why[0] && <span className="sub">{v.why[0]}</span>}</td>
            <td className="hide-narrow">{rev[k]?.label || 'unreviewed'}</td>
          </tr>))}</tbody></table></div>
      {s.provenance.proposals.length > 0 && <p className="muted small">Agent proposals for human-owned chapters wait in: {s.provenance.proposals.map((p: string) => <button key={p} type="button" className="link" onClick={() => host.openGuide(s.abs, p)}>{p}</button>)}</p>}
      {s.provenance.issues.length > 0 && <Banner kind="bad"><b>{s.provenance.errors} problem(s) in the text:</b><ul>{s.provenance.issues.map((i: string) => <li key={i}>{i}</li>)}</ul></Banner>}
    </Section>
  );
}

function Limits({ s, go }: any) {
  const d = s.settings.draft;
  const trig: Record<string, string> = { manual: 'never remind me', result: 'remind me when an important result has no draft', checkpoint: 'remind me only after a checkpoint' };
  return (
    <Section id="limits" title="What an agent may write" lead="An agent writes only when you press “Draft with an agent”. These limits go into its brief and are checked afterwards."
      actions={<button type="button" className="sm" onClick={() => go('settings', 'guide')}>Change in Settings</button>}>
      <ul>
        <li>At most <b>{d.max_words} words</b> per section, <b>{d.max_sections}</b> section(s) per draft, <b>{d.max_figures}</b> figure(s) per section.</li>
        <li>It never edits human-owned chapters, your notes, or <code>references.bib</code>, and invents no citations: it leaves a note where one would help.</li>
        <li>Reminders: <b>{trig[d.trigger]}</b>. Logging into Key results is {s.settings.auto_log ? <b>automatic</b> : <b>off</b>} (no agent involved).</li>
      </ul>
    </Section>
  );
}

function References({ host, s, act }: any) {
  const [open, setOpen] = useState(false);
  const c = s.citations;
  const miss = Object.keys(c.missing);
  return (
    <Section id="refs" title="References" count={c.entries}
      lead="Your sources live in references.bib. Cite one in any chapter as [@key]."
      actions={<><button type="button" className="sm" onClick={() => setOpen(true)}><Icon name="plus" /> Add a reference</button>
        <button type="button" className="sm" onClick={() => host.openGuide(s.abs, 'references.bib')}>Open references.bib</button></>}>
      {miss.length > 0 && <Banner kind="bad"><b>Cited but not in references.bib:</b> {miss.map((k) => <code key={k}> @{k} </code>)}. They would show as “?” in the book.</Banner>}
      {c.entries === 0 ? <p className="muted">No references yet. Paste a BibTeX entry from Zotero, a publisher page or Google Scholar, or add one with a title and authors.</p>
        : <p className="muted small">{c.entries} reference(s){c.unused.length ? `; not cited yet: ${c.unused.join(', ')}` : ''}.</p>}
      {open && <AddRef host={host} onClose={() => setOpen(false)} done={(r: any) => { setOpen(false); act('cite', async () => r, () => `Added ${r.added.join(', ')}: cite it as ${r.cite_as}`); }} />}
    </Section>
  );
}

function AddRef({ host, onClose, done }: { host: Host; onClose: () => void; done: (r: any) => void }) {
  const [mode, setMode] = useState<'bibtex' | 'fields'>('bibtex');
  const [f, setF] = useState({ bibtex: '', title: '', authors: '', year: '', doi: '', journal: '' });
  const [err, setErr] = useState('');
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const submit = async () => {
    setErr('');
    try { done(await host.call('guide_cite_add', mode === 'bibtex' ? { bibtex: f.bibtex } : { title: f.title, authors: f.authors, year: f.year, doi: f.doi, journal: f.journal })); } catch (e: any) { setErr(e.message); }
  };
  return (
    <Dialog title="Add a reference" onClose={onClose} actions={<><button type="button" onClick={onClose}>Cancel</button>
      <button type="button" className="primary" onClick={submit} disabled={mode === 'bibtex' ? !f.bibtex.trim() : !f.title.trim()}>Add</button></>}>
      <div className="segmented" role="group" aria-label="How to add">
        <button type="button" aria-pressed={mode === 'bibtex'} onClick={() => setMode('bibtex')}>Paste BibTeX</button>
        <button type="button" aria-pressed={mode === 'fields'} onClick={() => setMode('fields')}>Type the details</button>
      </div>
      {mode === 'bibtex' ? <>
        <label htmlFor="rf-bib">BibTeX entry</label>
        <textarea id="rf-bib" rows={9} data-autofocus value={f.bibtex} onChange={set('bibtex')} placeholder={'@article{lee2021alpha,\n  author = {Lee, Wei},\n  title = {…},\n  year = {2021}\n}'} />
      </> : <>
        <label htmlFor="rf-t">Title</label><input id="rf-t" type="text" data-autofocus value={f.title} onChange={set('title')} />
        <label htmlFor="rf-a">Authors <span className="muted">(Last, First and Last, First)</span></label><input id="rf-a" type="text" value={f.authors} onChange={set('authors')} />
        <label htmlFor="rf-y">Year</label><input id="rf-y" type="text" value={f.year} onChange={set('year')} size={6} />
        <label htmlFor="rf-j">Journal <span className="muted">(optional)</span></label><input id="rf-j" type="text" value={f.journal} onChange={set('journal')} />
        <label htmlFor="rf-d">DOI <span className="muted">(optional)</span></label><input id="rf-d" type="text" value={f.doi} onChange={set('doi')} />
      </>}
      {err && <div className="field-error" role="alert">{err}</div>}
      <p className="muted small">Only add sources you have actually read. The key is made from the first author, year and title word unless the BibTeX gives one.</p>
    </Dialog>
  );
}

function Publishing({ host, s }: any) {
  const p = s.publishing;
  return (
    <Section id="publish" title="Publishing" lead="Every push to main builds the scientific edition and publishes it as a website on GitHub Pages.">
      {p.workflow ? <p><Badge kind="ok">workflow present</Badge> <code>{p.path}</code></p>
        : <Banner kind="warn">The publish workflow is missing. <code>research guide init</code> adds it without touching anything else.</Banner>}
      <ol className="small">
        <li>Commit the guide on <code>main</code> (<code>{s.path}</code> and <code>.github</code>) and push.</li>
        <li>On GitHub: <b>Settings → Pages → Build and deployment → Source: GitHub Actions</b> (once).</li>
        <li>Each push to main that touches the guide then updates the site. The website never shows Research OS identifiers or evidence panels.</li>
      </ol>
      <div className="row">
        {s.built.scientific && <button type="button" className="sm" onClick={() => host.openGuide(s.abs, '_output/scientific/index.html')}>Open the last build</button>}
        <button type="button" className="sm" onClick={() => host.openGuide(s.abs, 'theme.scss')}>Edit the theme</button>
        <button type="button" className="sm" onClick={() => host.openGuide(s.abs, '_quarto.yml')}>Book settings</button>
        <button type="button" className="sm" onClick={() => host.openDoc('docs/GUIDE.md')}>Guide documentation</button>
      </div>
    </Section>
  );
}

/** The research website: experiments published from their verified, committed reports. Nothing here pushes; unpublishing moves, never deletes. */
function Website({ host, s, act, busy }: any) {
  const a = useAsync<any>(() => host.call('site_status'), [s]);
  const [exp, setExp] = useState('');
  const [removing, setRemoving] = useState<any>(null);
  return (
    <Section id="website" title="Research website" lead="Verified experiments become editable pages next to the guide. Research OS adds files, never deletes or overwrites your edits, and never pushes.">
      <State a={a}>{(w) => <>
        <div className="row small">
          <Badge kind={w.quarto.ok ? 'ok' : 'warn'}>{w.quarto.ok ? 'Quarto ready' : 'Quarto missing'}</Badge>
          <Badge kind={w.workflow.exists ? 'ok' : 'warn'}>{w.workflow.exists ? 'Pages workflow' : 'no Pages workflow'}</Badge>
          <Badge kind={w.remote.url ? 'ok' : ''}>{w.remote.url ? 'remote set' : 'local only'}</Badge>
          {w.remote.site_url && <code>{w.remote.site_url}</code>}
        </div>
        {w.experiments.length === 0 ? <p className="muted">No experiment is on the website yet.</p> : (
          <div className="tbl-wrap"><table><caption className="sr">Experiments on the website</caption>
            <thead><tr><th scope="col">Experiment</th><th scope="col">State</th><th scope="col" className="hide-narrow">Verified commit</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
            <tbody>{w.experiments.map((e: any) => (
              <tr key={e.experiment}>
                <td><button type="button" className="link idlink" onClick={() => host.openEntity(e.experiment)}>{e.experiment}</button> <code>/{e.slug}/</code></td>
                <td>{e.status === 'published' ? <Badge kind="ok">published</Badge> : <Badge kind="pend">unpublished</Badge>}{e.edited_by_you.length > 0 && <> <Badge kind="">your edits kept</Badge></>}</td>
                <td className="hide-narrow"><code>{e.commit}</code></td>
                <td className="min">{e.status === 'published'
                  ? <button type="button" className="sm" disabled={!!busy} onClick={async () => setRemoving(await host.call('site_remove', { experiment: e.experiment }))}>Unpublish…</button>
                  : <button type="button" className="sm" disabled={!!busy} onClick={() => act('site_add', () => host.call('site_add', { experiment: e.experiment }), () => `${e.experiment} is back on the website`)}>Publish again</button>}</td>
              </tr>))}</tbody></table></div>)}
        <div className="row">
          <input aria-label="Experiment to publish" placeholder="EXP-001" value={exp} onChange={(ev) => setExp(ev.target.value)} />
          <button type="button" className="sm" disabled={!!busy || !exp.trim()} title="Publishes the experiment's verified, committed report and figures as a draft page you can edit"
            onClick={() => act('site_add', () => host.call('site_add', { experiment: exp.trim() }), (r: any) => `${r.experiment} published as a draft in ${r.dir}: ${r.written.length} new file(s). Fill in the TODOs, commit and push when ready.`).then(() => { a.reload(); setExp(''); })}>Publish experiment</button>
          <button type="button" className="sm" onClick={() => host.run('research.guide.preview')}>Live preview</button>
        </div>
        <ol className="small">{w.next.map((n: string) => <li key={n}><code>{n}</code></li>)}</ol>
        {removing && <Dialog title={`Unpublish ${removing.experiment}?`} onClose={() => setRemoving(null)}
          actions={<><button type="button" onClick={() => setRemoving(null)}>Cancel</button>
            <button type="button" className="primary" disabled={!!busy} onClick={async () => { const e = removing.experiment; setRemoving(null); await act('site_remove', () => host.call('site_remove', { experiment: e, apply: true }), () => `${e} unpublished; its files are kept in _unpublished/`); a.reload(); }}>Unpublish</button></>}>
          <p>The folder <code>{removing.from}</code> moves to <code>{removing.to}</code> and {removing.unlist.length} page(s) leave the book. {removing.edited_by_you.length > 0 && <b>Your edits in {removing.edited_by_you.length} file(s) move with it. </b>}Nothing is deleted; publishing it again restores it. The live site changes only after you commit and push.</p>
        </Dialog>}
      </>}</State>
    </Section>
  );
}

/** Which Quarto is in use, and how to update it. Updating is the user's own install; Research OS never runs an installer. */
function QuartoBlock({ host, s }: any) {
  const q = s.quarto;
  return (
    <Section id="quarto" title="Quarto"
      lead="Quarto builds the website and the PDF. It is only needed for Build, Preview and PDF; writing, logging results and citations work without it.">
      <p>{q.ok ? <Badge kind="ok">Quarto {q.version}</Badge> : <Badge kind="warn">{q.version ? `Quarto ${q.version} is too old` : 'not found'}</Badge>}{' '}
        {q.quarto && <code>{q.quarto}</code>} {q.ok && <Badge kind={q.pdf ? 'ok' : 'warn'}>PDF {q.pdf ? 'available' : 'unavailable'}</Badge>}</p>
      {q.problems.map((x: string) => <p key={x} className="help-warn">{x}</p>)}
      <details><summary>How to install or update Quarto</summary>
        <ol className="small">
          <li><b>macOS:</b> <code>brew upgrade --cask quarto</code> (or <code>brew install --cask quarto</code> the first time), or download the installer from quarto.org/docs/download.</li>
          <li><b>Windows:</b> <code>winget upgrade --id Posit.Quarto</code>, or run the new installer from quarto.org/docs/download.</li>
          <li><b>Linux:</b> install the newest <code>.deb</code>/<code>.rpm</code>/tarball from quarto.org/docs/download, or <code>sudo apt install ./quarto-*.deb</code>.</li>
          <li>Then press <b>Reload</b> (top right). Research OS needs Quarto 1.4 or newer; PDF output uses the Typst that ships inside Quarto, so there is nothing else to install.</li>
          <li>Installed somewhere unusual? Put it on <code>PATH</code>, or set <code>RESEARCH_QUARTO=/path/to/quarto</code>, or <code>guide.quarto</code> in the project config.</li>
        </ol></details>
      <p className="muted small">Check from a terminal: <code>research guide quarto doctor</code>. <button type="button" className="link" onClick={() => host.openDoc('docs/GUIDE.md#updating-quarto')}>Guide documentation</button></p>
    </Section>
  );
}
