import copy
import importlib.util
from pathlib import Path
import unittest
s=importlib.util.spec_from_file_location('auth',Path(__file__).with_name('release_authorization.py'))
a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
class Tests(unittest.TestCase):
 def setUp(self):
  self.ctx=dict(repository=a.REPOSITORY,ref='refs/heads/main',event='workflow_dispatch',sha='a'*40,actor='Flem15',actor_id='127445894',triggering_actor='Flem15',action='publication',confirmation='development-publication',region='us-east-2',role='arn:aws:iam::655469962246:role/llm-parity-actions-ecr-push')
  self.env=dict(can_admins_bypass=False,protection_rules=[],deployment_branch_policy=dict(protected_branches=False,custom_branch_policies=True))
  self.branches=dict(total_count=1,branch_policies=[dict(name='main',type='branch')])
  self.protect=dict(required_pull_request_reviews=dict(required_approving_review_count=0,dismiss_stale_reviews=True,require_code_owner_reviews=False,require_last_push_approval=False),enforce_admins={'enabled':True},allow_force_pushes={'enabled':False},allow_deletions={'enabled':False},required_status_checks={'strict':True,'checks':[{'context':n,'app_id':15368} for n in a.REQUIRED_CHECKS]})
 def check(self):return a.validate(self.ctx,self.env,self.branches,self.protect,{'sha':'a'*40})
 def test_complete(self):self.assertEqual(self.check(),[])
 def test_untrusted_contexts(self):
  for key,value in [('repository','attacker/fork'),('ref','refs/tags/main'),('event','pull_request_target'),('event','push'),('actor','someone'),('actor_id','1'),('triggering_actor','someone'),('confirmation','production-publication'),('action','infrastructure'),('sha','b'*40),('region','us-east-1'),('role','admin'),('image_tag','b'*40)]:
   original=copy.deepcopy(self.ctx);self.ctx[key]=value;self.assertTrue(self.check(),key);self.ctx=original
 def test_no_reviewer(self):self.assertEqual(self.check(),[])
 def test_independent_review_deadlock(self):self.protect['required_pull_request_reviews']['required_approving_review_count']=1;self.assertTrue(self.check())
 def test_admin_bypass(self):self.env['can_admins_bypass']=True;self.assertTrue(self.check())
 def test_extra_branch(self):self.branches['total_count']=2;self.assertTrue(self.check())
 def test_tag_named_main(self):self.branches['branch_policies'][0]['type']='tag';self.assertTrue(self.check())
 def test_missing_protection(self):self.protect={};self.assertTrue(self.check())
 def test_wrong_producer(self):self.protect['required_status_checks']['checks'][0]['app_id']=1;self.assertTrue(self.check())
 def test_review_bypass(self):self.protect['required_pull_request_reviews']['bypass_pull_request_allowances']={'apps':[1]};self.assertTrue(self.check())
 def test_missing_checks(self):self.protect['required_status_checks']['checks']=[];self.assertTrue(self.check())
 def test_force_push(self):self.protect['allow_force_pushes']['enabled']=True;self.assertTrue(self.check())
 def test_unbound_status_names_rejected(self):
  self.protect['required_status_checks']={'strict':True,'contexts':list(a.REQUIRED_CHECKS)};self.assertTrue(self.check())
 def test_missing_scan_gate_rejected(self):
  self.protect['required_status_checks']['checks']=[c for c in self.protect['required_status_checks']['checks'] if c['context']!='Release Image Security'];self.assertTrue(self.check())
 def test_explicit_deployment_confirmation(self):
  self.ctx.update(action='deployment',confirmation='development-deployment');self.assertEqual(self.check(),[])
  self.ctx['confirmation']='development-publication';self.assertTrue(self.check())
 def test_manual_confirmation_and_scan_are_wired(self):
  root=Path(__file__).resolve().parents[2]
  for name,action in [('build-push-image.yml','publication'),('deploy-ecs-release.yml','deployment')]:
   text=(root/'.github/workflows'/name).read_text();self.assertIn('RELEASE_ACTION: '+action,text);self.assertIn('RELEASE_CONFIRMATION: ${{ inputs.confirmation }}',text)
  ci=(root/'.github/workflows/ci.yml').read_text();self.assertIn('name: Release Image Security',ci)
  self.assertEqual(ci.count('python3 .github/scripts/scan_release_image.py'),3)
 def test_workflow_guards_precede_credentials(self):
  root=Path(__file__).resolve().parents[2]
  for name in ['build-push-image.yml','deploy-ecs-release.yml']:
   text=(root/'.github/workflows'/name).read_text()
   credentials=text.index('uses: aws-actions/configure-aws-credentials')
   self.assertLess(text.index('run: python3 .github/scripts/release_authorization.py'),credentials)
   self.assertLess(text.index('run: python3 .github/scripts/predeployment_gate.py release-review/gate-input.json'),credentials)
   self.assertNotIn('deployment: false',text)
if __name__=='__main__':unittest.main()
