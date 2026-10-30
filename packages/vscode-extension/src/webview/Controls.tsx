import { useId } from 'react';
import { Help, fmt } from './Help';
import type { SettingOption } from './types';

export function SettingRow({ option, draft, error, onChange, onReset, onDoc, disabledReason }: {
  option: SettingOption; draft: any; error?: string; onChange: (v: any) => void; onReset: () => void; onDoc: (p: string) => void; disabledReason?: string;
}) {
  const id = useId();
  const o = option;
  const changed = draft !== undefined;
  const shown = changed ? draft : o.value;
  const dis = !!disabledReason;
  const hint = `${id}-hint`;
  return (
    <div className={'setting' + (changed ? ' changed' : '')} role="group" aria-labelledby={`${id}-l`}>
      <div className="setting-head">
        <label id={`${id}-l`} htmlFor={id} className="setting-label">{o.label}</label>
        <Help option={o} onDoc={onDoc} />
        {o.risk !== 'normal' && <span className="badge warn" title="Only a human can change this">policy</span>}
        <span className={'badge src-' + o.source}>{o.source}{o.inherited && o.source !== 'default' ? ' · inherited' : ''}</span>
        {(o.source !== 'default') && !changed && <button type="button" className="link" onClick={onReset} aria-label={`Reset ${o.label} to ${fmt(o.default)}`}>Reset</button>}
        {changed && <span className="badge pending">unsaved</span>}
      </div>
      <div className="setting-body">
        <Control id={id} option={o} value={shown} onChange={onChange} describedBy={hint} disabled={dis} />
        <span className="muted small" id={hint}>
          {dis ? disabledReason : <>Default {fmt(o.default)}{o.unit ? ` ${o.unit}` : ''}{o.source !== 'default' ? ` · was ${fmt(o.value)}` : ''}</>}
        </span>
      </div>
      {(error || o.errors.length > 0) && <div className="field-error" role="alert">{error || o.errors[0]}</div>}
    </div>
  );
}

function Control({ id, option: o, value, onChange, describedBy, disabled }: { id: string; option: SettingOption; value: any; onChange: (v: any) => void; describedBy: string; disabled: boolean }) {
  if (o.type === 'bool')
    return <button id={id} type="button" role="switch" aria-checked={!!value} aria-describedby={describedBy} disabled={disabled}
      className={'switch' + (value ? ' on' : '')} onClick={() => onChange(!value)}><span className="knob" /><span className="sr">{value ? 'On' : 'Off'}</span></button>;
  if (o.type === 'enum')
    return <select id={id} value={value ?? ''} aria-describedby={describedBy} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {o.nullable && <option value="">none</option>}{(o.choices || []).map((c) => <option key={c} value={c}>{c}</option>)}</select>;
  if (o.type === 'int' || o.type === 'float')
    return <span className="numfield"><input id={id} type="number" inputMode="numeric" value={value ?? ''} min={o.min ?? undefined} max={o.max ?? undefined}
      aria-describedby={describedBy} disabled={disabled} onChange={(e) => onChange(e.target.value)} />{o.unit && <span className="muted small">{o.unit}</span>}</span>;
  if (o.type === 'list' && o.choices)
    return <fieldset id={id} className="checks" aria-describedby={describedBy} disabled={disabled}>{o.choices.map((c) => {
      const cur: string[] = Array.isArray(value) ? value : [];
      return <label key={c}><input type="checkbox" checked={cur.includes(c)} onChange={(e) => onChange(e.target.checked ? [...cur, c] : cur.filter((x) => x !== c))} /> {c}</label>;
    })}</fieldset>;
  if (o.type === 'list')
    return <input id={id} type="text" placeholder="comma-separated" value={Array.isArray(value) ? value.join(', ') : value ?? ''} aria-describedby={describedBy} disabled={disabled} onChange={(e) => onChange(e.target.value)} />;
  return <input id={id} type="text" value={value ?? ''} placeholder={o.nullable ? 'none' : ''} aria-describedby={describedBy} disabled={disabled} onChange={(e) => onChange(e.target.value)} />;
}
