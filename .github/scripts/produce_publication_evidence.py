"""Produce/verify exact OCI build evidence. Local operations only; never pushes images."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from publication_evidence import targets, validate_checks, api
from scan_release_image import SCANNER, validate as validate_scan
from predeployment_gate import evaluate
from verify_release import MIN_GH, REPO

WORKFLOW=REPO+'/.github/workflows/produce-publication-evidence.yml'
REGISTRY='655469962246.dkr.ecr.us-east-2.amazonaws.com'

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def fresh(value,hours,now):
    at=datetime.fromisoformat(value.replace('Z','+00:00'))
    if at.tzinfo is None or not -timedelta(minutes=5)<=now-at<=timedelta(hours=hours):
        raise ValueError('Stale or future-dated evidence')

def oci(archive,revision):
    """Validate content addressing from the index through every config/layer blob."""
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Exact source SHA required')
    with tarfile.open(archive,'r:*') as t:
        members=t.getmembers(); names=[m.name for m in members]
        if len(names)!=len(set(names)):raise ValueError('Duplicate OCI entries')
        for m in members:
            if Path(m.name).is_absolute() or '..' in Path(m.name).parts or not (m.isfile() or m.isdir()):raise ValueError('Unsafe OCI entry')
        def read(name):
            member=t.getmember(name)
            if not member.isfile() or member.size>8*1024*1024:raise ValueError('Oversized OCI metadata')
            return t.extractfile(member).read()
        def blob(desc,metadata=False):
            digest=desc['digest']
            if not re.fullmatch('sha256:[0-9a-f]{64}',digest):raise ValueError('Invalid OCI digest')
            name='blobs/sha256/'+digest[7:];member=t.getmember(name)
            if member.size!=desc['size']:raise ValueError('OCI blob size mismatch')
            h=hashlib.sha256()
            with t.extractfile(member) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            if h.hexdigest()!=digest[7:]:raise ValueError('OCI content digest mismatch')
            return read(name) if metadata else None
        if json.loads(read('oci-layout'))!={'imageLayoutVersion':'1.0.0'}:raise ValueError('Invalid OCI layout')
        index=json.loads(read('index.json'))
        if index.get('schemaVersion')!=2 or len(index.get('manifests',[]))!=1:raise ValueError('Exactly one platform manifest required')
        desc=index['manifests'][0]
        if desc['mediaType']!='application/vnd.oci.image.manifest.v1+json':raise ValueError('OCI image manifest required')
        raw=blob(desc,True);manifest=json.loads(raw)
        config=json.loads(blob(manifest['config'],True))
        if config.get('os')!='linux' or config.get('architecture')!='amd64':raise ValueError('Linux amd64 required')
        if config.get('config',{}).get('Labels',{}).get('org.opencontainers.image.revision')!=revision:raise ValueError('Image source label mismatch')
        if not manifest.get('layers'):raise ValueError('Missing image layers')
        for layer in manifest['layers']:blob(layer)
        return desc['digest'],manifest['config']['digest'],raw

def verify_file(path,revision,run=subprocess.run):
    version=run(['gh','--version'],check=True,capture_output=True,text=True,timeout=20).stdout
    match=re.search(r'gh version (\d+)\.(\d+)\.(\d+)(\s|$)',version)
    if not match or tuple(map(int,match.groups()[:3]))<MIN_GH:raise ValueError('Patched gh >=2.102.0 required')
    args=['gh','attestation','verify',str(path),'--repo',REPO,'--cert-identity','https://github.com/'+WORKFLOW+'@refs/heads/main',
          '--cert-oidc-issuer','https://token.actions.githubusercontent.com','--source-digest',revision,'--signer-digest',revision,
          '--source-ref','refs/heads/main','--deny-self-hosted-runners','--predicate-type','https://slsa.dev/provenance/v1','--format','json']
    raw=run(args,check=True,capture_output=True,text=True,timeout=180).stdout
    if not isinstance(json.loads(raw),list) or not json.loads(raw):raise ValueError('No cryptographically verified attestations')
    return raw

def scan_archive(archive,out):
    out=Path(out).resolve();out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cache=Path(tmp)/'cache';cache.mkdir()
        layout=Path(tmp)/'layout';layout.mkdir()
        with tarfile.open(archive,'r:*') as tar:
            for member in tar.getmembers():
                if Path(member.name).is_absolute() or '..' in Path(member.name).parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe OCI layout')
            tar.extractall(layout,filter='data')
        subprocess.run(['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
          '--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=512m','--env','TRIVY_CACHE_DIR=/cache',
          '-v',str(layout)+':/image:ro','-v',str(out)+':/reports','-v',str(cache)+':/cache',SCANNER,
          'image','--input','/image','--ignorefile','/dev/null','--scanners','vuln,secret','--list-all-pkgs',
          '--format','json','--output','/reports/scan.json','--timeout','15m'],check=True,timeout=1000)
        validate_scan(json.loads((out/'scan.json').read_text()))
        shutil.copy2(cache/'db/metadata.json',out/'database.json')

def inventory(root,revision,config,now=None):
    now=now or datetime.now(timezone.utc);root=Path(root)
    images=[];bindings=[];databases=[]
    for target in targets(config):
        name=target['name'];p=root/name
        digest,config_id,raw=oci(p/'image.tar',revision)
        if (p/'manifest.json').read_bytes()!=raw:raise ValueError('Manifest substitution')
        scan=json.loads((p/'scan.json').read_text());validate_scan(scan)
        if scan.get('Metadata',{}).get('ImageID')!=config_id:raise ValueError('Scan config digest mismatch')
        fresh(scan['CreatedAt'],24,now)
        database=json.loads((p/'database.json').read_text());fresh(database['UpdatedAt'],48,now);databases.append(database)
        verified=verify_file(p/'manifest.json',revision)
        (p/'provenance.raw.json').write_text(verified)
        record={'schema':1,'verified':True,'repository':REPO,'source_revision':revision,'workflow':WORKFLOW,
                'image':REGISTRY+'/'+target['repository']+'@'+digest,'digest':digest,'verified_at':now.isoformat(),
                'raw_verification_sha256':hashlib.sha256(verified.encode()).hexdigest()}
        (p/'provenance.json').write_text(json.dumps(record,indent=2)+'\n')
        counts=Counter(v['Severity'] for r in scan['Results'] for v in r.get('Vulnerabilities',[]))
        images.append({'name':name,'id':digest,'config_id':config_id,'scan_exit_code':0,'scan_complete':True,
          'scanned_at':scan['CreatedAt'],'secret_findings':0,'vulnerabilities':{k:counts[k] for k in ['CRITICAL','HIGH','MEDIUM','LOW','UNKNOWN']},'raw_scan_evidence':name+'/scan.json'})
        bindings.append({'name':name,'verification_evidence':name+'/provenance.json','raw_verification_evidence':name+'/provenance.raw.json'})
    return {'images':images,'database':min(databases,key=lambda d:d['UpdatedAt'])},{'artifacts':bindings}

def assemble(root,review,revision,config):
    root=Path(root);review=Path(review)
    if (root/'gate-input.json').exists():raise ValueError('Output evidence already exists')
    # Review records are protected source; no user-supplied runtime URL or untrusted artifact.
    data=json.loads((review/'gate-input.json').read_text())
    if data.get('approved_actions')!=['publication']:raise ValueError('Publication-only review required')
    evidence={}
    for key,ref in data['evidence'].items():
        if Path(ref['path']).is_absolute() or '..' in Path(ref['path']).parts:raise ValueError('Unsafe review path')
        source=(review/ref['path']).resolve()
        if not source.is_relative_to(review.resolve()) or not source.is_file() or sha(source)!=ref['sha256']:raise ValueError('Review evidence hash or path mismatch')
        target=root/'review'/ref['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        evidence[key]={'path':str(target.relative_to(root)),'sha256':sha(target)}
    images,provenance=inventory(root,revision,config)
    checks=api('commits/'+revision+'/check-runs?per_page=100');validate_checks(checks,revision)
    (root/'required-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
    for name,value in [('images.json',images),('provenance.json',provenance)]: (root/name).write_text(json.dumps(value,indent=2)+'\n')
    # Every archive, manifest, raw scan, DB and verification receipt is signed transitively.
    for path in sorted(root.rglob('*')):
        if path.is_file():
            name=str(path.relative_to(root))
            if name.startswith('review/'):continue
            if name in evidence:raise ValueError('Reserved evidence key collision')
            evidence[name]={'path':name,'sha256':sha(path)}
    evidence['images']=evidence['images.json'];evidence['release_provenance']=evidence['provenance.json']
    data.update(expected_source_revision=revision,expected_images={i['name']:i['id'] for i in images['images']},evidence=evidence)
    data['checks']['image_provenance']={'status':'pass','evidence':['release_provenance']}
    data['checks']['local_validation']={'status':'pass','evidence':['required-checks.json','images']}
    result=evaluate(data,root,stage='publication',source_revision=revision)
    if result['decision']!='GO':raise ValueError(json.dumps(result))
    (root/'gate-input.json').write_text(json.dumps(data,indent=2)+'\n')

def verify_bundle(root,revision,config):
    root=Path(root)
    verify_file(root/'gate-input.json',revision)
    data=json.loads((root/'gate-input.json').read_text())
    result=evaluate(data,root,stage='publication',source_revision=revision)
    if result['decision']!='GO':raise ValueError(json.dumps(result))
    for target in targets(config):
        p=root/target['name'];digest,config_id,raw=oci(p/'image.tar',revision)
        if digest!=data['expected_images'].get(target['name']) or raw!=(p/'manifest.json').read_bytes():raise ValueError('Approved image digest mismatch')
        scan=json.loads((p/'scan.json').read_text());validate_scan(scan)
        if scan['Metadata']['ImageID']!=config_id:raise ValueError('Approved scan config mismatch')
        verify_file(p/'manifest.json',revision)
    return data

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build','assemble','verify']);p.add_argument('revision');p.add_argument('output',type=Path)
    p.add_argument('--review',type=Path,default=Path('docs/publication-review'));p.add_argument('--builder',default='publication');a=p.parse_args()
    config=json.loads(Path('.github/publication-targets.json').read_text());images=targets(config)
    if not re.fullmatch('[0-9a-f]{40}',a.revision):raise ValueError('Exact source SHA required')
    if a.action=='build':
        a.output.mkdir()  # Never reuse an earlier build's files.
        for image in images:
            out=a.output/image['name'];out.mkdir()
            subprocess.run(['docker','buildx','build','--builder',a.builder,'--platform','linux/amd64','--provenance=false',
              '--label','org.opencontainers.image.revision='+a.revision,'--output','type=oci,dest='+str(out/'image.tar'),image['context']],check=True)
            digest,config_id,raw=oci(out/'image.tar',a.revision);(out/'manifest.json').write_bytes(raw)
            scan_archive(out/'image.tar',out)
    elif a.action=='assemble':assemble(a.output,a.review,a.revision,config)
    else:verify_bundle(a.output,a.revision,config)

if __name__=='__main__':main()
