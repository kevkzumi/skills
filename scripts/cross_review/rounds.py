"""Persist the bounded review outcome independently of the author's completion action."""
import json
from pathlib import Path

from . import runner, scope


LIMIT = 3


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def state(directory):
    path = directory / 'rounds.json'
    return json.loads(path.read_text()) if path.exists() else {'history': [], 'resolved_findings': []}


def findings(reviews):
    return [dict(finding, axis=axis, index=index)
            for axis, report in reviews.items()
            for index, finding in enumerate(report.get('findings', []))]


def outcome(data, result):
    return dict(result, rounds_started=len(data['history']),
                rounds_completed=sum(item.get('completed', False) for item in data['history']),
                resolved_findings=data['resolved_findings'],
                remaining_findings=data.get('remaining_findings', findings(result.get('reviews', {}))),
                disagreements=[item for item in data.get('assessments', [])
                               if item['resolution'] == 'disagree'])


def assess(directory, assessments):
    data = state(directory)
    result = json.loads((directory / 'result.json').read_text())
    current = findings(result.get('reviews', {}))
    if not current or data.get('terminal'):
        raise ValueError('No findings are available for author assessment, or the workflow is blocked.')
    if not isinstance(assessments, list):
        raise ValueError('Assessments must be a list of axis, index, resolution, and explanation objects.')
    expected = {(item['axis'], item['index']) for item in current}
    actual = []
    for item in assessments:
        if (not isinstance(item, dict) or item.get('axis') not in ('standards', 'spec')
                or type(item.get('index')) is not int or item.get('resolution') not in ('fixed', 'disagree')
                or not isinstance(item.get('explanation'), str) or not item['explanation'].strip()):
            raise ValueError('Each finding needs a fixed/disagree assessment with an explanation.')
        actual.append((item['axis'], item['index']))
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError('Assess every current finding exactly once; none may be silently dismissed.')
    data['assessments'] = assessments
    if any(item['resolution'] == 'disagree' for item in assessments):
        data['terminal'] = 'Unresolved author-reviewer disagreement; present it to the user.'
    result.update(allowed=False, error=data.get('terminal', 'Author fixes need fresh external review.'))
    result = outcome(data, result)
    write(directory / 'rounds.json', data)
    write(directory / 'result.json', result)
    return result


def requirements_unchanged(policy):
    sources = list(policy['standards'])
    if policy.get('spec_source'):
        sources.append({'source': policy['spec_source'], 'contents': policy['spec']})
    try:
        return all(Path(source['source']).read_text() == source['contents'] for source in sources)
    except OSError:
        return False


def review(session, directory, spec, standards, timeout, skip_reason, spec_source=None):
    data = state(directory)
    previous_result = json.loads((directory / 'result.json').read_text()) if (directory / 'result.json').exists() else {}
    previous_reviews = previous_result.get('reviews', {})
    blocker = data.get('terminal')
    if len(data['history']) >= LIMIT:
        blocker = blocker or 'Three review rounds exhausted; present remaining work to the user.'
    if findings(previous_reviews) and not data.get('assessments'):
        blocker = blocker or 'Assess every finding and fix accepted issues before requesting another review.'
    if blocker:
        result = outcome(data, dict(previous_result, allowed=False, error=blocker))
        write(directory / 'result.json', result)
        return result
    # Fix the requirements and skip policy for this workflow, so a failed axis cannot be bypassed.
    policy = {'spec': spec, 'standards': standards, 'skip_reason': skip_reason, 'spec_source': spec_source}
    if data.get('policy', policy) != policy:
        result = outcome(data, dict(previous_result, allowed=False,
                                   error='Review requirements or Spec skip policy changed during this workflow.'))
        write(directory / 'result.json', result)
        return result
    data['policy'] = policy
    round_dir = directory / ('round-{}'.format(len(data['history']) + 1))
    round_dir.mkdir()
    data['history'].append({'artifacts': str(round_dir), 'completed': False})
    write(directory / 'rounds.json', data)
    write(directory / 'result.json', outcome(data, {'allowed': False, 'error': 'Review is incomplete.'}))
    previous = {}
    for axis, report in previous_reviews.items():
        previous[axis] = dict(report, author_assessments=[item for item in data.get('assessments', [])
                                                       if item['axis'] == axis])
    try:
        reviewed_scope = scope.snapshot(session, round_dir)
        reports = runner.run(session, reviewed_scope, round_dir, spec, standards, timeout,
                             skip_reason, previous=previous or None)
        completed = all(report.get('skipped') or report.get('complete') for report in reports.values())
        data['history'][-1].update(completed=completed, reviews=reports)
        # A failed axis cannot establish that its previous findings were resolved.
        outstanding = {axis: (report if report.get('complete') or report.get('skipped')
                              else previous_reviews.get(axis, report))
                       for axis, report in reports.items()}
        data['remaining_findings'] = findings(outstanding)
        allowed = completed and not findings(reports)
        result = {'allowed': bool(allowed), 'scope': reviewed_scope, 'reviews': reports,
                  'artifacts': str(round_dir)}
        if not completed:
            data['terminal'] = 'External review is incomplete; preserve the work and report the blocker.'
            result['error'] = data['terminal']
        elif not requirements_unchanged(policy):
            result.update(allowed=False, error='Review requirements changed during review; results are stale.')
        elif not scope.unchanged(session, reviewed_scope):
            result.update(allowed=False, error='Implementation changed during review; results are stale and fresh review is required.')
        else:
            for axis, previous_report in previous_reviews.items():
                for index, finding in enumerate(previous_report.get('findings', [])):
                    if finding not in reports.get(axis, {}).get('findings', []):
                        data['resolved_findings'].append(dict(finding, axis=axis, index=index))
        if findings(reports):
            result['error'] = ('Three review rounds exhausted; remaining findings block completion.'
                               if len(data['history']) == LIMIT else 'Author fixes and fresh review are required.')
        data.pop('assessments', None)
    except Exception as error:
        data['terminal'] = 'Review could not complete: {}'.format(error)
        result = dict(previous_result, allowed=False, error=data['terminal'], artifacts=str(round_dir))
    result = outcome(data, result)
    write(round_dir / 'result.json', result)
    write(directory / 'rounds.json', data)
    write(directory / 'result.json', result)
    return result


def gate(session, directory):
    data = state(directory)
    result = json.loads((directory / 'result.json').read_text())
    if data.get('terminal'):
        result.update(allowed=False, error=data['terminal'])
    elif result.get('allowed') and not requirements_unchanged(data['policy']):
        result.update(allowed=False, error='Review requirements changed after review; completion is blocked.')
    elif result.get('allowed') and not scope.unchanged(session, result['scope']):
        result.update(allowed=False, error='Implementation changed after review; fresh review required.')
    return outcome(data, result)
