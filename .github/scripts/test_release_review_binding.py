"""Fail closed before verification when deployment review bindings disagree."""
import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
s=importlib.util.spec_from_file_location('release_verify',Path(__file__).with_name('verify_release.py'));v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
class ReviewBindingTests(unittest.TestCase):
 def attempt(self,review,existing=False):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);sha='a'*40;digest='sha256:'+'b'*64
   (p/'image.json').write_text(json.dumps({'imageDetails':[{'registryId':'655469962246','repositoryName':'demo-inference-api','imageTags':[sha],'imageDigest':digest}]}))
   (p/'review.json').write_text(json.dumps(review));out=p/'verified.json'
   if existing:out.write_text('existing evidence')
   with patch.object(sys,'argv',['verify_release.py',sha,str(p/'image.json'),str(out),'--review',str(p/'review.json')]),patch.object(v,'verify') as verify:
    with self.assertRaises(ValueError):v.main()
    verify.assert_not_called();self.assertFalse(out.with_suffix('.raw.json').exists())
    self.assertEqual(out.read_text() if existing else out.exists(),'existing evidence' if existing else False)
 def test_wrong_revision(self):self.attempt({'expected_source_revision':'c'*40,'expected_images':{'api':'sha256:'+'b'*64}})
 def test_wrong_digest(self):self.attempt({'expected_source_revision':'a'*40,'expected_images':{'api':'sha256:'+'c'*64}})
 def test_missing_review_identity(self):self.attempt({})
 def test_existing_receipt_never_overwritten(self):self.attempt({},True)
if __name__=='__main__':unittest.main()
