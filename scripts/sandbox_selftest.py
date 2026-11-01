"""Credential-free integration smoke: only reviewed reference fixtures are run."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from factory_eval.grader import prepare, execute, scope, reference_trace, container_runner
from factory_eval.task import Task

root=Path(__file__).resolve().parents[1]
for tid in ('E01','E03','E07'):
    task=Task.load(root/'tasks'/tid)
    with tempfile.TemporaryDirectory(prefix='factory-sandbox-selftest-') as temp:
        staged=prepare(task,task.root/'reference',Path(temp)/'staged')
        result=execute(task,staged,scope_valid=scope(task,task.root/'reference'),
            trace=reference_trace(task) if tid=='E03' else None,
            runner=container_runner('factory-grading:local',task,staged))
        if not result['scope_valid'] or not all(j['executed'] and j['status']=='passed' for j in result['jobs']):
            raise RuntimeError(f'{tid}: sandbox reference grading failed: {result}')
        print(f'{tid} sandbox reference: passed',flush=True)
