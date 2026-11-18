import importlib.util,json,subprocess,unittest
from pathlib import Path
from types import SimpleNamespace
s=importlib.util.spec_from_file_location('verify',Path(__file__).with_name('verify_release.py'));v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
class Tests(unittest.TestCase):
 def setUp(self):self.sha='a'*40;self.digest='sha256:'+'b'*64;self.calls=[]
 def fake(self,args,**kwargs):
  self.calls.append(args)
  return SimpleNamespace(stdout='gh version 2.102.0\n' if args==['gh','--version'] else '[{"verificationResult":{}}]')
 def test_strict_verified_command(self):
  record,raw=v.verify(self.sha,self.digest,self.fake)
  self.assertTrue(record['verified']);cmd=self.calls[-1]
  for flag,value in [('--source-digest',self.sha),('--signer-digest',self.sha),('--cert-identity','https://github.com/'+v.WORKFLOW+'@refs/heads/main'),('--source-ref','refs/heads/main')]:self.assertEqual(cmd[cmd.index(flag)+1],value)
  self.assertNotIn('--signer-workflow',cmd);self.assertIn('--deny-self-hosted-runners',cmd);self.assertIn('oci://'+v.IMAGE+'@'+self.digest,cmd)
 def test_unpatched_cli_blocks(self):
  with self.assertRaises(ValueError):v.verify(self.sha,self.digest,lambda *a,**k:SimpleNamespace(stdout='gh version 2.97.0\n'))
 def test_failed_crypto_verification_blocks(self):
  def run(args,**kw):
   if args==['gh','--version']:return self.fake(args,**kw)
   raise subprocess.CalledProcessError(1,args)
  with self.assertRaises(subprocess.CalledProcessError):v.verify(self.sha,self.digest,run)
 def test_empty_results_block(self):
  with self.assertRaises(ValueError):v.verify(self.sha,self.digest,lambda args,**kw:self.fake(args,**kw) if args==['gh','--version'] else SimpleNamespace(stdout='[]'))
 def test_bad_inputs_never_invoke_cli(self):
  for sha,digest in [('main',self.digest),(self.sha,'latest'),(self.sha,'sha256:'+'A'*64)]:
   with self.assertRaises(ValueError):v.verify(sha,digest,self.fake)
  self.assertFalse(self.calls)
 def test_registry_binding(self):
  item={'registryId':'655469962246','repositoryName':'demo-inference-api','imageTags':[self.sha],'imageDigest':self.digest}
  self.assertEqual(v.resolve({'imageDetails':[item]},self.sha),self.digest)
  for key,value in [('registryId','111111111111'),('repositoryName','other'),('imageTags',['c'*40]),('imageDigest','latest')]:
   bad=dict(item);bad[key]=value
   with self.assertRaises(ValueError):v.resolve({'imageDetails':[bad]},self.sha)
 def test_ambiguous_image_blocks(self):
  with self.assertRaises(ValueError):v.resolve({'imageDetails':[]},self.sha)
 def test_workflow_verifies_before_deploy_and_does_not_attest_existing_image(self):
  root=Path(__file__).resolve().parents[2]
  deploy=(root/'.github/workflows/deploy-ecs-release.yml').read_text()
  self.assertLess(deploy.index('python3 .github/scripts/verify_release.py'),deploy.index('aws ecs register-task-definition'))
  self.assertIn('"${IMAGE_TAG}" "${IMAGE_DIGEST}"',deploy)
  self.assertIn('"${ACTIVE_IMAGE}" != "${EXPECTED_IMAGE}"',deploy)
  build=(root/'.github/workflows/build-push-image.yml').read_text()
  block=build[build.index('- name: Attest only'):build.index('- name: Verify new or existing')]
  self.assertIn("if: steps.image-check.outputs.exists != 'true'",block)
  self.assertIn('subject-digest: ${{ steps.digest.outputs.digest }}',block)
 def test_scan_blocks_high_unknown_secrets_and_incomplete_reports(self):
  sp=importlib.util.spec_from_file_location('scan',Path(__file__).with_name('scan_release_image.py'));m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
  self.assertEqual(m.validate({'ArtifactType':'container_image','Metadata':{'OS':{'Family':'alpine'}},'Results':[{'Class':'os-pkgs','Packages':[{'Name':'zlib'}]}]}),{})
  for report in [{}, {'ArtifactType':'container_image','Results':[]},
      {'ArtifactType':'container_image','Results':[{'Vulnerabilities':[{'Severity':'HIGH'}]}]},
      {'ArtifactType':'container_image','Results':[{'Vulnerabilities':[{'Severity':'UNKNOWN'}]}]},
      {'ArtifactType':'container_image','Results':[{'Secrets':[{}]}]}]:
   if report.get('Results'):
    report['Metadata']={'OS':{'Family':'alpine'}}
    for result in report['Results']:
     result.update({'Class':'os-pkgs','Packages':[{'Name':'zlib'}]})
   with self.assertRaises(ValueError):m.validate(report)
if __name__=='__main__':unittest.main()
