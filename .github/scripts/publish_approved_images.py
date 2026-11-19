"""Authorized workflow entry point only. Upload approved OCI archives without rebuilding."""
import json
import os
from pathlib import Path
import subprocess
import sys
from produce_publication_evidence import REGISTRY, verify_bundle, oci
from publication_evidence import targets
from verify_release import resolve, verify

def publish(revision,root,output,run=subprocess.run):
    root=Path(root);output=Path(output)
    config=json.loads(Path('.github/publication-targets.json').read_text())
    approved=verify_bundle(root,revision,config)  # Fails before any ECR mutation.
    output.mkdir()
    results=[]; planned=[]
    # Inspect every destination before the first upload; missing repositories and denied
    # permissions must not cause a predictable partial publication.
    for target in targets(config):
        name=target['name'];repository=target['repository'];digest=approved['expected_images'][name]
        describe=['aws','ecr','describe-images','--region','us-east-2','--repository-name',repository,'--image-ids','imageTag='+revision,'--output','json']
        result=run(describe,capture_output=True,text=True,timeout=60)
        if result.returncode:
            if 'An error occurred (ImageNotFoundException)' not in result.stderr:raise RuntimeError('ECR lookup failed: publication stopped')
        else:
            if resolve(json.loads(result.stdout),revision,name,repository)!=digest:raise ValueError('Immutable ECR tag differs from approved digest; never overwrite')
            verify(revision,digest,name=name,repository=repository)
        planned.append((target,describe,result))
    for target,describe,result in planned:
        name=target['name'];repository=target['repository'];digest=approved['expected_images'][name]
        if result.returncode:
            actual,_,_=oci(root/name/'image.tar',revision)
            if actual!=digest:raise ValueError('Archive changed before publication')
            run(['skopeo','copy','--preserve-digests','--dest-authfile',str(Path.home()/'.docker/config.json'),
                 'oci-archive:'+str(root/name/'image.tar'),'docker://'+REGISTRY+'/'+repository+':'+revision],check=True,timeout=1200)
            result=run(describe,check=True,capture_output=True,text=True,timeout=60)
        details=json.loads(result.stdout)
        if resolve(details,revision,name,repository)!=digest:raise ValueError('Immutable ECR tag differs from approved digest; never overwrite')
        receipt,raw=verify(revision,digest,name=name,repository=repository)
        p=output/name;p.mkdir();(p/'image-details.json').write_text(json.dumps(details,indent=2)+'\n')
        (p/'provenance.json').write_text(json.dumps(receipt,indent=2)+'\n');(p/'provenance.raw.json').write_text(raw)
        results.append({'name':name,'image':receipt['image'],'source_revision':revision})
    (output/'images.json').write_text(json.dumps({'images':results},indent=2)+'\n')

if __name__=='__main__':publish(*sys.argv[1:])
