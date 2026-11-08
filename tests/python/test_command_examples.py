"""Every `research …` example shipped in docs, templates and generated text must parse with the real CLI parser."""
import glob
import os
import re
import shlex

import pytest

from research_cli.main import build_parser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILES = [os.path.join(ROOT, 'README.md')] + [f for f in glob.glob(os.path.join(ROOT, 'docs/*.md')) if not re.search(r'PHASE_|_SUMMARY|REVIEW|AUDIT|UPGRADING', f)] + glob.glob(os.path.join(ROOT, 'packages/core/research/**/*.md'), recursive=True) \
    + glob.glob(os.path.join(ROOT, 'packages/core/research/*.py'))
EXAMPLE = re.compile(r'`(research (?:[a-z][a-z-]*)(?: [^`\n]*)?)`')
FENCE = re.compile(r'^```[^\n]*\n(.*?)^```', re.S | re.M)
FENCED_CMD = re.compile(r'^\s*(?:\$ )?(research [a-z][a-z-]*(?: .*)?)$', re.M)


def _commands(src):
    yield from (m.group(1) for m in EXAMPLE.finditer(src))
    for block in FENCE.finditer(src):  # fenced blocks: one command per line, `\` continues it
        yield from (m.group(1).rstrip('\\ ') for m in FENCED_CMD.finditer(block.group(1).replace('\\\n', ' ')))


def examples():
    seen = set()
    for f in FILES:
        for cmd in _commands(open(f, encoding='utf8').read()):
            cmd = re.sub(r'\s+#.*$| / .*$| [>|] .*$', '', cmd)  # trailing comment, prose alternatives
            cmd = re.sub(r'([\w-]+)(?:\\?[|/][\w-]+)+', r'\1', cmd)  # `a|b|c` -> `a`
            cmd = re.split(r' \(?[A-Z]+\)?(?: |$)', cmd)[0]  # ALLCAPS placeholder (ID, NAME, N): the rest is elided
            text = re.sub(r'\{[^}]*\}', 'X', cmd)
            text = text.split(' | ')[0].split('…')[0].split('<')[0].split('[')[0].replace('\\"', '"')
            if text not in seen and len(text.split()) > 1:
                seen.add(text)
                yield os.path.relpath(f, ROOT), text.strip()


@pytest.fixture(scope='module')
def parser():
    return build_parser()


@pytest.mark.parametrize('where,text', sorted(examples()))
def test_example_parses(where, text, capsys, parser):
    try:
        argv = shlex.split(text.replace('pass|fail', 'pass'))[1:]
        argv = argv[:argv.index('--')] if '--' in argv else argv  # main() splits the trailing command off first
    except ValueError:
        pytest.skip('contains prose quoting')
    # Placeholders were elided, so a missing required argument is fine; a bad subcommand, choice or flag is not.
    try:
        parser.parse_args(argv)
    except SystemExit as e:
        if e.code == 0:  # --help
            return
        err = capsys.readouterr().err
        assert 'arguments are required' in err or 'expected one argument' in err, f'{where}: `{text}`\n{err}'
