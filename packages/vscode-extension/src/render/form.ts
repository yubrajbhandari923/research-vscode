/** Schema-driven forms (create/edit entities). The client script (media/ui.js) collects values. */
import { esc, fmtVal } from '../util';
import { ic } from './ui';

export type FieldType = 'text' | 'textarea' | 'select' | 'seg' | 'entity' | 'entities' | 'kv' | 'list' | 'number' | 'check' | 'hidden';

export interface Field {
  name: string;
  label?: string;
  type: FieldType;
  required?: boolean;
  hint?: string;
  placeholder?: string;
  options?: [string, string, string?][]; // value, label, color
  filter?: string[]; // entity types for pickers
  rows?: number;
  mono?: boolean;
  value?: any;
  base?: Record<string, any>; // for kv: original values (shows delta)
  cls?: string;
  autofocus?: boolean;
}

export interface FormGroup {
  title?: string;
  icon?: string;
  fields: (Field | Field[])[];
  cls?: string;
}

export interface FormSpec {
  kind: string;
  title: string;
  subtitle?: string;
  icon?: string;
  submit: string;
  groups: FormGroup[];
  context?: any; // echoed back on submit
}

function fieldHtml(f: Field): string {
  const id = `f_${f.name}`;
  const lbl = f.label && f.type !== 'check'
    ? `<label class="lbl" for="${id}">${esc(f.label)}${f.required ? '<span class="req">*</span>' : ''}${f.hint ? `<span class="hint">${esc(f.hint)}</span>` : ''}</label>`
    : '';
  const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : '';
  const af = f.autofocus ? ' autofocus' : '';
  const req = f.required ? ' data-required="1"' : '';
  let input = '';
  switch (f.type) {
    case 'hidden':
      return `<input type="hidden" data-field="${esc(f.name)}" data-type="text" value="${esc(f.value ?? '')}">`;
    case 'text':
      input = `<input type="text" id="${id}" data-field="${esc(f.name)}" data-type="text" value="${esc(f.value ?? '')}"${ph}${af}${req} class="${f.mono ? 'mono' : ''}">`;
      break;
    case 'number':
      input = `<input type="number" id="${id}" data-field="${esc(f.name)}" data-type="number" value="${esc(f.value ?? '')}"${ph}${req}>`;
      break;
    case 'textarea':
      input = `<textarea id="${id}" data-field="${esc(f.name)}" data-type="text" rows="${f.rows || 3}"${ph}${af}${req} class="${f.mono ? 'mono' : ''}" data-autosize>${esc(f.value ?? '')}</textarea>`;
      break;
    case 'list':
      input = `<input type="text" id="${id}" data-field="${esc(f.name)}" data-type="list" value="${esc((f.value || []).join(', '))}"${ph}>`;
      break;
    case 'select':
      input = `<select id="${id}" data-field="${esc(f.name)}" data-type="text">${(f.options || [])
        .map(([v, l]) => `<option value="${esc(v)}"${v === (f.value ?? '') ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
      break;
    case 'seg':
      input = `<div class="seg" data-field="${esc(f.name)}" data-type="seg" data-value="${esc(f.value ?? '')}">${(f.options || [])
        .map(([v, l, c]) => `<button type="button" data-v="${esc(v)}" class="${v === (f.value ?? '') ? 'on' : ''}" style="${c ? `--c:var(--${c})` : ''}">${esc(l)}</button>`).join('')}</div>`;
      break;
    case 'check':
      input = `<label class="check"><input type="checkbox" data-field="${esc(f.name)}" data-type="check"${f.value ? ' checked' : ''}> ${esc(f.label || '')}${f.hint ? ` <span class="muted small">${esc(f.hint)}</span>` : ''}</label>`;
      break;
    case 'entity':
    case 'entities':
      input = `<div class="picker" data-field="${esc(f.name)}" data-type="${f.type}" data-filter="${esc((f.filter || []).join(','))}" data-value="${esc(JSON.stringify(f.type === 'entity' ? (f.value ? [f.value] : []) : f.value || []))}">
        <div class="chips"></div><input type="text" id="${id}" autocomplete="off" placeholder="${esc(f.placeholder || 'Type an ID or search…')}"><div class="dd hidden"></div></div>`;
      break;
    case 'kv': {
      const v = f.value || {};
      const base = f.base || {};
      const rows = Object.keys(v).map((k) => kvRow(k, v[k], base[k], k in base)).join('');
      input = `<div class="kv" data-field="${esc(f.name)}" data-type="kv" data-base="${esc(JSON.stringify(base))}">${rows}
        <button type="button" class="ghost" data-kv-add>${ic('add')}Add parameter</button>
        <div class="muted small" style="margin-top:6px">Values are parsed: <code>0.1</code> → number, <code>true</code> → bool, <code>[0.1, 0.5]</code> → list, otherwise text.</div></div>`;
      break;
    }
  }
  return `<div class="f ${f.cls || ''}">${lbl}${input}</div>`;
}

export function kvRow(k: string, v: any, base?: any, hasBase = false): string {
  const vs = typeof v === 'string' ? v : JSON.stringify(v);
  const changed = hasBase && JSON.stringify(base) !== JSON.stringify(v);
  return `<div class="kvrow${changed ? ' changed' : ''}"><input type="text" class="mono" data-k value="${esc(k)}" placeholder="name"><input type="text" class="mono" data-v value="${esc(vs ?? '')}" placeholder="value"><button type="button" class="icon" data-kv-del title="Remove">${ic('close')}</button>${changed ? `<div class="was">was ${esc(fmtVal(base))}</div>` : ''}</div>`;
}

export function renderForm(spec: FormSpec): string {
  const groups = spec.groups
    .map((g) => {
      const body = g.fields.map((f) => (Array.isArray(f) ? `<div class="frow">${f.map(fieldHtml).join('')}</div>` : fieldHtml(f))).join('');
      return `<div class="fgroup ${g.cls || ''}">${g.title ? `<div class="gtitle">${g.icon ? ic(g.icon) : ''}${esc(g.title)}</div>` : ''}${body}</div>`;
    })
    .join('');
  return `<div class="page narrow fade-in"><form id="form" data-kind="${esc(spec.kind)}" data-context="${esc(JSON.stringify(spec.context ?? null))}" autocomplete="off">
    <div class="form-hd"><div class="eyebrow">${spec.icon ? ic(spec.icon) : ''} ${esc(spec.kind)}</div><h1>${esc(spec.title)}</h1>${spec.subtitle ? `<p>${spec.subtitle}</p>` : ''}</div>
    ${groups}
    <div class="form-actions"><button type="submit" class="primary">${ic('check')}${esc(spec.submit)}</button><button type="button" class="ghost" data-cancel>Cancel</button>
      <span class="muted small"><span class="kbd">${process.platform === 'darwin' ? '⌘' : 'Ctrl'}</span>+<span class="kbd">Enter</span> to save</span><span class="err grow"></span></div>
  </form></div>`;
}
