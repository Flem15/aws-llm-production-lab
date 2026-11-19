import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

s=importlib.util.spec_from_file_location('evidence',Path(__file__).with_name('publication_evidence.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
v=importlib.util.spec_from_file_location('verifier',Path(__file__).with_name('verify_release.py'));verifier=importlib.util.module_from_spec(v);v.loader.exec_module(verifier)
class EvidenceTests(unittest.TestCase):
 def setUp(self):
  self.sha='a'*40;self.digest='b'*64;self.now=datetime.now(timezone.utc)
  self.config={'schema':1,'evidence_workflow':'.github/workflows/review.yml','images':[{'name':n,'repository':m.REPOSITORIES[n],'context':c} for n,c in m.CONTEXTS.items()]}
  actor={'login':'Flem15','id':127445894}
  self.run={'id':123,'run_attempt':1,'head_sha':self.sha,'head_branch':'main','event':'workflow_dispatch','status':'completed','conclusion':'success','path':self.config['evidence_workflow'],'repository':{'full_name':m.REPO},'head_repository':{'full_name':m.REPO},'actor':actor,'triggering_actor':actor,'updated_at':self.now.isoformat()}
  self.artifact={'id':456,'name':'approved-release-evidence','expired':False,'digest':'sha256:'+self.digest,'workflow_run':{'id':123}}
 def check(self,run=None,artifacts=None,config=None):
  return m.validate(run or self.run,artifacts or {'total_count':1,'artifacts':[self.artifact]},self.sha,config or self.config,self.digest,self.now)
 def test_exact_approved_identity(self):self.assertEqual(self.check(),456)
 def test_untrusted_run_contexts(self):
  for key,value in [('head_sha','c'*40),('head_branch','feature'),('event','pull_request'),('conclusion','failure'),('path','.github/workflows/attacker.yml'),('run_attempt',2),('updated_at','2000-01-01T00:00:00Z'),('repository',{'full_name':'attacker/fork'}),('head_repository',{'full_name':'attacker/fork'}),('actor',{'login':'Flem15','id':1}),('triggering_actor',{'login':'attacker','id':127445894})]:
   with self.subTest(key=key):
    run=copy.deepcopy(self.run);run[key]=value
    with self.assertRaises(ValueError):self.check(run=run)
 def test_artifact_substitution(self):
  for key,value in [('digest','sha256:'+'c'*64),('expired',True),('workflow_run',{'id':789}),('id',0),('name','untrusted')]:
   a=copy.deepcopy(self.artifact);a[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):self.check(artifacts={'total_count':1,'artifacts':[a]})
 def test_artifact_ambiguity_and_truncation(self):
  for data in [{'total_count':101,'artifacts':[self.artifact]}, {'total_count':2,'artifacts':[self.artifact,self.artifact]}]:
   with self.assertRaises(ValueError):self.check(artifacts=data)
 def test_unapproved_destinations_block(self):
  for change in ('missing','duplicate','context','issuer'):
   c=copy.deepcopy(self.config)
   if change=='missing':c['images'][1]['repository']=None
   if change=='duplicate':c['images'][1]['repository']=c['images'][0]['repository']
   if change=='context':c['images'][1]['context']='.'
   if change=='issuer':c['evidence_workflow']=None
   with self.subTest(change=change),self.assertRaises(ValueError):self.check(config=c)
 def test_checked_in_configuration_blocks_until_reviewed(self):
  c=json.loads((Path(__file__).parents[1]/'publication-targets.json').read_text())
  self.assertEqual(len(m.targets(c)),3)
 def archive(self,name='gate-input.json'):
  stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w') as z:z.writestr(name,'{}')
  raw=stream.getvalue();return raw,hashlib.sha256(raw).hexdigest()
 def test_extract_exact_approved_archive(self):
  raw,digest=self.archive()
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'evidence';m.extract(raw,digest,out);self.assertTrue((out/'gate-input.json').is_file())
   with self.assertRaises(ValueError):m.extract(raw,digest,out)
 def test_bad_archive_never_extracts(self):
  for name in ['../gate-input.json','/gate-input.json','..\\gate-input.json']:
   raw,digest=self.archive(name)
   with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):m.extract(raw,digest,Path(tmp)/'out')
  raw,digest=self.archive()
  with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):m.extract(raw,'0'*64,Path(tmp)/'out')
 def test_all_three_registry_identities(self):
  for image in self.config['images']:
   item={'registryId':'655469962246','repositoryName':image['repository'],'imageTags':[self.sha],'imageDigest':'sha256:'+self.digest}
   self.assertEqual(verifier.resolve({'imageDetails':[item]},self.sha,image['name'],image['repository']),item['imageDigest'])
   item['repositoryName']='wrong'
   with self.assertRaises(ValueError):verifier.resolve({'imageDetails':[item]},self.sha,image['name'],image['repository'])
 def test_required_checks_reject_failed_wrong_source_or_spoofed_app(self):
  names=['Application and Docker','CDK Build and Synthesis','Restricted Egress Security and Docker','Bot City Safety and Docker','Release Image Security']
  good={'total_count':5,'check_runs':[{'name':n,'head_sha':self.sha,'app':{'id':15368},'status':'completed','conclusion':'success'} for n in names]}
  m.validate_checks(good,self.sha)
  for key,value in [('head_sha','c'*40),('app',{'id':1}),('conclusion','failure'),('status','in_progress')]:
   bad=copy.deepcopy(good);bad['check_runs'][0][key]=value
   with self.assertRaises(ValueError):m.validate_checks(bad,self.sha)
  bad=copy.deepcopy(good);bad['total_count']=6
  with self.assertRaises(ValueError):m.validate_checks(bad,self.sha)
 def test_session_cannot_deploy_and_verification_precedes_credentials(self):
  s=(Path(__file__).parents[1]/'workflows/build-push-image.yml').read_text()
  self.assertLess(s.index('Authenticate and download'),s.index('Configure temporary AWS'))
  self.assertLess(s.index('Verify all signed evidence'),s.index('Configure temporary AWS'))
  self.assertIn('inline-session-policy:',s)
  self.assertNotIn('aws ecs ',s);self.assertNotIn('workflow_run:',s)
  self.assertNotIn('docker build',s);self.assertNotIn('attestations: write',s)
if __name__=='__main__':unittest.main()
