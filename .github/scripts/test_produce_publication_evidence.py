import copy
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import produce_publication_evidence as p
from predeployment_gate import REQUIRED
from publication_evidence import CONTEXTS

class ProducerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.bundle=self.root/'bundle';self.bundle.mkdir();self.review=self.root/'review';self.review.mkdir()
  self.revision='a'*40;self.now=datetime.now(timezone.utc).isoformat()
  self.config={'schema':1,'evidence_workflow':'.github/workflows/produce-publication-evidence.yml','images':[{'name':n,'repository':{'api':'demo-inference-api','bot-city':'bot-city-dev','proxy':'bot-city-proxy-dev'}[n],'context':c} for n,c in CONTEXTS.items()]}
  for name in CONTEXTS:self.image(name)
  cloud={'account':'655469962246','region':'us-east-2','checked_at':self.now,'release_chain_removed':True,'github_authorization_active':True}
  (self.review/'cloud.json').write_text(json.dumps(cloud))
  review={'schema':1,'account':'655469962246','region':'us-east-2','collected_at':self.now,'approved_actions':['publication'],'evidence':{'cloud_controls':{'path':'cloud.json','sha256':p.sha(self.review/'cloud.json')}},'checks':{k:{'status':'pass','evidence':['cloud_controls']} for k in REQUIRED},'findings':[]}
  (self.review/'gate-input.json').write_text(json.dumps(review))
  names=['Application and Docker','CDK Build and Synthesis','Restricted Egress Security and Docker','Bot City Safety and Docker','Release Image Security']
  self.checks={'total_count':5,'check_runs':[{'name':n,'head_sha':self.revision,'status':'completed','conclusion':'success','app':{'id':15368}} for n in names]}
 def image(self,name):
  out=self.bundle/name;out.mkdir(exist_ok=True);members={}
  def blob(raw):
   digest='sha256:'+hashlib.sha256(raw).hexdigest();members['blobs/sha256/'+digest[7:]]=raw;return {'digest':digest,'size':len(raw)}
  config=blob(json.dumps({'os':'linux','architecture':'amd64','config':{'Labels':{'org.opencontainers.image.revision':self.revision}}}).encode())
  manifest=json.dumps({'schemaVersion':2,'config':config,'layers':[blob(b'layer-'+name.encode())]}).encode();desc=blob(manifest);desc['mediaType']='application/vnd.oci.image.manifest.v1+json'
  members.update({'index.json':json.dumps({'schemaVersion':2,'manifests':[desc]}).encode(),'oci-layout':b'{"imageLayoutVersion":"1.0.0"}'})
  with tarfile.open(out/'image.tar','w') as t:
   for n,raw in members.items():
    info=tarfile.TarInfo(n);info.size=len(raw);t.addfile(info,io.BytesIO(raw))
  (out/'manifest.json').write_bytes(manifest)
  (out/'scan.json').write_text(json.dumps({'ArtifactType':'container_image','CreatedAt':self.now,'Metadata':{'ImageID':config['digest'],'OS':{'Family':'alpine'}},'Results':[{'Class':'os-pkgs','Packages':[{'Name':'zlib'}]}]}))
  (out/'database.json').write_text(json.dumps({'UpdatedAt':self.now}))
 def assemble(self):
  with patch.object(p,'verify_file',return_value='[{"verificationResult":{}}]'),patch.object(p,'api',return_value=self.checks):p.assemble(self.bundle,self.review,self.revision,self.config)
 def test_complete_bundle_generation_and_verification(self):
  self.assemble()
  with patch.object(p,'verify_file',return_value='[{"verificationResult":{}}]') as verify:
   result=p.verify_bundle(self.bundle,self.revision,self.config);self.assertEqual(len(result['expected_images']),3);self.assertEqual(verify.call_count,4)
   self.assertEqual(result['approved_actions'],['publication'])
   self.assertIn('bot-city/image.tar',result['evidence'])
 def test_wrong_source_rejected(self):
  with self.assertRaises(ValueError):p.oci(self.bundle/'api/image.tar','b'*40)
 def test_corrupt_layer_rejected(self):
  f=self.bundle/'api/image.tar';raw=f.read_bytes();f.write_bytes(raw.replace(b'layer-api',b'layer-bad'))
  with self.assertRaises(ValueError):p.oci(f,self.revision)
 def test_wrong_manifest_rejected(self):
  (self.bundle/'api/manifest.json').write_text('{}')
  with self.assertRaises(ValueError):self.assemble()
 def test_wrong_scan_identity_rejected(self):
  f=self.bundle/'api/scan.json';d=json.loads(f.read_text());d['Metadata']['ImageID']='sha256:'+'b'*64;f.write_text(json.dumps(d))
  with self.assertRaises(ValueError):self.assemble()
 def test_bad_scans_rejected(self):
  f=self.bundle/'api/scan.json';original=f.read_text()
  for finding in [{'Vulnerabilities':[{'Severity':'HIGH'}]},{'Secrets':[{}]}]:
   d=json.loads(original);d['Results'][0].update(finding);f.write_text(json.dumps(d))
   with self.assertRaises(ValueError):self.assemble()
 def test_stale_database_rejected(self):
  (self.bundle/'api/database.json').write_text(json.dumps({'UpdatedAt':(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()}))
  with self.assertRaises(ValueError):self.assemble()
 def test_failed_signature_blocks_generation(self):
  with patch.object(p,'verify_file',side_effect=subprocess.CalledProcessError(1,['gh'])),self.assertRaises(subprocess.CalledProcessError):p.assemble(self.bundle,self.review,self.revision,self.config)
  self.assertFalse((self.bundle/'gate-input.json').exists())
 def test_missing_approval_blocks_generation(self):
  f=self.review/'gate-input.json';d=json.loads(f.read_text());d['checks']['change_approval']['status']='pending';f.write_text(json.dumps(d))
  with self.assertRaises(ValueError):self.assemble()
 def test_tampering_after_signature_rejected(self):
  self.assemble();(self.bundle/'proxy/image.tar').write_bytes(b'tampered')
  with patch.object(p,'verify_file',return_value='[{}]'),self.assertRaises(ValueError):p.verify_bundle(self.bundle,self.revision,self.config)
 def test_crypto_command_identity_source_and_empty_output(self):
  calls=[]
  def run(args,**kw):calls.append(args);return SimpleNamespace(stdout='gh version 2.104.0\n' if args==['gh','--version'] else '[{}]')
  p.verify_file(self.bundle/'api/manifest.json',self.revision,run)
  cmd=calls[-1]
  self.assertIn('https://github.com/'+p.WORKFLOW+'@refs/heads/main',cmd);self.assertIn('--deny-self-hosted-runners',cmd)
  self.assertEqual(cmd[cmd.index('--source-digest')+1],self.revision)
  with self.assertRaises(ValueError):p.verify_file('file',self.revision,lambda args,**kw:SimpleNamespace(stdout='gh version 2.104.0\n' if args==['gh','--version'] else '[]'))
 def test_missing_image_blocks_generation(self):
  (self.bundle/'proxy/image.tar').unlink()
  with self.assertRaises(FileNotFoundError):self.assemble()

if __name__=='__main__':unittest.main()
