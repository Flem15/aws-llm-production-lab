#!/usr/bin/env python3
"""Scan the exact local image before publication; never publish or deploy."""
import collections,json,os,re,subprocess,sys,tempfile
from pathlib import Path
SCANNER='aquasec/trivy@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e'

def validate(report):
    if report.get('ArtifactType')!='container_image' or not report.get('Results'):
        raise ValueError('Incomplete container scan')
    if report.get('Metadata',{}).get('OS',{}).get('Family') not in {'alpine','debian'} or not any(r.get('Class')=='os-pkgs' and r.get('Packages') for r in report['Results']):
        raise ValueError('Expected supported OS package inventory missing')
    counts=collections.Counter(v['Severity'] for r in report['Results'] for v in r.get('Vulnerabilities',[]))
    if set(counts)-{'CRITICAL','HIGH','MEDIUM','LOW','UNKNOWN'} or any(counts[k] for k in ['CRITICAL','HIGH','UNKNOWN']):
        raise ValueError('Blocking vulnerability findings')
    if any(r.get('Secrets') for r in report['Results']):raise ValueError('Embedded secret findings')
    if report.get('Metadata',{}).get('OS',{}).get('EOSL') is True:raise ValueError('Unsupported OS')
    return dict(counts)

def main():
    image,output=sys.argv[1:];out=Path(output).resolve();out.mkdir(parents=True,exist_ok=True)
    identity=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
    if not re.fullmatch('sha256:[0-9a-f]{64}',identity):raise ValueError('Image identity missing')
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'reports').mkdir();(root/'cache').mkdir()
        subprocess.run(['docker','save','-o',str(root/'image.tar'),identity],check=True)
        subprocess.run(['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
            '--env','TRIVY_CACHE_DIR=/cache','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=512m',
            '-v',str(root/'image.tar')+':/image.tar:ro','-v',str(root/'reports')+':/reports',
            '-v',str(root/'cache')+':/cache',SCANNER,'image','--input','/image.tar',
            '--ignorefile','/dev/null','--scanners','vuln,secret','--list-all-pkgs',
            '--format','json','--output','/reports/scan.json','--timeout','15m'],check=True,timeout=1000)
        raw=(root/'reports/scan.json').read_text();report=json.loads(raw)
        (out/'publication-scan.json').write_text(raw)
        counts=validate(report)
        (out/'publication-scan-summary.json').write_text(json.dumps({'image_id':identity,'counts':counts,'scanner':SCANNER},indent=2))
if __name__=='__main__':main()
