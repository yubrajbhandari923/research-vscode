// Copies the Python core + CLI into the extension so the VSIX is self-contained.
import fs from 'fs';
import path from 'path';

const here = path.dirname(new URL(import.meta.url).pathname);
const ext = path.resolve(here, '..');
const repo = path.resolve(ext, '..', '..');
const dest = path.join(ext, 'python');
fs.rmSync(dest, { recursive: true, force: true });
const copy = (from, to) => fs.cpSync(from, to, { recursive: true, filter: (s) => !/__pycache__|\.pyc$/.test(s) });
copy(path.join(repo, 'packages', 'core', 'research'), path.join(dest, 'research'));
copy(path.join(repo, 'packages', 'cli', 'research_cli'), path.join(dest, 'research_cli'));
console.log('bundled python core →', path.relative(repo, dest));
