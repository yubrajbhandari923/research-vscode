import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from '../src/webview/App';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

export async function mount(host: any, initial?: any) {
  const el = document.createElement('div');
  document.body.appendChild(el);
  const root = createRoot(el);
  await act(async () => { root.render(<App host={host} initial={initial} />); });
  return { el, unmount: () => act(async () => root.unmount()) };
}
export const tick = async (ms = 0) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
export { act };
