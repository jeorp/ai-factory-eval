"""Cost-free harness contracts with explicitly fake Runner/GitHub boundaries."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory_eval.orchestrator import Evaluation
from factory_eval.scoring import score, metrics
from factory_eval.task import Task
from factory_eval.util import Invalid, BudgetExceeded

ROOT=Path(__file__).resolve().parents[1]
class FakeAPI:
 def __init__(self):self.mutations=[]
 def verify_repository(self,repository):return {'default_branch':'main','full_name':repository}
 def request(self,method,path,data=None):self.mutations.append((method,path));return {'number':1}
 def cleanup(self,*args):self.mutations.append(('cleanup',args[1]))

class FakeAdapter:
 calls=[]
 def __init__(self,binary,env,trial,total,event):self.task=None;self.follows=0;self.number=0;self.closed=False
 def result(self,status):
  changes=[]
  for p in self.task.root.joinpath('reference').iterdir():
   old=self.task.root/'initial'/p.name
   if p.is_file() and (not old.exists() or p.read_bytes()!=old.read_bytes()):
    content=p.read_text()
    if self.task.id=='E03' and not self.follows and p.name=='summary.go':content=content.split('func FormatSummary')[0]
    changes.append({'path':p.name,'action':'update' if old.exists() else 'create','content':content,'base_commit':'0'*40})
  return {'implementation':{'status':status,'branch':'ai-factory/eval-test-work','pullNumber':self.number,'commit':'a'*40,'repairRound':1,'ci':{'state':'passed'}},'changes':changes}
 def call(self,action,**kw):
  self.calls.append(action)
  if action=='hello':return {'capabilities':{k:True for k in ['real_runner','native_ci','pre_call_budget','draft_pr','followup']}}
  if action=='start':self.number=kw.get('pull_number',0);self.task=Task.load(ROOT/'tasks'/kw['task_id']);return self.result('plan_ready')
  if action=='approve':return self.result('ready')
  if action=='followup':self.follows+=1;return self.result('plan_ready')
  if action=='fix_ci':return self.result('plan_ready')
  if action in ('publish','check_ci'):self.number=1;return self.result('published')
  raise AssertionError(action)
 def close(self):self.closed=True

class OrchestrationTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'EVAL_MODEL_KEY':'mock-key','EVAL_WORK_TOKEN':'mock-work','EVAL_CONTROL_TOKEN':'mock-control'});self.env.start()
  self.temp=tempfile.TemporaryDirectory();self.e=Evaluation(ROOT/'examples/config.json',ROOT,Path(self.temp.name)/'results')
  self.e.work_api=FakeAPI();FakeAdapter.calls=[]
 def tearDown(self):self.temp.cleanup();self.env.stop()
 def grader(self,task,record,commit,base,trial,phase='implementation',trace=None):
  initial=phase=='initial'
  passed=not initial
  reqs={r['id']:{'passed': task.data['initial'][r['id']] if initial else passed,'tests':r['tests'],'job':r['job']}for r in task.data['requirements']}
  if task.id=='E03' and not initial:
   from factory_eval.grader import trace_valid
   self.assertTrue(trace_valid(trace))
  return {'grading':{'status':'passed' if passed else 'implementation_failed','requirements':reqs,'regression':False},'evidence':{'logs_complete':True}}
 def test_e03_followup_is_before_first_publish_and_saved(self):
  self.e.grade=self.grader
  with patch('factory_eval.orchestrator.Adapter',FakeAdapter):r=self.e.run_one(Task.load(ROOT/'tasks/E03'),0,'baseline','b'*40,'fake')
  self.assertEqual(r['status'],'passed');self.assertLess(FakeAdapter.calls.index('followup'),FakeAdapter.calls.index('publish'))
  self.assertTrue((self.e.destination/r['run_id']/'changes/before-followup.json').exists())
  self.assertFalse(r['cleanup_pending'])
 def test_e04_creates_real_initial_pr_and_repairs_all_failures(self):
  def grade(task,record,commit,base,trial,phase='implementation',trace=None):
   g=self.grader(task,record,commit,base,trial,phase,trace)
   if phase=='implementation' and not record['attempts']:g['grading']['status']='implementation_failed'
   return g
  self.e.grade=grade
  with patch('factory_eval.orchestrator.Adapter',FakeAdapter):r=self.e.run_one(Task.load(ROOT/'tasks/E04'),0,'candidate','c'*40,'fake')
  self.assertEqual(r['status'],'passed');self.assertEqual(len(r['attempts']),2)
  self.assertEqual(FakeAdapter.calls.count('fix_ci'),1)
  self.assertEqual([x[0]for x in self.e.work_api.mutations],['POST','POST','cleanup'])
  self.assertEqual(metrics([r])['false_completion']['count'],1)
 def test_stop_preserves_partial_changes_and_branch_without_pr(self):
  self.e.grade=self.grader
  class PartialAdapter(FakeAdapter):
   def __init__(self,binary,env,trial,total,event):
    super().__init__(binary,env,trial,total,event);self.event=event
   def call(self,action,**kw):
    if action=='publish':
     self.event({'type':'checkpoint','checkpoint':{'Implementation':{'status':'publishing','refBranch':'ai-factory/eval-owned-work','commit':'a'*40}},'changes':[{'path':'language.go','action':'update','content':'partial'}]})
     raise BudgetExceeded('stopped after branch creation')
    return super().call(action,**kw)
  with patch('factory_eval.orchestrator.Adapter',PartialAdapter):r=self.e.run_one(Task.load(ROOT/'tasks/E01'),0,'baseline','b'*40,'fake')
  self.assertEqual(r['status'],'budget_exceeded');self.assertTrue(r['cleanup_pending'])
  self.assertTrue((self.e.destination/r['run_id']/'changes/checkpoint.json').exists())
  self.assertEqual(self.e.work_api.mutations,[])
 def test_preflight_error_keeps_every_planned_trial(self):
  with patch.object(self.e,'preflight',side_effect=Invalid('credentials unavailable')):
   runs=self.e.run()
  self.assertEqual(len(runs),24)
  self.assertTrue(all(r['status']=='infrastructure_error' for r in runs))
  self.assertEqual(len(list(self.e.destination.glob('*/run.json'))),24)
 def test_budget_stop_before_start_is_recorded_without_writes(self):
  self.e.total.limits['seconds']=0
  with patch('factory_eval.orchestrator.Adapter',FakeAdapter):r=self.e.run_one(Task.load(ROOT/'tasks/E01'),0,'candidate','c'*40,'fake')
  self.assertEqual(r['status'],'budget_exceeded');self.assertEqual(self.e.work_api.mutations,[])


 def test_ci_repair_checkpoint_may_omit_publication_fields(self):
  def grade(task,record,commit,base,trial,phase='implementation',trace=None):
   g=self.grader(task,record,commit,base,trial,phase,trace)
   if phase=='implementation' and not record['attempts']:g['grading']['status']='implementation_failed'
   return g
  self.e.grade=grade
  class RepairCheckpointAdapter(FakeAdapter):
   def __init__(self,binary,env,trial,total,event):
    super().__init__(binary,env,trial,total,event);self.event=event
   def call(self,action,**kw):
    if action=='fix_ci':
     # StartCIFix/NextImplementation retains PullNumber while a new draft
     # intentionally omits publication-only branch and commit fields.
     self.event({'type':'checkpoint','checkpoint':{'Implementation':{'status':'planning','pullNumber':1,'version':1}}})
    return super().call(action,**kw)
  with patch('factory_eval.orchestrator.Adapter',RepairCheckpointAdapter):r=self.e.run_one(Task.load(ROOT/'tasks/E01'),0,'baseline','b'*40,'fake')
  self.assertEqual(r['status'],'passed',r.get('reason'))
  self.assertEqual(len(r['attempts']),2)
  self.assertEqual(FakeAdapter.calls.count('fix_ci'),1)
  self.assertEqual(r['branch'],'ai-factory/eval-test-work')
  self.assertEqual(r['commit'],'a'*40)
  self.assertFalse(r['cleanup_pending'])

if __name__=='__main__':unittest.main()
