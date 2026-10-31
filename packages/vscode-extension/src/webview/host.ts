// The only thing the UI knows about where it runs. VS Code today; a browser/HTTP host can implement the same interface later.
export interface Host {
  /** Call a core JSON-RPC method. Rejects with Error(message) on failure. */
  call<T = any>(method: string, params?: Record<string, any>): Promise<T>;
  /** Open a record (Q-, EXP-, RUN-, F-, D-, T-, …) in the host's detail view. */
  openEntity(id: string): void;
  /** Open a configuration file in the editor (project-relative path, or the machine config path the core reported). */
  openFile(path: string): void;
  /** Open documentation (a repo-relative path with optional #anchor). */
  openDoc(path: string): void;
  /** Persist small UI state (selected page, search text). */
  getState(): any;
  setState(s: any): void;
}

export function vscodeHost(api: { postMessage(m: any): void; getState(): any; setState(s: any): void }): Host {
  let seq = 0;
  const waiting = new Map<number, { ok: (v: any) => void; err: (e: Error) => void }>();
  window.addEventListener('message', (ev) => {
    const m = ev.data;
    if (!m || m.type !== 'rpc-result') return;
    const w = waiting.get(m.id);
    if (!w) return;
    waiting.delete(m.id);
    m.error ? w.err(new Error(m.error)) : w.ok(m.result);
  });
  return {
    call: (method, params) => new Promise((ok, err) => {
      const id = ++seq;
      waiting.set(id, { ok, err });
      api.postMessage({ type: 'rpc', id, method, params: params || {} });
    }),
    openEntity: (id) => api.postMessage({ type: 'open-entity', id }),
    openDoc: (path) => api.postMessage({ type: 'open-doc', path }),
    openFile: (path) => api.postMessage({ type: 'open-file', path }),
    getState: () => api.getState() || {},
    setState: (s) => api.setState(s),
  };
}
