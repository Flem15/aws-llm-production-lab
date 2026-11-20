import sys,importlib.util,unittest,tempfile,json,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
p=Path(__file__).resolve().parent/'renew_publication_evidence.py'
s=importlib.util.spec_from_file_location('renew',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_source_separate_from_signer(self):
  b=m.binding('new-signer');self.assertEqual(b['image_source_revision'],m.SOURCE);self.assertEqual(b['evidence_signer_revision'],'new-signer');self.assertEqual(b['images'],m.IMAGES)
 def test_expired_review_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'gate-input.json').write_text(json.dumps({'evidence':{'review_records':{'path':'review.json'}}}))
   (p/'review.json').write_text(json.dumps({'approval':{'status':'approved_for_evidence_preparation_only','expires_at':'2020-01-01T00:00:00+00:00'},'npm':{'dispositions':[]}}))
   with self.assertRaises(ValueError):m.review_expiry(p)
 def test_review_not_approved(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'gate-input.json').write_text(json.dumps({'evidence':{'review_records':{'path':'review.json'}}}))
   (p/'review.json').write_text(json.dumps({'approval':{'status':'pending','expires_at':'2099-01-01T00:00:00+00:00'},'npm':{'dispositions':[]}}))
   with self.assertRaises(ValueError):m.review_expiry(p)
 def test_no_overwrite(self):
  with tempfile.TemporaryDirectory() as t,patch.object(m,'api') as api:
   with self.assertRaises(ValueError):m.prepare(Path(t),'signer',{})
   api.assert_not_called()
 def test_path_traversal(self):
  for name in ['../bad','/absolute','x/../../bad']:
   with self.assertRaises(ValueError):m.safe_path(Path('/tmp/root'),name)
 def test_zip_traversal(self):
  with tempfile.TemporaryDirectory() as t:
   a=Path(t)/'a.zip'
   with zipfile.ZipFile(a,'w') as z:z.writestr('../bad','bad')
   with self.assertRaises(ValueError):m.extract(a,Path(t)/'out')
 def test_wrong_run(self):
  with tempfile.TemporaryDirectory() as t,patch.object(m,'api',return_value={'head_sha':'bad','workflow_id':423868189,'conclusion':'success'}):
   with self.assertRaises(ValueError):m.prepare(Path(t)/'out','new',{})
 def test_wrong_image(self):
  with patch.object(m.producer,'targets',return_value=[{'name':'api'}]),patch.object(m.producer,'oci',return_value=('bad','config',b'')):
   with self.assertRaises(ValueError):m.verify_images(Path('/tmp/none'),{})
 def test_wrong_gate_source(self):
  with tempfile.TemporaryDirectory() as t,patch.object(m.producer,'verify_file'):
   p=Path(t);(p/'gate-input.json').write_text(json.dumps({'expected_images':m.IMAGES,'expected_source_revision':'bad'}))
   with self.assertRaises(ValueError):m.verify(p,'new',{})
 def test_stale_gate_blocks(self):
  with tempfile.TemporaryDirectory() as t,patch.object(m.producer,'verify_file'),patch.object(m,'evaluate',return_value={'decision':'NO-GO'}):
   p=Path(t);(p/'gate-input.json').write_text(json.dumps({'expected_images':m.IMAGES,'expected_source_revision':m.SOURCE}))
   with self.assertRaises(ValueError):m.verify(p,'new',{})
if __name__=='__main__':unittest.main(verbosity=2)
