"""Evidence-only renewal of fixed existing images. Never builds or pushes images."""
import argparse,hashlib,json,os,shutil,subprocess,tempfile,zipfile
from pathlib import Path
import produce_publication_evidence as producer
from predeployment_gate import evaluate

REPO='Flem15/aws-llm-production-lab'
SOURCE='5cdba2f8550334799c847f75a4572d6c28769768'
RUN=44670971252
ARTIFACT=14600775427
ARCHIVE_SHA='76ad2e9c3806d5c321f219fbe2db83cdf0fcf86261a33223fd8db5b9f1d34755'
IMAGES={'api':'sha256:861196575034027812fbcdf1f724c73d167ff26dd2212d6d529398537c99fad8','bot-city':'sha256:f8b1368f39b88a2f4893ac9d32bd71d1d605d6ebb4e7cbe121bc94222f5d56fc','proxy':'sha256:5b3edd720e8ebce17e2d63167512301e8620e6c6d8ea4b21dd1cfb78dc52223c'}

def require(ok,why):
 if not ok:raise ValueError(why)
def api(path):return json.loads(subprocess.check_output(['gh','api','repos/'+REPO+'/'+path]))
def safe_path(root,name):
 p=Path(name);require(not p.is_absolute() and '..' not in p.parts,'Unsafe evidence path')
 target=(root/p).resolve();require(target.is_relative_to(root.resolve()),'Evidence path escaped root');return target
def extract(archive,root):
 with zipfile.ZipFile(archive) as z:
  names=set()
  for member in z.infolist():
   require(member.filename not in names,'Duplicate archive member');names.add(member.filename)
   safe_path(root,member.filename)
   kind=(member.external_attr>>16)&0o170000
   require(kind in (0,0o100000,0o040000),'Nonregular archive member')
  z.extractall(root)
def verify_images(root,config):
 for target in producer.targets(config):
  p=root/target['name'];digest,_,raw=producer.oci(p/'image.tar',SOURCE)
  require(digest==IMAGES[target['name']] and raw==(p/'manifest.json').read_bytes(),'Image changed')
  producer.verify_file(p/'manifest.json',SOURCE)
def verify(root,signer_revision,config):
 # New evidence signer revision is distinct from original image/build source revision.
 producer.verify_file(root/'gate-input.json',signer_revision)
 gate=json.loads((root/'gate-input.json').read_text())
 require(gate['expected_images']==IMAGES and gate['expected_source_revision']==SOURCE,'Source/digest binding changed')
 require(evaluate(gate,root,stage='publication',source_revision=SOURCE)['decision']=='GO','Fresh evidence gate rejected renewal')
 review_expiry(root)
 verify_images(root,config)
 # Revalidate raw scan config IDs, vulnerability/secret results, database and scan freshness.
 for name in IMAGES:
  p=root/name;_,config_id,_=producer.oci(p/'image.tar',SOURCE)
  scan=json.loads((p/'scan.json').read_text());producer.validate_scan(scan)
  require(scan['Metadata']['ImageID']==config_id,'Scan config mismatch')
  now=producer.datetime.now(producer.timezone.utc)
  producer.fresh(scan['CreatedAt'],24,now)
  producer.fresh(json.loads((p/'database.json').read_text())['UpdatedAt'],48,now)
 require(json.loads((root/'renewal.json').read_text())==binding(signer_revision),'Renewal identity mismatch')
def review_expiry(root):
 gate=json.loads((root/'gate-input.json').read_text())
 ref=gate['evidence']['review_records']
 record=json.loads(safe_path(root,ref['path']).read_text())
 now=producer.datetime.now(producer.timezone.utc)
 deadlines=[record['approval']['expires_at']]+[d['expires_at'] for d in record['npm']['dispositions']]
 for value in deadlines:
  until=producer.datetime.fromisoformat(value)
  require(until.tzinfo is not None and now<until,'Review approval or risk disposition expired')
 require(record['approval']['status']=='approved_for_evidence_preparation_only','Review approval absent')

def binding(signer):return {'schema':1,'kind':'evidence-renewal-not-image-build','image_source_revision':SOURCE,'evidence_signer_revision':signer,'original_producer_run':RUN,'original_artifact_id':ARTIFACT,'original_archive_sha256':ARCHIVE_SHA,'images':IMAGES}
def prepare(root,signer_revision,config):
 require(not root.exists(),'Never overwrite a bundle')
 run=api('actions/runs/'+str(RUN));require(run['head_sha']==SOURCE and run['workflow_id']==423868189 and run['conclusion']=='success','Original producer mismatch')
 artifact=api('actions/artifacts/'+str(ARTIFACT));require(not artifact['expired'] and artifact['name']=='approved-release-evidence' and artifact['digest']=='sha256:'+ARCHIVE_SHA,'Original artifact mismatch/expired')
 with tempfile.TemporaryDirectory() as t:
  archive=Path(t)/'original.zip'
  with archive.open('wb') as out:subprocess.run(['gh','api','repos/'+REPO+'/actions/artifacts/'+str(ARTIFACT)+'/zip'],stdout=out,check=True,timeout=300)
  require(producer.sha(archive)==ARCHIVE_SHA,'Archive checksum mismatch')
  root.mkdir();extract(archive,root)
 # Historical signature remains valid; historical freshness is NOT treated as current approval.
 producer.verify_file(root/'gate-input.json',SOURCE)
 old=json.loads((root/'gate-input.json').read_text());require(old['expected_images']==IMAGES and old['expected_source_revision']==SOURCE,'Original evidence binding mismatch')
 for evidence in old['evidence'].values():require(producer.sha(safe_path(root,evidence['path']))==evidence['sha256'],'Original evidence content mismatch')
 verify_images(root,config)
 # Keep the historical signed manifest; assemble refuses to overwrite any gate.
 (root/'gate-input.json').rename(root/'original-gate-input.json')
 for name in IMAGES:producer.scan_archive(root/name/'image.tar',root/name)
 (root/'renewal.json').write_text(json.dumps(binding(signer_revision),indent=2)+'\n')
 # Refresh observations/approvals only from separately reviewed records promoted to main.
 producer.assemble(root,Path('docs/publication-review'),SOURCE,config)
 require(json.loads((root/'gate-input.json').read_text())['expected_images']==IMAGES,'Renewal changed image identity')
 review_expiry(root)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','verify']);p.add_argument('signer_revision');p.add_argument('root',type=Path);a=p.parse_args()
 require(a.signer_revision==os.environ['GITHUB_SHA'] and os.environ.get('GITHUB_REF')=='refs/heads/main','Trusted main revision required')
 config=json.loads(Path('.github/publication-targets.json').read_text())
 {'prepare':prepare,'verify':verify}[a.action](a.root,a.signer_revision,config)
