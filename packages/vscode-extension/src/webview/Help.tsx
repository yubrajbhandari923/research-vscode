import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { SettingOption } from './types';

/** A `?` that explains a setting. Opens on hover or keyboard focus, stays open when clicked (also how touch works),
 *  closes on Escape or an outside click, and never covers the control it describes (it opens below the icon). */
export function Help({ option, onDoc }: { option: SettingOption; onDoc?: (path: string) => void }) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [flip, setFlip] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const id = useId();
  const open = hover || focus || pinned;
  const timer = useRef<number | undefined>(undefined);

  const enter = () => { window.clearTimeout(timer.current); setHover(true); };
  const leave = () => { window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setHover(false), 140); };

  useLayoutEffect(() => {
    if (!open || !pop.current || !wrap.current) return;
    const r = wrap.current.getBoundingClientRect();
    setFlip(r.left + 340 > window.innerWidth - 12);
  }, [open]);

  const close = useCallback(() => { setPinned(false); setHover(false); setFocus(false); }, []);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { close(); wrap.current?.querySelector('button')?.focus(); } };
    const away = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) close(); };
    document.addEventListener('keydown', key);
    document.addEventListener('mousedown', away);
    return () => { document.removeEventListener('keydown', key); document.removeEventListener('mousedown', away); };
  }, [open, close]);

  const o = option;
  return (
    <span className="help" ref={wrap} onMouseEnter={enter} onMouseLeave={leave}>
      <button type="button" className="help-btn" aria-label={`About ${o.label}`} aria-expanded={open} aria-controls={id}
        onClick={() => setPinned((p) => !p)} onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}>?</button>
      {open && (
        <div id={id} ref={pop} role="note" className={'help-pop' + (flip ? ' flip' : '')}>
          <p className="help-lead">{o.help}</p>
          {o.why && <p><b>Why</b> {o.why}</p>}
          {o.effect && <p><b>When changed</b> {o.effect}</p>}
          <dl>
            <dt>Default</dt><dd>{fmt(o.default)}{o.unit ? ` ${o.unit}` : ''}</dd>
            <dt>Now</dt><dd>{fmt(o.value)} <span className="muted">({o.source}{o.inherited ? ', inherited' : ''})</span></dd>
            {o.choices && <><dt>Allowed</dt><dd>{o.choices.join(', ')}</dd></>}
            {(o.min != null || o.max != null) && <><dt>Range</dt><dd>{o.min ?? '…'} to {o.max ?? '…'}</dd></>}
            {o.affects.length > 0 && <><dt>Changes</dt><dd>{o.affects.join(', ')}</dd></>}
            <dt>Key</dt><dd><code>{o.key.split('.').flatMap((k, i) => (i ? [<wbr key={'w' + i} />, '.' + k] : [k]))}</code></dd>
            <dt>File</dt><dd>{o.file ? <code>{o.file}</code> : <span className="muted">built-in default; written to the project file when you change it</span>}</dd>
          </dl>
          {o.risk !== 'normal' && <p className="help-warn">Policy setting: only a human can change it{o.risk === 'risky' ? ', with confirmation' : ''}. Agents are refused.</p>}
          {onDoc && <a href="#" onClick={(e) => { e.preventDefault(); onDoc(o.doc || 'docs/spec/10-configuration.md'); }}>Read more</a>}
        </div>
      )}
    </span>
  );
}

export function fmt(v: any): string {
  if (v === null || v === undefined) return 'none';
  if (v === true) return 'on';
  if (v === false) return 'off';
  if (Array.isArray(v)) return v.length ? v.join(', ') : 'none';
  return String(v);
}
