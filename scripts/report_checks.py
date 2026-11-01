"""Separate credential-bearing reporter. Never imports or runs candidate code."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factory_eval.github import GitHub
from factory_eval.task import Task
from factory_eval.util import Invalid, read_json, write_json

root = Path(__file__).resolve().parents[1]
evidence = read_json(root / 'out' / 'grading.json')
api = GitHub(os.environ['CHECKS_TOKEN'])
for k, v in {'repository': os.environ['WORK_REPOSITORY'], 'commit': os.environ['HEAD_SHA'],
             'definition_sha': os.environ['DEFINITION_SHA'], 'run_id': os.environ['RUN_ID'],
             'workflow_run_id': int(os.environ['GITHUB_RUN_ID'])}.items():
    if evidence.get(k) != v:
        raise Invalid('grading artifact identity mismatch: ' + k)
if evidence['repository'] not in json.loads(os.environ['WORK_REPOSITORIES']):
    raise Invalid('report target outside allowlist')
evidence['issuer'] = int(os.environ['ISSUER_ID'])
task = Task.load(root / 'tasks' / os.environ['TASK_ID'])
if {j['name'] for j in evidence['jobs']} != set(task.data['required_jobs']):
    raise Invalid('job manifest mismatch')
for job in evidence['jobs']:
    raw = evidence['failure_logs'].get(job['name'], '')
    # Render JSON Go output into compact full test output; preserve every failure.
    lines = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
            if 'Output' in row:
                lines.append(row['Output'].rstrip())
        except ValueError:
            lines.append(line)
    log = '\n'.join(lines)
    if len(log.encode()) > 3800:
        log = '[INCOMPLETE: full failure evidence is in the trusted grading artifact]\n' + log[:3000]
    created = api.request('POST', f"/repos/{evidence['repository']}/check-runs", {
        'name': 'factory-eval/' + job['name'], 'head_sha': evidence['commit'],
        'external_id': evidence['run_id'], 'details_url': os.environ['WORKFLOW_URL'],
        'status': 'completed', 'conclusion': 'success' if job['status'] == 'passed' else 'failure',
        'output': {'title': job['name'] + ': ' + job['status'],
                   'summary': 'Trusted AI Factory evaluation. Run: ' + evidence['run_id'], 'text': log or 'Executed successfully.'}})
    if created.get('app', {}).get('id') != evidence['issuer']:
        raise Invalid('check issuer does not match the configured GitHub App')
write_json(root / 'out' / 'grading.json', evidence, (os.environ['CHECKS_TOKEN'],))
