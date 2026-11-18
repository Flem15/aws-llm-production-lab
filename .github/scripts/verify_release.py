#!/usr/bin/env python3
"""Verify registry artifact provenance with GitHub CLI; never deploy or publish."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPO='Flem15/aws-llm-production-lab'
IMAGE='655469962246.dkr.ecr.us-east-2.amazonaws.com/demo-inference-api'
WORKFLOW=REPO+'/.github/workflows/build-push-image.yml'
MIN_GH=(2,102,0)  # GHSA-wjmr-j3rp-mh2g exact signer-workflow fix


def resolve(document, revision):
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Invalid source revision')
    images=document['imageDetails']
    if len(images)!=1:raise ValueError('Exactly one registry image required')
    item=images[0];digest=item.get('imageDigest','')
    if (item.get('registryId')!='655469962246' or item.get('repositoryName')!='demo-inference-api'
            or revision not in item.get('imageTags',[]) or not re.fullmatch('sha256:[0-9a-f]{64}',digest)):
        raise ValueError('Registry identity, revision tag or digest mismatch')
    return digest


def verify(revision,digest,run=subprocess.run):
    if not re.fullmatch('[0-9a-f]{40}',revision) or not re.fullmatch('sha256:[0-9a-f]{64}',digest):
        raise ValueError('Invalid source revision or digest')
    version=run(['gh','--version'],check=True,capture_output=True,text=True,timeout=20).stdout
    match=re.search(r'gh version (\d+)\.(\d+)\.(\d+)(\s|$)',version)
    if not match or tuple(map(int,match.groups()[:3]))<MIN_GH:raise ValueError('GitHub CLI >=2.102.0 required for patched verification')
    command=['gh','attestation','verify','oci://'+IMAGE+'@'+digest,'--repo',REPO,
        '--cert-identity','https://github.com/'+WORKFLOW+'@refs/heads/main',
        '--cert-oidc-issuer','https://token.actions.githubusercontent.com','--source-digest',revision,
        '--signer-digest',revision,'--source-ref','refs/heads/main','--deny-self-hosted-runners',
        '--predicate-type','https://slsa.dev/provenance/v1','--format','json']
    raw=run(command,check=True,capture_output=True,text=True,timeout=180).stdout
    result=json.loads(raw)
    if not isinstance(result,list) or not result:raise ValueError('No verified attestations')
    return {'schema':1,'verified':True,'repository':REPO,'source_revision':revision,
        'image':IMAGE+'@'+digest,'digest':digest,'workflow':WORKFLOW,
        'verified_at':datetime.now(timezone.utc).isoformat(),'verifier_version':version.splitlines()[0],
        'verification_command':command,'raw_verification_sha256':hashlib.sha256(raw.encode()).hexdigest()},raw


def main():
    parser=argparse.ArgumentParser();parser.add_argument('revision');parser.add_argument('image_details',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--review',type=Path)
    args=parser.parse_args()
    # A failed attempt must not leave a new success record. Output must be new.
    if args.output.exists():raise ValueError('Refusing to reuse a verification record')
    digest=resolve(json.loads(args.image_details.read_text()),args.revision)
    if args.review:
        review=json.loads(args.review.read_text())
        if review.get('expected_source_revision')!=args.revision or review.get('expected_images',{}).get('api')!=digest:
            raise ValueError('Resolved registry artifact differs from approved release evidence')
    record,raw=verify(args.revision,digest)
    args.output.with_suffix('.raw.json').write_text(raw)
    args.output.write_text(json.dumps(record,indent=2)+'\n')
    print(digest)

if __name__=='__main__':main()
