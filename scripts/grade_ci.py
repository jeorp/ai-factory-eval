"""Trusted host entrypoint. No candidate code is executed on the host."""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factory_eval.grader import prepare, execute, scope, container_runner
from factory_eval.task import Task
from factory_eval.util import Invalid, repo, sha, write_json

root = Path(__file__).resolve().parents[1]
task = Task.load(root / 'tasks' / os.environ['TASK_ID'])
repository = repo(os.environ['WORK_REPOSITORY'])
allowed = json.loads(os.environ['WORK_REPOSITORIES'])
if repository not in allowed or repository in (os.environ['GITHUB_REPOSITORY'], 'jeorp/ai-factory'):
    raise Invalid('work repository outside dedicated allowlist')
head, base = sha(os.environ['HEAD_SHA']), sha(os.environ['BASE_SHA'])
definition = sha(os.environ['DEFINITION_SHA'])
if os.environ['GITHUB_SHA'] != definition:
    raise Invalid('workflow is not running at pinned definition SHA')
trace = json.loads(os.environ['TRACE'])
if len(json.dumps(trace)) > 30000:
    raise Invalid('trace too large')
candidate = Path(os.environ['CANDIDATE_PATH']).resolve()
# Independent task scope uses fixed fixture hashes; candidate tests are never grading sources.
valid = scope(task, candidate)
with tempfile.TemporaryDirectory(prefix='factory-grade-') as temp:
    staged = prepare(task, candidate, Path(temp) / 'staged')
    evidence = execute(task, staged, scope_valid=valid, trace=trace or None,
                       runner=container_runner('factory-grading:local', task, staged))
write_json(root / 'out' / 'grading.json', {**evidence, 'repository': repository, 'commit': head,
    'base_commit': base, 'definition_sha': definition, 'run_id': os.environ['RUN_ID'],
    'task_id': task.id, 'workflow_run_id': int(os.environ['GITHUB_RUN_ID'])})
