"""Authenticate a manually approved evidence archive before extracting it. No AWS calls."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

REPO = 'Flem15/aws-llm-production-lab'
REPOSITORIES = {'api':'demo-inference-api','bot-city':'bot-city-dev','proxy':'bot-city-proxy-dev'}
CONTEXTS = {'api': 'app/demo-inference-api', 'bot-city': 'app/bot-city', 'proxy': 'app/egress-proxy'}

def targets(config):
    images = config.get('images', [])
    if config.get('schema') != 1 or len(images) != 3 or {i['name'] for i in images} != set(CONTEXTS):
        raise ValueError('Exactly three reviewed publication targets required')
    repositories = []
    for image in images:
        repository = image.get('repository')
        if not isinstance(repository, str) or not re.fullmatch(r'[a-z0-9]+(?:[._/-][a-z0-9]+)*', repository):
            raise ValueError('Publication repository has not been approved')
        if image.get('context') != CONTEXTS[image['name']]:
            raise ValueError('Build context mismatch')
        if repository != REPOSITORIES[image['name']]:
            raise ValueError('Reviewed repository mismatch')
        repositories.append(repository)
    if len(set(repositories)) != 3:
        raise ValueError('Image repositories must be distinct')
    workflow = config.get('evidence_workflow')
    if not isinstance(workflow, str) or not re.fullmatch(r'\.github/workflows/[a-z0-9_-]+\.yml', workflow):
        raise ValueError('Trusted evidence producer has not been approved')
    return images

def validate(run, artifacts, revision, config, expected_hash, now=None):
    targets(config)
    now = now or datetime.now(timezone.utc)
    if not re.fullmatch('[0-9a-f]{40}', revision) or not re.fullmatch('[0-9a-f]{64}', expected_hash):
        raise ValueError('Exact source and reviewed artifact SHA256 required')
    for key, expected in {'head_sha': revision, 'head_branch': 'main', 'event': 'workflow_dispatch',
                          'status': 'completed', 'conclusion': 'success', 'path': config['evidence_workflow']}.items():
        if run.get(key) != expected:
            raise ValueError('Untrusted evidence run: '+key)
    if run.get('repository', {}).get('full_name') != REPO or run.get('head_repository', {}).get('full_name') != REPO:
        raise ValueError('Evidence repository mismatch')
    for key in ('actor', 'triggering_actor'):
        if run.get(key, {}).get('login') != 'Flem15' or run.get(key, {}).get('id') != 127445894:
            raise ValueError('Unauthorized evidence actor')
    if run.get('run_attempt') != 1:
        raise ValueError('Rerun evidence requires a new reviewed run')
    at = datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
    if at.tzinfo is None or not -timedelta(minutes=5) <= now-at <= timedelta(hours=24):
        raise ValueError('Evidence run is stale or future dated')
    if artifacts.get('total_count') != len(artifacts.get('artifacts', [])):
        raise ValueError('Incomplete artifact enumeration')
    found = [a for a in artifacts['artifacts'] if a.get('name') == 'approved-release-evidence']
    if len(found) != 1:
        raise ValueError('Exactly one approved evidence artifact required')
    artifact = found[0]
    if artifact.get('expired') is not False or artifact.get('digest') != 'sha256:'+expected_hash:
        raise ValueError('Artifact is expired or differs from manual approval')
    if artifact.get('workflow_run', {}).get('id') != run.get('id'):
        raise ValueError('Artifact run mismatch')
    if not isinstance(artifact.get('id'), int) or artifact['id'] <= 0:
        raise ValueError('Invalid artifact ID')
    return artifact['id']

def validate_checks(document, revision):
    required={'Application and Docker','CDK Build and Synthesis','Restricted Egress Security and Docker','Bot City Safety and Docker','Release Image Security'}
    checks=document.get('check_runs',[])
    if document.get('total_count') != len(checks):
        raise ValueError('Incomplete required-check enumeration')
    for name in required:
        matches=[c for c in checks if c.get('name') == name and c.get('app',{}).get('id') == 15368]
        if len(matches) != 1 or matches[0].get('head_sha') != revision or matches[0].get('status') != 'completed' or matches[0].get('conclusion') != 'success':
            raise ValueError('Required GitHub Actions check is not verified: '+name)


def extract(raw, digest, destination):
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Downloaded evidence does not match approved bytes')
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Evidence destination must be new')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or sum(i.file_size for i in infos) > 3_000_000_000:
            raise ValueError('Ambiguous or oversized evidence archive')
        for i in infos:
            path = Path(i.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in i.filename or (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe archive member')
        if 'gate-input.json' not in names:
            raise ValueError('Missing evidence manifest')
        destination.mkdir()
        archive.extractall(destination)

def api(path, binary=False):
    result = subprocess.run(['gh', 'api', 'repos/'+REPO+'/'+path], check=True, capture_output=True, timeout=120)
    return result.stdout if binary else json.loads(result.stdout)

def main():
    p=argparse.ArgumentParser();p.add_argument('revision');p.add_argument('run_id');p.add_argument('sha256');p.add_argument('destination');p.add_argument('--config',default='.github/publication-targets.json')
    a=p.parse_args()
    if not re.fullmatch('[1-9][0-9]*',a.run_id):raise ValueError('Invalid run ID')
    config=json.loads(Path(a.config).read_text())
    targets(config)  # No network call while prerequisites are unset.
    validate_checks(api('commits/'+a.revision+'/check-runs?per_page=100'),a.revision)
    run=api('actions/runs/'+a.run_id)
    if run.get('id') != int(a.run_id):raise ValueError('Run ID mismatch')
    artifacts=api('actions/runs/'+a.run_id+'/artifacts?per_page=100')
    artifact_id=validate(run,artifacts,a.revision,config,a.sha256)
    extract(api('actions/artifacts/'+str(artifact_id)+'/zip',binary=True),a.sha256,a.destination)

if __name__ == '__main__':main()
