import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from factory_eval.budget import Budget
from factory_eval.config import load_config
from factory_eval.github import GitHub, StripCredentialRedirect
from factory_eval.grader import inventory, scope, trace_valid, reference_trace, container_runner, go_tests
from factory_eval.orchestrator import schedule, validate_initial
from factory_eval.report import report
from factory_eval.scoring import score, metrics, classify
from factory_eval.task import Task
from factory_eval.util import Invalid, BudgetExceeded, Cancelled, safe_path, sha, repo, redact, write_json

ROOT=Path(__file__).resolve().parents[1]
IDENTITY={'repository':'example/work','commit':'a'*40,'definition_sha':'b'*40,'run_id':'test-run','issuer':99,'workflow_run_id':123}

def evidence(task):
    return {**IDENTITY,'scope_valid':True,'regression_failed':False,
        'jobs':[{'name':n,'executed':True,'status':'passed'}for n in task.data['required_jobs']],
        'tests':[{'name':n,'executed':True,'status':'passed'}for r in task.data['requirements']for n in r['tests']]}

def budget(**limits):
    return Budget({'calls':2,'input_tokens':200,'output_tokens':100,'seconds':10,'ci_attempts':3,'cost':'1',**limits},
                  {'input_per_million':'10','output_per_million':'30','currency':'USD','effective_date':'2026-11-01'})

class BudgetTests(unittest.TestCase):
    def test_exact_limit_then_stop(self):
        b=budget();b.reserve(100,50);b.record(10,5);b.reserve(100,50)
        with self.assertRaises(BudgetExceeded):b.reserve(0,0)
        self.assertEqual(b.snapshot()['calls'],2)
        self.assertEqual(b.snapshot()['input_tokens'],10)
        self.assertIsNone(b.snapshot()['provider_billed_cost'])
    def test_estimate_not_actual_controls_next_call(self):
        b=budget(input_tokens=10)
        with self.assertRaises(BudgetExceeded):b.reserve(11,1)
        self.assertEqual(b.usage.calls,0)
    def test_cost_gate(self):
        b=budget(cost='0.00001')
        with self.assertRaises(BudgetExceeded):b.reserve(2,1)
    def test_errors_retries_still_consume_reservations(self):
        b=budget();b.reserve(20,20);b.reserve(20,20)
        with self.assertRaises(BudgetExceeded):b.reserve(20,20)
    def test_deadline_and_cancel(self):
        clock=[0];b=budget();b.clock=lambda:clock[0];b.started=0
        clock[0]=11
        with self.assertRaises(BudgetExceeded):b.check()
        b.cancelled=True
        with self.assertRaises(Cancelled):b.reserve(0,0)
    def test_observed_usage_cannot_exceed_limit(self):
        b=budget()
        with self.assertRaises(BudgetExceeded):b.record(201,1)
    def test_missing_limits_and_nonfinite_numbers(self):
        for value in [0,-1,'NaN','Infinity',True]:
            with self.subTest(value=value), self.assertRaises(Invalid):budget(calls=value)
        b=budget()
        with self.assertRaises(Invalid):Budget({},b.pricing)
    def test_three_attempts_is_maximum(self):
        b=budget(ci_attempts=3)
        b.reserve_ci(initial=True)
        for _ in range(3):b.reserve_ci()
        with self.assertRaises(BudgetExceeded):b.reserve_ci()
        self.assertEqual(b.snapshot()['ci_runs'],4)
    def test_total_ci_limit_includes_initial_runs_without_losing_phase_counts(self):
        b=budget(ci_attempts=2)
        b.reserve_ci(initial=True,include_initial=True)
        b.reserve_ci(include_initial=True)
        self.assertEqual(b.snapshot()['ci_runs'],2)
        self.assertEqual(b.snapshot()['initial_ci_runs'],1)
        for initial in (True,False):
            with self.assertRaises(BudgetExceeded):b.reserve_ci(initial=initial,include_initial=True)

class ScoringTests(unittest.TestCase):
    def setUp(self):self.task=Task.load(ROOT/'tasks/E01');self.e=evidence(self.task)
    def test_complete_pass(self):self.assertEqual(score(self.task,self.e,IDENTITY)['status'],'passed')
    def test_identity_is_required(self):
        for key in IDENTITY:
            e=copy.deepcopy(self.e);e[key]='other'
            with self.subTest(key=key):self.assertEqual(score(self.task,e,IDENTITY)['status'],'implementation_failed')
    def test_missing_skipped_unexecuted_zero_detected(self):
        for transform in [lambda e:e.update(tests=[]),lambda e:e['tests'].pop(),
                          lambda e:e['tests'][0].update(executed=False),lambda e:e['tests'][0].update(status='skipped')]:
            e=copy.deepcopy(self.e);transform(e)
            self.assertEqual(score(self.task,e,IDENTITY)['status'],'implementation_failed')
    def test_duplicate_test_not_counted(self):
        self.e['tests'].append(self.e['tests'][0]);self.assertEqual(score(self.task,self.e,IDENTITY)['status'],'implementation_failed')
    def test_required_job_missing_or_neutral(self):
        for change in [lambda e:e['jobs'].pop(),lambda e:e['jobs'][0].update(status='neutral'),lambda e:e['jobs'][0].update(executed=False)]:
            e=copy.deepcopy(self.e);change(e);self.assertEqual(score(self.task,e,IDENTITY)['status'],'implementation_failed')
    def test_test_tampering_denies_pass(self):
        self.e['scope_valid']=False;self.assertEqual(score(self.task,self.e,IDENTITY)['status'],'implementation_failed')
    def test_ai_acceptance_criteria_do_not_change_fixed_requirements(self):
        self.e['ai_acceptance_criteria']=[];self.e['tests'][0]['status']='failed'
        self.assertEqual(len(score(self.task,self.e,IDENTITY)['requirements']),6)
        self.assertEqual(score(self.task,self.e,IDENTITY)['status'],'implementation_failed')
    def test_language_fluency_is_unmeasured(self):self.assertEqual(score(self.task,self.e,IDENTITY)['language_quality'],'unmeasured')
    def test_classifications(self):
        self.assertEqual(classify([]),'blocked')
        self.assertEqual(classify([],stopped='budget_exceeded'),'budget_exceeded')
    def test_metrics_keep_unscored_trials_and_denominators(self):
        good=score(self.task,self.e,IDENTITY)
        self.e['tests'][0]['status']='failed';bad=score(self.task,self.e,IDENTITY)
        common={'variant':'baseline','requirement_ids':['E01-R01']}
        runs=[{**common,'status':'passed','attempts':[{'grading':good}],'completed':True},
              {**common,'status':'implementation_failed','attempts':[{'grading':bad}],'completed':True},
              {**common,'status':'blocked','attempts':[],'completed':False}]
        m=metrics(runs)
        self.assertEqual(m['final_all'],{'count':1,'denominator':3,'rate':1/3})
        self.assertEqual(m['final_scored']['denominator'],2)
        self.assertEqual(m['false_completion']['count'],1)
        self.assertEqual(m['requirements']['E01-R01']['denominator'],3)
    def test_initial_failures_are_not_regressions(self):
        self.e['jobs'][2]['status']='failed';self.assertFalse(score(self.task,self.e,IDENTITY)['regression'])

class TaskTests(unittest.TestCase):
    def test_all_definitions_have_full_reference_coverage(self):
        for tid in ['E01','E02','E03','E04']:
            t=Task.load(ROOT/'tasks'/tid)
            self.assertTrue(all(t.data['reference'].values()));self.assertEqual(len(t.fingerprint),64)
    def test_scope_paths(self):
        t=Task.load(ROOT/'tasks/E04')
        self.assertTrue(t.permits('service.go'))
        for path in ['service_test.go','.github/workflows/ci.yml','go.mod','README.md','grading/test.go','nested/service.go','nested/service_test.go']:
            with self.subTest(path=path):self.assertFalse(t.permits(path))
    def test_plan_rejected_before_write(self):
        t=Task.load(ROOT/'tasks/E01')
        for files in [[],[{'path':'go.mod','action':'update'}],[{'path':'language.go','action':'move'}],
                      [{'path':'language.go','action':'create','content':'x'*100001}]]:
            with self.assertRaises(Invalid):t.approve(files)
    def test_reference_scope_valid(self):
        for tid in ['E01','E02','E03','E04']:
            t=Task.load(ROOT/'tasks'/tid);self.assertTrue(scope(t,t.root/'reference'))
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'evil').symlink_to('/etc/passwd')
            with self.assertRaises(Invalid):inventory(d)
    def test_trace_is_independent_of_final_file(self):
        t=Task.load(ROOT/'tasks/E03');trace=reference_trace(t);self.assertTrue(trace_valid(trace))
        trace['before_followup']['published']=True;self.assertFalse(trace_valid(trace))
        self.assertFalse(trace_valid(None))
    def test_create_reference_maintained(self):
        trace=reference_trace(Task.load(ROOT/'tasks/E03'))
        trace['after_followup']['action']='update';self.assertFalse(trace_valid(trace))
    def test_e03_public_initial_state_does_not_reveal_followup(self):
        task=Task.load(ROOT/'tasks/E03')
        public=task.request(0)+'\n'+''.join(p.read_text() for p in (task.root/'initial').rglob('*') if p.is_file())
        self.assertNotIn('FormatSummary',public)
        self.assertNotIn(task.data['requests'][1],public)
        self.assertIn('FormatSummary',task.request(1))
    def test_trace_rejects_preimplemented_followup_and_noop(self):
        trace=reference_trace(Task.load(ROOT/'tasks/E03'))
        trace['before_followup']['content']=trace['after_followup']['content']
        self.assertFalse(trace_valid(trace))
        trace['before_followup']['content']+='\n// extra\n'
        self.assertFalse(trace_valid(trace))
    def test_trace_requires_both_unpublished_snapshots(self):
        for value in (True,None):
            trace=reference_trace(Task.load(ROOT/'tasks/E03'))
            trace['after_followup']['published']=value
            self.assertFalse(trace_valid(trace))
    def test_requests_expose_exact_scope_without_hidden_grading(self):
        for tid in ['E01','E02','E03','E04']:
            task=Task.load(ROOT/'tasks'/tid)
            for i in range(len(task.data['requests'])):
                request=task.request(i)
                self.assertTrue(request.startswith(task.data['requests'][i]))
                bounds=json.loads(request.rsplit('\n',1)[1])
                self.assertEqual(bounds,{k:task.data[k] for k in ('allowed_paths','forbidden_paths','max_files','max_bytes')})
                self.assertNotIn('requirements',bounds)
    def test_container_does_not_receive_secrets_or_writable_source(self):
        t=Task.load(ROOT/'tasks/E01')
        captured=[]
        with patch('factory_eval.grader.run',side_effect=lambda args,cwd:(captured.append(args) or (0,''))):
            container_runner('image',t,'/tmp/staged')(['go','test','.'],'/tmp/staged')
        args=captured[0]
        for flag in ['--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--user=65534:65534']:
            self.assertIn(flag,args)
        self.assertTrue(any(x.endswith(':/work:ro')for x in args))
        self.assertNotIn('GITHUB_TOKEN',' '.join(args));self.assertNotIn('docker.sock',' '.join(args))
    def test_other_failed_test_does_not_erase_passed_requirement(self):
        lines=[]
        for n,status in [('TestA','pass'),('TestB','fail')]:
            lines.extend([json.dumps({'Action':'run','Test':n}),json.dumps({'Action':status,'Test':n})])
        rows,_=go_tests('.', ['TestA','TestB'],lambda *_:(1,'\n'.join(lines)))
        self.assertEqual([r['status']for r in rows],['passed','failed'])
    def test_zero_tests_and_fake_output_cannot_pass(self):
        rows,_=go_tests('.', ['TestA'],lambda *_:(0,json.dumps({'Action':'output','Output':'TestA passed'})))
        self.assertFalse(rows[0]['executed']);self.assertEqual(rows[0]['status'],'failed')

class GithubTests(unittest.TestCase):
    def test_pagination_and_incomplete_page(self):
        api=GitHub('fake');api.request=lambda method,path: list(range(100)) if path.endswith('&page=1') else [1]
        self.assertEqual(len(api.pages('/test')),101)
    def test_dispatch_requires_exact_definition_commit(self):
        api=GitHub('fake');api.resolve=lambda *_:'a'*40
        with self.assertRaises(Invalid):api.dispatch('example/eval','tag','b'*40,{})
    def test_redirect_does_not_leak_token_to_signed_artifact_url(self):
        request=urllib.request.Request('https://api.github.com/artifact',headers={'Authorization':'Bearer secret'})
        redirected=StripCredentialRedirect().redirect_request(request,None,302,'',{},'https://objects.githubusercontent.com/signed')
        self.assertFalse(redirected.has_header('Authorization'))
        with self.assertRaises(Invalid):StripCredentialRedirect().redirect_request(request,None,302,'',{},'http://example.com')
    def test_cleanup_rejects_foreign_or_moved_branch_before_mutation(self):
        api=GitHub('fake');calls=[]
        def request(method,path,data=None):
            calls.append(method)
            return {'head':{'ref':'main','sha':'a'*40,'repo':{'full_name':'example/work'}},'draft':True,'merged':False}
        api.request=request
        with self.assertRaises(Invalid):api.cleanup('example/work','ai-factory/eval-run-work',1,'a'*40,'run')
        self.assertEqual(calls,['GET'])
    def test_cleanup_owned_pr_only_and_no_force_push(self):
        api=GitHub('fake');calls=[];branch='ai-factory/eval-run-work'
        def request(method,path,data=None):
            calls.append((method,path,data))
            if '/pulls/' in path and method=='GET':return {'head':{'ref':branch,'sha':'a'*40,'repo':{'full_name':'example/work'}},'draft':True,'merged':False}
            if '/git/ref/' in path:return {'object':{'sha':'a'*40}}
        api.request=request;api.cleanup('example/work',branch,1,'a'*40,'run')
        self.assertEqual([x[0]for x in calls],['GET','GET','PATCH','DELETE'])
    def test_initial_blob_validation_is_independent(self):
        api=GitHub('fake');api.request=lambda *_:{'truncated':False,'tree':[]}
        with self.assertRaises(Invalid):validate_initial(api,Task.load(ROOT/'tasks/E01'),'example/work','a'*40)

class ConfigAndArtifactTests(unittest.TestCase):
    def test_example_valid_and_alternating_schedule(self):
        c=load_config(ROOT/'examples/config.json',ROOT)
        entries=schedule(c)
        self.assertEqual(len(entries),24)
        self.assertEqual([x[2]for x in entries[:4]],['baseline','candidate','baseline','candidate'])
    def test_work_cannot_be_eval_or_core_repository(self):
        for key in ['evaluation_repository','core_repository']:
            c=json.loads((ROOT/'examples/config.json').read_text());c['work_repositories']['E01']['repository']=c[key]
            with tempfile.TemporaryDirectory()as d:
                f=Path(d)/'config.json';write_json(f,c)
                with self.assertRaises(Invalid):load_config(f,ROOT)
    def test_credentials_are_names_only(self):
        c=json.loads((ROOT/'examples/config.json').read_text());c['credentials']['model']='sk-actual-secret'
        with tempfile.TemporaryDirectory()as d:
            f=Path(d)/'config.json';write_json(f,c)
            with self.assertRaises(Invalid):load_config(f,ROOT)
    def test_secrets_headers_and_signed_urls_are_redacted(self):
        value=redact('custom-token Authorization: Bearer abc\nhttps://host/file?sig=abc\nghp_abcdefghijklm', ['custom-token'])
        for s in ['custom-token','Bearer abc','sig=abc','ghp_abcdefghijklm']:self.assertNotIn(s,value)
    def test_paths_commits_and_repositories(self):
        for p in ['/absolute','a/../b','./a','a//b','a\\b']:
            with self.assertRaises(Invalid):safe_path(p)
        with self.assertRaises(Invalid):sha('main')
        with self.assertRaises(Invalid):repo('https://github.com/x/y')
    def test_empty_report_still_retains_categories(self):
        with tempfile.TemporaryDirectory()as d:
            report([],d);m=json.loads((Path(d)/'comparison.json').read_text())
            self.assertEqual(m['baseline']['total'],0);self.assertIn('budget_exceeded',m['candidate']['categories'])

if __name__=='__main__':unittest.main()
