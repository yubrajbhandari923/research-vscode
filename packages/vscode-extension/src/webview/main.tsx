import { createRoot } from 'react-dom/client';
import { App } from './App';
import { vscodeHost } from './host';

declare const acquireVsCodeApi: () => any;
const initial = (window as any).__RESEARCH_INITIAL__ || {};
createRoot(document.getElementById('root')!).render(<App host={vscodeHost(acquireVsCodeApi())} initial={initial} />);
