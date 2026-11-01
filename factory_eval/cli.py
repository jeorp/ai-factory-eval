from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from .adapter import build_core
from .config import load_config
from .grader import self_test
from .orchestrator import Evaluation
from .report import report
from .task import Task
from .util import EvalError, read_json

ROOT = Path(__file__).resolve().parents[1]

def main(argv=None):
    p = argparse.ArgumentParser(description='Bounded real-Runner evaluation; no paid calls in self-test.')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('self-test', help='verify trusted initial fixtures, references and intentional mutations')
    validate = sub.add_parser('validate', help='validate task definitions and optional live config without network/model calls')
    validate.add_argument('--config')
    build = sub.add_parser('build-core', help='archive a fixed core SHA and build the headless command')
    build.add_argument('--source', required=True)
    build.add_argument('--sha', required=True)
    build.add_argument('--output', required=True)
    export = sub.add_parser('export-task', help='export only public initial code for a dedicated work repository')
    export.add_argument('task', choices=['E01','E02','E03','E04','E05','E06','E07'])
    export.add_argument('--output', required=True)
    run = sub.add_parser('run', help='paid live evaluation with real GitHub, explicit credentials and all limits')
    run.add_argument('--config', required=True)
    run.add_argument('--output', required=True)
    compare = sub.add_parser('report', help='aggregate stored run.json files without executing code')
    compare.add_argument('directory')
    args = p.parse_args(argv)
    try:
        if args.command == 'self-test':
            for line in self_test(ROOT / 'tasks'):
                print(line)
        elif args.command == 'validate':
            for path in sorted((ROOT / 'tasks').iterdir()):
                task = Task.load(path)
                print(task.id, 'v'+str(task.data['version']), task.fingerprint)
            if args.config:
                load_config(args.config, ROOT)
                print('configuration: valid (credentials and remote SHAs not checked)')
        elif args.command == 'build-core':
            print(build_core(args.source, args.sha, ROOT/'integrations/ai-factory', args.output))
        elif args.command == 'export-task':
            task = Task.load(ROOT/'tasks'/args.task)
            shutil.copytree(task.root/'initial', args.output)
            print(Path(args.output).resolve())
        elif args.command == 'run':
            runs = Evaluation(args.config, ROOT, args.output).run()
            print(Path(args.output).resolve()/'report.md')
            return 0 if runs and all(r['status']=='passed' for r in runs) else 1
        elif args.command == 'report':
            directory = Path(args.directory).resolve()
            runs = [read_json(p) for p in sorted(directory.glob('*/run.json'))]
            report(runs, directory)
            print(directory/'report.md')
        return 0
    except EvalError as e:
        print(f'{e.category}: {e}', file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError, OSError) as e:
        print(f'infrastructure_error: invalid input or unavailable resource ({type(e).__name__})', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
