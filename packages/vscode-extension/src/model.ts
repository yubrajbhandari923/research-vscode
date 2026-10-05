import * as vscode from 'vscode';
import { CoreClient } from './client';

export interface Tree {
  project: { name: string; goal?: string };
  questions: any[];
  experiments: any[];
  findings: any[];
  decisions: any[];
  checkpoints: any[];
  notes: any[];
  artifacts: any[];
  skills: any[];
  agent: { context: any[]; prompts: any[]; templates: any[] };
  needs_synthesis: string[];
}

/** Cached project state. One refresh fetches everything the sidebar needs. */
export class Model implements vscode.Disposable {
  tree?: Tree;
  resume?: any;
  index?: { items: any[]; statuses: Record<string, string[]> };
  initialized = false;
  error?: string;
  private _onChange = new vscode.EventEmitter<void>();
  readonly onChange = this._onChange.event;
  private timer?: NodeJS.Timeout;
  private inflight?: Promise<void>;
  private again = false;
  private poll?: NodeJS.Timeout;

  constructor(public client: CoreClient, private out: vscode.OutputChannel) {}

  get root() {
    return this.client.root;
  }

  /** Debounced refresh (file watcher, after mutations). */
  schedule(ms = 250) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.refresh(), ms);
  }

  async refresh(): Promise<void> {
    if (this.inflight) {
      this.again = true;
      return this.inflight;
    }
    this.inflight = (async () => {
      try {
        const info = await this.client.request('ping');
        this.initialized = !!info.initialized;
        await vscode.commands.executeCommand('setContext', 'research.ready', true);
        await vscode.commands.executeCommand('setContext', 'research.initialized', this.initialized);
        if (this.initialized) {
          const [tree, resume, index] = await Promise.all([
            this.client.request<Tree>('tree'),
            this.client.request('resume'),
            this.client.request('index'),
          ]);
          this.tree = tree;
          this.resume = resume;
          this.index = index;
        }
        this.error = undefined;
      } catch (e: any) {
        this.error = e.message;
        this.out.appendLine(`[refresh] ${e.message}`);
        await vscode.commands.executeCommand('setContext', 'research.ready', true);
      } finally {
        this.inflight = undefined;
        this._onChange.fire();
        this.updatePolling();
        if (this.again) {
          this.again = false;
          this.schedule(50);
        }
      }
    })();
    return this.inflight;
  }

  /** While runs are active, periodically reconcile their status (local pids, SLURM queue). */
  private updatePolling() {
    const active = (this.resume?.active_runs || []).length > 0;
    if (active && !this.poll) {
      const secs = Math.max(3, vscode.workspace.getConfiguration('research').get<number>('pollIntervalSeconds', 10));
      this.poll = setInterval(async () => {
        try {
          await this.client.request('run_refresh', {});
          this.schedule(10);
        } catch {
          /* ignore */
        }
      }, secs * 1000);
    } else if (!active && this.poll) {
      clearInterval(this.poll);
      this.poll = undefined;
    }
  }

  find(id: string): any | undefined {
    return this.index?.items.find((i) => i.id === id);
  }

  experiment(id: string) {
    return this.tree?.experiments.find((e) => e.id === id);
  }

  dispose() {
    if (this.poll) clearInterval(this.poll);
    if (this.timer) clearTimeout(this.timer);
  }
}
