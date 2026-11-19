#!/usr/bin/env python3
"""Offline evidence gate. Never calls AWS or authorizes a deployment."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

REQUIRED = frozenset({'iam_chain', 'github_controls', 'image_provenance', 'template_review',
    'drift_review', 'secret_delivery', 'recovery_controls', 'network_validation',
    'alarm_delivery', 'npm_disposition', 'rollback_plan', 'local_validation', 'change_approval'})
IMAGE_NAMES = {'bot-city', 'proxy', 'api'}


def evaluate(data, root, now=None, stage="deployment", source_revision=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    try:
        if data['schema'] != 1 or data['account'] != '655469962246' or data['region'] != 'us-east-2':
            reasons.append('wrong schema, account or region')
        at = datetime.fromisoformat(data['collected_at'])
        if at.tzinfo is None or now - at > timedelta(hours=24) or at - now > timedelta(minutes=5):
            reasons.append('evidence timestamp is missing timezone, stale or in the future')
        if stage not in {'publication','deployment'}:
            reasons.append('unknown review stage')
        if stage not in data.get('approved_actions', []):
            reasons.append('requested action lacks explicit scoped approval')
        revision = data.get('expected_source_revision')
        if not re.fullmatch(r'[0-9a-f]{40}', str(revision)) or (source_revision is not None and revision != source_revision):
            reasons.append('approved source revision does not match requested release')
        evidence = data['evidence']
        if not evidence:
            reasons.append('no evidence files')
        verified = {}
        root = Path(root).resolve()
        for name, record in evidence.items():
            path = (root / record['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                reasons.append(f'missing or unsafe evidence path: {name}')
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != record['sha256']:
                reasons.append(f'evidence hash mismatch: {name}')
                continue
            verified[name] = raw
        checks = data['checks']
        if not isinstance(checks, dict) or set(checks) != REQUIRED:
            reasons.append('required review checks are missing or unexpected')
        required = REQUIRED if stage == "deployment" else {"iam_chain","github_controls","npm_disposition","local_validation","change_approval","image_provenance"}
        for name in required:
            check = checks.get(name, {})
            if check.get('status') != 'pass':
                reasons.append(f'check not passed: {name}')
            refs = check.get('evidence', [])
            if not refs or any(ref not in verified for ref in refs):
                reasons.append(f'check lacks verified evidence: {name}')
        if stage in {"publication", "deployment"}:
            provenance = json.loads(verified['release_provenance'])
            revision = data['expected_source_revision']
            if not re.fullmatch(r'[0-9a-f]{40}', str(revision)):
                reasons.append('approved exact source revision missing')
            bindings = provenance.get('artifacts', [])
            if len(bindings) != 3 or {p.get('name') for p in bindings} != IMAGE_NAMES:
                reasons.append('signed provenance required for all release images')
            for binding in bindings:
                record = json.loads(verified[binding['verification_evidence']])
                if (record.get('verified') is not True or record.get('source_revision') != revision
                        or record.get('repository') != 'Flem15/aws-llm-production-lab'
                        or record.get('digest') != data['expected_images'].get(binding['name'])):
                    reasons.append('provenance source or digest binding mismatch')
                when = datetime.fromisoformat(record['verified_at'])
                if when.tzinfo is None or now - when > timedelta(hours=24) or when - now > timedelta(minutes=5):
                    reasons.append('provenance verification stale or future dated')
                raw = verified[binding['raw_verification_evidence']]
                if hashlib.sha256(raw).hexdigest() != record.get('raw_verification_sha256') or not json.loads(raw):
                    reasons.append('provenance verification receipt missing or altered')
        image_data = json.loads(verified['images'])
        images = image_data['images']
        db_at = datetime.fromisoformat(image_data['database']['UpdatedAt'])
        if db_at.tzinfo is None or now - db_at > timedelta(hours=48) or db_at - now > timedelta(minutes=5):
            reasons.append('scanner vulnerability database stale or future dated')

        if len(images) != 3 or {image['name'] for image in images} != IMAGE_NAMES:
            reasons.append('exactly three distinct image scan records required')
        cloud = json.loads(verified['cloud_controls'])
        if cloud.get('account') != '655469962246' or cloud.get('region') != 'us-east-2':
            reasons.append('cloud controls account or region mismatch')
        checked = datetime.fromisoformat(cloud['checked_at'])
        if checked.tzinfo is None or now - checked > timedelta(hours=24) or checked - now > timedelta(minutes=5):
            reasons.append('cloud controls stale or future dated')
        for control in ('release_chain_removed', 'github_authorization_active'):
            if cloud.get(control) is not True:
                reasons.append('cloud control not verified: ' + control)
        if stage == "deployment":
            deliveries = cloud.get('alarm_deliveries', [])
            required_alarms = cloud.get('required_alarm_arns', [])
            if not required_alarms or {d.get('alarm_arn') for d in deliveries} != set(required_alarms):
                reasons.append('alarm delivery coverage incomplete')
            for delivery in deliveries:
                if (delivery.get('encrypted') is not True or delivery.get('subscription_confirmed') is not True
                        or not delivery.get('message_id') or not delivery.get('receipt_reference')
                        or delivery.get('trigger') != 'cloudwatch_alarm_transition'):
                    reasons.append('alarm delivery lacks verified end-to-end receipt')
                received = datetime.fromisoformat(delivery['received_at'])
                if received.tzinfo is None or now - received > timedelta(hours=24) or received - now > timedelta(minutes=5):
                    reasons.append('alarm receipt stale or future dated')
        for image in images:
            scanned = datetime.fromisoformat(image['scanned_at'])
            if scanned.tzinfo is None or now - scanned > timedelta(hours=24) or scanned - now > timedelta(minutes=5):
                reasons.append('image scan stale or future dated')
            name = image['name']
            if type(image.get('scan_exit_code')) is not int or image.get('scan_exit_code') != 0 or image.get('scan_complete') is not True:
                reasons.append(f'incomplete image scan: {name}')
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', str(image.get('id', ''))) or image.get('id') != data['expected_images'].get(name):
                reasons.append(f'image digest mismatch: {name}')
            counts = image['vulnerabilities']
            raw_scan = json.loads(verified[image['raw_scan_evidence']])
            if (raw_scan.get('ArtifactType') != 'container_image' or not raw_scan.get('Results')
                    or raw_scan.get('Metadata', {}).get('ImageID') != image.get('config_id')
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(image.get('config_id', '')))):
                reasons.append(f'raw scan missing or bound to wrong image: {name}')
            if datetime.fromisoformat(raw_scan['CreatedAt']) != scanned:
                reasons.append(f'raw scan timestamp mismatch: {name}')
            raw_counts = Counter(v['Severity'] for result in raw_scan['Results'] for v in result.get('Vulnerabilities', []))
            if set(raw_counts) - {'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'}:
                reasons.append(f'unknown severity label in raw scan: {name}')
            if any(raw_counts[k] != counts.get(k) for k in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN')):
                reasons.append(f'scan summary does not match raw vulnerability findings: {name}')
            raw_secrets = sum(len(result.get('Secrets', [])) for result in raw_scan['Results'])
            if raw_secrets != image.get('secret_findings'):
                reasons.append(f'secret summary does not match raw scan: {name}')

            if not all(isinstance(counts.get(k), int) and not isinstance(counts.get(k), bool)
                       and counts[k] >= 0 for k in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN')):
                reasons.append(f'invalid vulnerability counts: {name}')
            elif any(counts[k] for k in ('CRITICAL', 'HIGH', 'UNKNOWN')):
                reasons.append(f'unresolved high/critical/unknown image findings: {name}')
            if type(image.get('secret_findings')) is not int or image.get('secret_findings') != 0:
                reasons.append(f'unresolved or missing secret scan: {name}')
        for finding in data['findings']:
            if stage == 'publication' and finding.get('id') in {'ALARM-01','EVIDENCE-01'}:
                continue  # Operational readiness blocks deployment, not approved artifact publication.
            if finding.get('blocking') is not False and finding.get('status') != 'resolved':
                reasons.append(f'unresolved finding: {finding.get("id", "missing-id")}')
    except (KeyError, TypeError, ValueError, OSError, AttributeError) as exc:
        reasons.append('malformed or incomplete review evidence: ' + type(exc).__name__)
    return {'decision': 'NO-GO' if reasons else 'GO', 'reasons': sorted(set(reasons)),
            'authorization': 'This local review result does not authorize AWS changes or trading.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--stage', choices=['deployment','publication'], default='deployment')
    parser.add_argument('--source-revision')
    args = parser.parse_args()
    if args.stage == 'publication' and not args.source_revision:
        parser.error('publication requires an exact requested source revision')
    try:
        result = evaluate(json.loads(args.manifest.read_text()), args.manifest.parent, stage=args.stage, source_revision=args.source_revision)
    except (OSError, ValueError):
        result = {'decision': 'NO-GO', 'reasons': ['review manifest cannot be read']}
    print(json.dumps(result, indent=2))
    return 0 if result['decision'] == 'GO' else 2


if __name__ == '__main__':
    raise SystemExit(main())
