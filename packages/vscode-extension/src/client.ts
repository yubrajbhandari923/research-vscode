/**
 * JSON-RPC client for the bundled Python core (`python -m research.rpc`).
 *
 * The process is spawned by the extension host. Because the extension declares
 * extensionKind=workspace, under Remote SSH this happens on the remote machine, next to the
 * project files, git, SQLite and SLURM.
 */
import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';

export class RpcError extends Error {
  constructor(message: string, public code?: number, public hint?: string, public kind?: string) {
    super(message);
  }
}

interface Pending {
  resolve: (v: any) => void;
  reject: (e: any) => void;
  timer: NodeJS.Timeout;
  method: string;
}

export class CoreClient implements vscode.Disposable {
  private proc?: cp.ChildProcessWithoutNullStreams;
  private buf = '';
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private ready?: Promise<any>;
  private restarts = 0;
  public python?: string;
  public readyInfo: any;

  constructor(
    public readonly root: string,
    private readonly pythonPath: string,
    private readonly out: vscode.OutputChannel,
  ) {}

  static corePath(ext: vscode.ExtensionContext): string {
    // Packaged VSIX: <ext>/python ; dev checkout: <repo>/packages/core + packages/cli
    const bundled = path.join(ext.extensionPath, 'python');
    if (fs.existsSync(path.join(bundled, 'research', '__init__.py'))) return bundled;
    const repo = path.resolve(ext.extensionPath, '..');
    return [path.join(repo, 'core'), path.join(repo, 'cli')].join(path.delimiter);
  }

  private start(): Promise<any> {
    if (this.ready) return this.ready;
    this.ready = new Promise((resolve, reject) => {
      const python = this.pythonPath;
      this.python = python;
      const env = { ...process.env, PYTHONPATH: [CoreClient.pythonPathEnv, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter), PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' };
      // UI actions are the human's, even when VS Code itself was launched from an agent's shell.
      for (const k of ['CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED',
        'RESEARCH_AGENT', 'RESEARCH_AGENT_ROLE', 'RESEARCH_AGENT_MODEL', 'RESEARCH_AUTHOR_TYPE']) delete (env as any)[k];
      this.out.appendLine(`[core] starting ${python} -m research.rpc --root ${this.root}`);
      let proc: cp.ChildProcessWithoutNullStreams;
      try {
        proc = cp.spawn(python, ['-m', 'research.rpc', '--root', this.root], { cwd: this.root, env });
      } catch (e: any) {
        this.ready = undefined;
        return reject(new RpcError(`Could not start Python (${python}): ${e.message}`));
      }
      this.proc = proc;
      let started = false;
      const failStart = (msg: string) => {
        if (!started) {
          started = true;
          this.ready = undefined;
          reject(new RpcError(msg));
        }
      };
      proc.on('error', (e) => failStart(`Could not start Python (${python}): ${e.message}. Set "research.pythonPath".`));
      proc.stderr.on('data', (d) => this.out.append(`[core stderr] ${d}`));
      proc.stdout.on('data', (d: Buffer) => {
        this.buf += d.toString('utf8');
        let i: number;
        while ((i = this.buf.indexOf('\n')) >= 0) {
          const line = this.buf.slice(0, i).trim();
          this.buf = this.buf.slice(i + 1);
          if (!line) continue;
          let msg: any;
          try {
            msg = JSON.parse(line);
          } catch {
            this.out.appendLine(`[core] non-JSON output: ${line}`);
            continue;
          }
          if (msg.event === 'ready') {
            started = true;
            this.restarts = 0;
            this.readyInfo = msg;
            this.out.appendLine(`[core] ready v${msg.version} (initialized=${msg.initialized})`);
            resolve(msg);
            continue;
          }
          const p = this.pending.get(msg.id);
          if (!p) continue;
          this.pending.delete(msg.id);
          clearTimeout(p.timer);
          if (msg.error) {
            const e = msg.error;
            if (e.trace) this.out.appendLine(`[core] ${p.method} failed:\n${e.trace}`);
            p.reject(new RpcError(e.message, e.code, e.hint, e.kind));
          } else p.resolve(msg.result);
        }
      });
      proc.on('exit', (code, sig) => {
        this.out.appendLine(`[core] exited (code=${code}, signal=${sig})`);
        failStart(`Research core exited during start-up (code ${code}). Is ${python} Python ≥ 3.8? See Output → Research.`);
        for (const [, p] of this.pending) {
          clearTimeout(p.timer);
          p.reject(new RpcError('Research core process exited'));
        }
        this.pending.clear();
        this.proc = undefined;
        this.ready = undefined;
      });
    });
    return this.ready;
  }

  static pythonPathEnv = '';

  async request<T = any>(method: string, params: any = {}, timeoutMs = 120_000): Promise<T> {
    try {
      await this.start();
    } catch (e) {
      if (this.restarts++ < 2) {
        await new Promise((r) => setTimeout(r, 300));
        await this.start();
      } else throw e;
    }
    const proc = this.proc;
    if (!proc) throw new RpcError('Research core not running');
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new RpcError(`Research core timed out on ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      proc.stdin.write(JSON.stringify({ id, method, params }) + '\n');
    });
  }

  restart() {
    this.proc?.kill();
    this.proc = undefined;
    this.ready = undefined;
  }

  dispose() {
    try {
      this.proc?.stdin.end();
      this.proc?.kill();
    } catch {
      /* ignore */
    }
  }
}

/** Find a usable Python ≥ 3.8 on the extension-host machine. */
export function detectPython(configured: string, out: vscode.OutputChannel): string {
  const candidates = configured ? [configured] : process.platform === 'win32' ? ['py', 'python', 'python3'] : ['python3', 'python'];
  for (const c of candidates) {
    try {
      const r = cp.spawnSync(c, ['-c', 'import sys;print("%d.%d"%sys.version_info[:2]);print(sys.executable)'], { encoding: 'utf8', timeout: 8000 });
      if (r.status === 0) {
        const [ver, exe] = r.stdout.trim().split(/\r?\n/);
        const [maj, min] = ver.split('.').map(Number);
        if (maj === 3 && min >= 8) {
          out.appendLine(`[core] using Python ${ver} at ${exe}`);
          return exe || c;
        }
        out.appendLine(`[core] ${c} is Python ${ver} (< 3.8), skipping`);
      }
    } catch {
      /* try next */
    }
  }
  return configured || 'python3';
}
