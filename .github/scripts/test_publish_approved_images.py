import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import publish_approved_images as p

class PublishTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.sha='a'*40;self.digest='sha256:'+'b'*64
  self.config=json.loads(Path('.github/publication-targets.json').read_text());self.calls=[]
 def fake_run(self,args,**kwargs):
  self.calls.append(args)
  if args[0]=='skopeo':return SimpleNamespace(returncode=0)
  repo=args[args.index('--repository-name')+1]
  return SimpleNamespace(returncode=0,stdout=json.dumps({'imageDetails':[{'registryId':'655469962246','repositoryName':repo,'imageTags':[self.sha],'imageDigest':self.digest}]}))
 def execute(self,runner=None):
  with patch.object(p,'verify_bundle',return_value={'expected_images':{n['name']:self.digest for n in self.config['images']}}),patch.object(p,'verify',return_value=({'image':'verified@'+self.digest},'[{}]')),patch.object(p,'oci',return_value=(self.digest,'config',b'raw')):
   p.publish(self.sha,self.root/'bundle',self.root/'out',run=runner or self.fake_run)
 def test_existing_images_are_verified_without_upload(self):
  self.execute();self.assertEqual(len(self.calls),3);self.assertTrue((self.root/'out/images.json').exists());self.assertTrue(all(c[0]=='aws' for c in self.calls))
 def test_failed_evidence_never_calls_aws(self):
  with patch.object(p,'verify_bundle',side_effect=ValueError('bad signature')),self.assertRaises(ValueError):p.publish(self.sha,self.root,self.root/'out',run=self.fake_run)
  self.assertEqual(self.calls,[])
 def test_access_denied_never_pushes(self):
  def denied(args,**kw):self.calls.append(args);return SimpleNamespace(returncode=1,stderr='AccessDeniedException')
  with self.assertRaises(RuntimeError):self.execute(denied)
  self.assertEqual(len(self.calls),1)
 def test_mismatched_existing_digest_never_overwrites(self):
  def wrong(args,**kw):
   r=self.fake_run(args,**kw);r.stdout=r.stdout.replace(self.digest,'sha256:'+'c'*64);return r
  with self.assertRaises(ValueError):self.execute(wrong)
  self.assertEqual(len(self.calls),1)
 def test_missing_later_repository_blocks_all_uploads(self):
  def missing(args,**kw):
   self.calls.append(args)
   if 'bot-city-dev' in args:return SimpleNamespace(returncode=1,stderr='RepositoryNotFoundException')
   return SimpleNamespace(returncode=1,stderr='An error occurred (ImageNotFoundException)')
  with self.assertRaises(RuntimeError):self.execute(missing)
  self.assertEqual(len(self.calls),2);self.assertTrue(all(c[0]=='aws' for c in self.calls))
 def test_missing_images_use_digest_preserving_copy(self):
  seen=set()
  def absent(args,**kw):
   if args[0]=='aws' and tuple(args) not in seen:
    seen.add(tuple(args));self.calls.append(args);return SimpleNamespace(returncode=1,stderr='An error occurred (ImageNotFoundException)')
   return self.fake_run(args,**kw)
  self.execute(absent)
  copies=[c for c in self.calls if c[0]=='skopeo'];self.assertEqual(len(copies),3)
  for c in copies:self.assertIn('--preserve-digests',c);self.assertTrue(c[-1].endswith(':'+self.sha))
if __name__=='__main__':unittest.main()
