import json
from pathlib import Path
import tempfile
import unittest

from factory_eval.adapter import Adapter
from factory_eval.budget import Budget
from factory_eval.util import BudgetExceeded, Invalid

SCRIPT='''#!/usr/bin/env python3
import json,sys
for line in sys.stdin:
 command=json.loads(line)
 def emit(type,**kw):print(json.dumps({'version':'factory-eval/v1','type':type,**kw}),flush=True)
 if command['action']=='hello':
  emit('result',capabilities={'real_runner':False});continue
 if command['action']=='model':
  emit('reserve',input_tokens=20,output_tokens=10)
  ack=json.loads(sys.stdin.readline())
  if ack.get('allow'):
   emit('usage',input_tokens=5,output_tokens=3)
   emit('checkpoint',checkpoint={'Progress':'verified mock contract'})
   emit('result',implementation={'status':'ready'})
 if command['action']=='write':
  emit('write_intent');json.loads(sys.stdin.readline());emit('result',implementation={'status':'published'})
 if command['action']=='bad':print('not-json',flush=True)
'''

def budget(calls=3):
 return Budget({'calls':calls,'input_tokens':200,'output_tokens':200,'seconds':5,'ci_attempts':3,'cost':'1'},
  {'input_per_million':'1','output_per_million':'2','currency':'USD','effective_date':'2026-11-01'})

class AdapterTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.binary=Path(self.temp.name)/'mock-core';self.binary.write_text(SCRIPT);self.binary.chmod(0o755)
  self.events=[];self.trial=budget();self.total=budget();self.adapter=Adapter(self.binary,{},self.trial,self.total,self.events.append)
 def tearDown(self):self.adapter.close();self.temp.cleanup()
 def test_handshake_and_checkpoint_contract(self):
  self.assertFalse(self.adapter.call('hello')['capabilities']['real_runner'])
  self.assertEqual(self.adapter.call('model')['implementation']['status'],'ready')
  self.assertEqual(self.trial.snapshot()['calls'],1);self.assertEqual(self.total.snapshot()['input_tokens'],5)
  self.assertTrue(any(e['type']=='checkpoint' for e in self.events))
 def test_total_budget_blocks_next_physical_request(self):
  self.total.limits['calls']=1;self.adapter.call('model')
  with self.assertRaises(BudgetExceeded):self.adapter.call('model')
  self.assertEqual(self.trial.snapshot()['calls'],1)
 def test_cancel_before_write_intent_ack(self):
  self.total.cancelled=True
  from factory_eval.util import Cancelled
  with self.assertRaises(Cancelled):self.adapter.call('write')
 def test_invalid_protocol_is_infrastructure_error(self):
  with self.assertRaises(Invalid):self.adapter.call('bad')

if __name__=='__main__':unittest.main()
