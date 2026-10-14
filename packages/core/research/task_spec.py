"""One compatibility projection of task intent, execution boundaries and checks.

Neither legacy representation is rewritten. Conflicting declarations are retained
with their sources; callers must reconcile them before execution or completion.
"""
from .util import PolicyBlocked


def effective(p, tid):
    from . import hooks, orchestrate
    task = p.get('task', tid)
    led = orchestrate.ledger(p, tid)
    contract = led.get('contract') or {}
    values, sources, conflicts = {}, {}, []
    for key, legacy, structured in (
        ('objective', 'goal', 'objective'), ('inputs', 'inputs', 'context'),
        ('expected_outputs', 'expected_outputs', 'expected_outputs'),
        ('verification', 'verification', 'verification'),
    ):
        declarations = []
        for source, value in (('task.' + legacy, task.get(legacy)),
                              ('contract.' + structured, contract.get(structured))):
            if value:
                declarations.append({'source': source, 'value': value})
        sources[key] = declarations
        def normalized(value):
            return [str(x).strip() for x in (value if isinstance(value, list) else [value])]
        if len(declarations) == 2 and normalized(declarations[0]['value']) != normalized(declarations[1]['value']):
            conflicts.append({'field': key, 'declarations': declarations})
        values[key] = declarations[0]['value'] if declarations else None
    values.update(acceptance_criteria=task.get('acceptance_criteria'),
                  constraints=contract.get('constraints') or [],
                  allowed_files=contract.get('allowed_files') or [],
                  checks=task.get('checks') or [])
    for key in ('acceptance_criteria', 'checks', 'constraints', 'allowed_files'):
        sources[key] = [{'source': ('contract.' if key in ('constraints', 'allowed_files') else 'task.') + key,
                         'value': values[key]}]
    return {'task': tid, **values, 'sources': sources, 'conflicts': conflicts,
            'frozen_tests': led['frozen'], 'task_override': led.get('override'),
            'hooks': [h for h in hooks.listing(p) if h['stage'] in ('preflight', 'post_implementation')],
            'policy': {'verification': p.config.get('verification') or {},
                       'agent_policy': p.policy},
            'scientific_review': 'Findings require separate independent review; checks do not establish scientific support.'}


def assert_consistent(spec):
    if spec['conflicts']:
        raise PolicyBlocked('Conflicting task specification: ' + ', '.join(c['field'] for c in spec['conflicts']),
                            hint='Inspect task context and reconcile the task and contract declarations explicitly.')


def render(spec):
    lines = ['### Effective task specification']
    for key in ('objective', 'inputs', 'expected_outputs', 'acceptance_criteria', 'constraints',
                'allowed_files', 'verification', 'checks', 'frozen_tests'):
        value = spec.get(key)
        if value:
            lines.append('- **' + key.replace('_', ' ').capitalize() + ':** ' + str(value))
    for conflict in spec['conflicts']:
        lines.append('- **CONFLICT ' + conflict['field'] + ':** ' + str(conflict['declarations']))
    for hook in spec['hooks']:
        lines.append('- Hook ' + hook['name'] + ' (' + hook['stage'] + '): ' + hook['command'] +
                     ('; required' if hook['required'] else '; advisory') +
                     ('; disabled' if not hook['enabled'] else '; approved' if hook['approved'] else '; HUMAN APPROVAL REQUIRED'))
    lines.append('- ' + spec['scientific_review'])
    lines.append('- Sources: task record and orchestration contract; conflicting values above are unresolved, not overrides.')
    return '\n'.join(lines)
