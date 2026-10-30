export type Page = 'home' | 'work' | 'research' | 'settings';

export interface SettingOption {
  key: string; type: 'bool' | 'int' | 'float' | 'str' | 'path' | 'enum' | 'list';
  category: string; label: string; help: string; why: string; effect: string;
  risk: 'normal' | 'policy' | 'risky'; choices: string[] | null; min: number | null; max: number | null;
  nullable: boolean; affects: string[]; scopes: string[]; restart: boolean; depends_on: string | null;
  doc: string | null; unit: string | null; approval: boolean;
  value: any; default: any; source: 'default' | 'machine' | 'project'; inherited: boolean;
  overridden_in: string[]; errors: string[];
}
export interface SettingsPayload {
  categories: { id: string; label: string }[]; options: SettingOption[];
  machine_config: string | null; project_config: string; fingerprint: string;
}
export interface Preview {
  ok: boolean; errors: Record<string, string>; values: Record<string, any>; scope: string; path: string; diff: string;
  changed: boolean; needs_confirmation: boolean; policy_keys: string[]; risky_keys: string[]; affects: string[];
  restart: boolean; blocked_for_agents?: string[];
}
export interface Attention { kind: string; id: string; title: string; detail: string; severity: 'info' | 'warn'; open: { page: Page; target: string } }
