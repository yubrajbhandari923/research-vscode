"""Every `research …` example shipped in docs, templates and generated text must parse with the real CLI parser."""
import glob
import os
import re
import shlex

import pytest

from research_cli.main import build_parser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILES = [os.path.join(ROOT, 'README.md')] + glob.glob(os.path.join(ROOT, 'packages/core/research/**/*.md'), recursive=True) \
    + glob.glob(os.path.join(ROOT, 'packages/core/research/*.py'))
EXAMPLE = re.compile(r'`(research (?:[a-z][a-z-]*)(?: [^`\n]*)?)`')


def examples():
    seen = set()
    for f in FILES:
        for m in EXAMPLE.finditer(open(f, encoding='utf8').read()):
            text = re.sub(r'\{[^}]*\}', 'X', m.group(1))
            text = text.split(' | ')[0].split('…')[0].split('<')[0].split('[')[0].replace('\\"', '"')
            if text not in seen and len(text.split()) > 1:
                seen.add(text)
                yield os.path.relpath(f, ROOT), text.strip()


@pytest.mark.parametrize('where,text', sorted(examples()))
def test_example_parses(where, text, capsys):
    try:
        argv = shlex.split(text.replace('pass|fail', 'pass'))[1:]
        argv = argv[:argv.index('--')] if '--' in argv else argv  # main() splits the trailing command off first
    except ValueError:
        pytest.skip('contains prose quoting')
    # Placeholders were elided, so a missing required argument is fine; a bad subcommand, choice or flag is not.
    try:
        build_parser().parse_args(argv)
    except SystemExit as e:
        if e.code == 0:  # --help
            return
        err = capsys.readouterr().err
        assert 'arguments are required' in err or 'expected one argument' in err, f'{where}: `{text}`\n{err}'
