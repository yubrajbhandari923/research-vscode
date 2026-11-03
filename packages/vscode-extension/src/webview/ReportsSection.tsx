import { useState } from 'react';
import { Banner, EmptyState, Icon, Section, State, ago, useAsync } from './common';
import type { Host } from './host';

/** Reports: the complete record. Not the Scientist Guide, which is the short story you write. */
export function ReportsSection({ host, onGuide }: { host: Host; onGuide?: () => void }) {
  const a = useAsync<any>(() => host.call('reports'), []);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const make = async (kind: 'report' | 'full') => {
    setBusy(kind); setMsg(null);
    try {
      const r = kind === 'report' ? await host.call('report', { fmt: 'both' }) : await host.call('report_full', { check: true });
      const files: string[] = kind === 'report' ? r.abs : [r.abs];
      setMsg({ ok: true, text: kind === 'report' ? `Report written: ${r.paths.join(', ')}` : `Full export written (${r.check?.ok === false ? 'coverage check FAILED' : 'every record accounted for'}): ${r.path}` });
      a.reload();
      host.openReport(files.find((f) => f.endsWith('.html')) || files[0]);
    } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(''); }
  };
  return (
    <Section id="reports" title="Reports"
      lead={<>A report is the complete, generated record: every finding with its evidence plots, failed directions, decisions, runs and syntheses. It is not the story you tell:
        for that, write the <button type="button" className="link" onClick={onGuide}>Scientist Guide</button>.</>}
      actions={<><button type="button" className="sm primary" disabled={!!busy} onClick={() => make('report')}><Icon name="file" /> {busy === 'report' ? 'Writing…' : 'Write project report'}</button>
        <button type="button" className="sm" disabled={!!busy} title="Every recorded field, deterministic, with a coverage check" onClick={() => make('full')}>{busy === 'full' ? 'Exporting…' : 'Full export'}</button></>}>
      {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} role="status" onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
      <State a={a}>{(d) => d.reports.length === 0 ? <EmptyState inline title="No report written yet.">Write one to get an HTML page and a Markdown file under <code>.research/reports/</code>.</EmptyState> : (
        <div className="tbl-wrap"><table><caption className="sr">Written reports</caption>
          <thead><tr><th scope="col">File</th><th scope="col">Written</th><th scope="col"><span className="sr">Open</span></th></tr></thead>
          <tbody>{d.reports.slice(0, 8).map((r: any) => (
            <tr key={r.name}><td><code>{r.name}</code>{r.full && <span className="sub">complete export</span>}</td><td>{ago(new Date(r.modified * 1000).toISOString())}</td>
              <td className="min"><button type="button" className="sm" onClick={() => host.openReport(r.abs)}>{r.name.endsWith('.html') ? 'Open in browser' : 'Open'}</button></td></tr>))}</tbody></table></div>)}</State>
    </Section>
  );
}
