// Renders the real Control Center outside VS Code with data captured from the real core (scripts/make_preview_fixture.py).
import { createRoot } from 'react-dom/client';
import { App } from '../src/webview/App';

declare const __FIXTURE__: any;
declare const __SCENARIO__: { initial?: any; steps?: any[] };

const F = __FIXTURE__;
let state: any = {};
const host = {
  call: async (method: string, p: any = {}) => {
    if (method === 'control') return F[p.page];
    if (method === 'settings') return F.settings.all;
    if (method === 'settings_preview') return { ...F.preview, errors: {} };
    if (method === 'workspace_archive') return F.archive_impact;
    if (method === 'promotion_diff') return F.promotion_diff;
    return {};
  },
  openEntity() {}, openDoc() {}, getState: () => state, setState: (s: any) => (state = s),
};
createRoot(document.getElementById('root')!).render(<App host={host as any} initial={__SCENARIO__.initial} />);

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const find = (sel: string, text?: string) => Array.from(document.querySelectorAll<HTMLElement>(sel)).find((e) => !text || e.textContent!.trim().startsWith(text) || e.getAttribute('aria-label') === text);
(async () => {
  await wait(500);
  for (const s of __SCENARIO__.steps || []) {
    if (s.hover) find('button.help-btn', s.hover)?.parentElement?.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    if (s.click) find('button', s.click)?.click();
    if (s.type) {
      const row = Array.from(document.querySelectorAll('.setting')).find((r) => r.querySelector('.setting-label')!.textContent === s.type[0]);
      const input = row?.querySelector('input') as HTMLInputElement;
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      set.call(input, s.type[1]);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (s.search) {
      const input = document.getElementById('setting-search') as HTMLInputElement;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, s.search);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    await wait(350);
  }
  document.title = 'ready';
})();
