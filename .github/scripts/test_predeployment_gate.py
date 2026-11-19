import copy
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('gate', Path(__file__).with_name('predeployment_gate.py'))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 11, 20, tzinfo=timezone.utc)
        self.images = {'database': {'UpdatedAt':self.now.isoformat()}, 'images': [dict(name=name, id='sha256:'+hashlib.sha256(name.encode()).hexdigest(), scan_exit_code=0, scan_complete=True,
            scanned_at=self.now.isoformat(), secret_findings=0, vulnerabilities={k: 0 for k in ['CRITICAL','HIGH','MEDIUM','LOW','UNKNOWN']})
            for name in ['bot-city','proxy','api']]}
        self.data = dict(schema=1, account='655469962246', region='us-east-2', collected_at=self.now.isoformat(),
            evidence={}, checks={name:{'status':'pass','evidence':['images']} for name in gate.REQUIRED},
            expected_images={i['name']:i['id'] for i in self.images['images']}, findings=[])
        cloud = dict(account='655469962246', region='us-east-2', checked_at=self.now.isoformat(),
            release_chain_removed=True, github_authorization_active=True, required_alarm_arns=['alarm'],
            alarm_deliveries=[dict(alarm_arn='alarm', encrypted=True, subscription_confirmed=True,
                message_id='message', receipt_reference='operator-receipt', trigger='cloudwatch_alarm_transition',
                received_at=self.now.isoformat())])
        raw=json.dumps(cloud).encode();(self.root/'cloud.json').write_bytes(raw)
        self.data['evidence']['cloud_controls']={'path':'cloud.json','sha256':hashlib.sha256(raw).hexdigest()}
        for image in self.images['images']:
            image['config_id']=image['id']
            image['raw_scan_evidence']='raw-'+image['name']
            raw=json.dumps({'ArtifactType':'container_image','CreatedAt':self.now.isoformat(),
                'Metadata':{'ImageID':image['id']},'Results':[{'Vulnerabilities':[]}]}).encode()
            path='raw-'+image['name']+'.json';(self.root/path).write_bytes(raw)
            self.data['evidence'][image['raw_scan_evidence']]={'path':path,'sha256':hashlib.sha256(raw).hexdigest()}
        self.data['expected_source_revision']='a'*40
        self.data['approved_actions']=['deployment']
        bindings=[]
        for image in self.images['images']:
            name=image['name'];raw=b'[{"verificationResult":{}}]'
            record=dict(verified=True,source_revision='a'*40,repository='Flem15/aws-llm-production-lab',
                digest=image['id'],verified_at=self.now.isoformat(),raw_verification_sha256=hashlib.sha256(raw).hexdigest())
            for key,content in [('receipt-'+name,json.dumps(record).encode()),('signed-'+name,raw)]:
                path=key+'.json';(self.root/path).write_bytes(content)
                self.data['evidence'][key]={'path':path,'sha256':hashlib.sha256(content).hexdigest()}
            bindings.append(dict(name=name,verification_evidence='receipt-'+name,raw_verification_evidence='signed-'+name))
        raw=json.dumps({'artifacts':bindings}).encode();(self.root/'provenance.json').write_bytes(raw)
        self.data['evidence']['release_provenance']={'path':'provenance.json','sha256':hashlib.sha256(raw).hexdigest()}
        self.write_images()

    def write_images(self):
        raw=json.dumps(self.images).encode();(self.root/'images.json').write_bytes(raw)
        self.data['evidence']['images']={'path':'images.json','sha256':hashlib.sha256(raw).hexdigest()}

    def result(self):
        return gate.evaluate(self.data,self.root,self.now)['decision']

    def test_complete_evidence_passes_without_authorizing_actions(self):
        self.assertEqual(self.result(),'GO')
        self.assertIn('does not authorize',gate.evaluate(self.data,self.root,self.now)['authorization'])

    def test_missing_check_blocks(self):
        del self.data['checks']['alarm_delivery'];self.assertEqual(self.result(),'NO-GO')

    def test_wrong_account_and_region_block(self):
        for key,value in [('account','111111111111'),('region','us-east-1')]:
            original=self.data[key];self.data[key]=value;self.assertEqual(self.result(),'NO-GO');self.data[key]=original

    def test_stale_and_future_evidence_block(self):
        for at in [self.now-timedelta(days=2),self.now+timedelta(hours=1)]:
            self.data['collected_at']=at.isoformat();self.assertEqual(self.result(),'NO-GO')

    def test_hash_tampering_blocks(self):
        (self.root/'images.json').write_text('{}');self.assertEqual(self.result(),'NO-GO')

    def test_path_escape_blocks(self):
        self.data['evidence']['images']['path']='../outside.json';self.assertEqual(self.result(),'NO-GO')

    def test_digest_mismatch_blocks(self):
        self.data['expected_images']['api']='sha256:other';self.assertEqual(self.result(),'NO-GO')

    def test_failed_scan_is_not_clean(self):
        self.images['images'][0]['scan_exit_code']=1;self.write_images();self.assertEqual(self.result(),'NO-GO')

    def test_high_and_unknown_findings_block_even_when_checks_claim_pass(self):
        for severity in ['CRITICAL','HIGH','UNKNOWN']:
            self.images['images'][0]['vulnerabilities'][severity]=1
            self.write_images();self.assertEqual(self.result(),'NO-GO')
            self.images['images'][0]['vulnerabilities'][severity]=0

    def test_unresolved_finding_blocks(self):
        self.data['findings']=[{'id':'IAM-01','status':'open','blocking':True}];self.assertEqual(self.result(),'NO-GO')

    def test_duplicate_images_block(self):
        self.images['images'][2]=copy.deepcopy(self.images['images'][0]);self.write_images();self.assertEqual(self.result(),'NO-GO')

    def test_missing_scanner_fields_block(self):
        del self.images['images'][0]['secret_findings'];self.write_images();self.assertEqual(self.result(),'NO-GO')

    def test_malformed_manifest_blocks(self):
        self.assertEqual(gate.evaluate({},self.root,self.now)['decision'],'NO-GO')

    def test_old_scan_with_new_manifest_blocks(self):
        self.images['images'][0]['scanned_at']=(self.now-timedelta(days=2)).isoformat()
        self.write_images();self.assertEqual(self.result(),'NO-GO')

    def test_missing_cloud_evidence_blocks(self):
        del self.data['evidence']['cloud_controls'];self.assertEqual(self.result(),'NO-GO')

    def test_unverified_cloud_and_delivery_block(self):
        original=json.loads((self.root/'cloud.json').read_text())
        for key,value in [('release_chain_removed',False),('github_authorization_active',False),('alarm_deliveries',[])]:
            data=copy.deepcopy(original);data[key]=value;raw=json.dumps(data).encode()
            (self.root/'cloud.json').write_bytes(raw)
            self.data['evidence']['cloud_controls']['sha256']=hashlib.sha256(raw).hexdigest()
            self.assertEqual(self.result(),'NO-GO')

    def test_boolean_scan_counts_block(self):
        self.images['images'][0]['secret_findings']=False
        self.write_images();self.assertEqual(self.result(),'NO-GO')

    def test_old_database_with_fresh_scan_blocks(self):
        self.images['database']['UpdatedAt']=(self.now-timedelta(days=3)).isoformat()
        self.write_images();self.assertEqual(self.result(),'NO-GO')

    def test_raw_high_finding_cannot_be_hidden_by_summary(self):
        p=self.root/'raw-api.json';d=json.loads(p.read_text())
        d['Results'][0]['Vulnerabilities']=[{'Severity':'HIGH'}]
        raw=json.dumps(d).encode();p.write_bytes(raw)
        self.data['evidence']['raw-api']['sha256']=hashlib.sha256(raw).hexdigest()
        self.assertEqual(self.result(),'NO-GO')

    def test_missing_raw_scan_blocks(self):
        del self.data['evidence']['raw-api'];self.assertEqual(self.result(),'NO-GO')

    def test_raw_image_binding_blocks(self):
        self.images['images'][0]['config_id']='sha256:'+'f'*64
        self.write_images();self.assertEqual(self.result(),'NO-GO')

    def test_missing_provenance_blocks(self):
        del self.data['evidence']['release_provenance'];self.assertEqual(self.result(),'NO-GO')

    def test_wrong_source_revision_blocks(self):
        self.data['expected_source_revision']='c'*40;self.assertEqual(self.result(),'NO-GO')

    def test_publication_requires_its_own_approval(self):
        self.assertEqual(gate.evaluate(self.data,self.root,self.now,stage='publication',source_revision='a'*40)['decision'],'NO-GO')

    def test_approved_publication_requires_prepublication_signed_provenance(self):
        self.data['approved_actions']=['publication']
        self.data['checks']['alarm_delivery']['status']='blocked'
        self.assertEqual(gate.evaluate(self.data,self.root,self.now,stage='publication',source_revision='a'*40)['decision'],'GO')
        self.assertEqual(self.result(),'NO-GO')
        del self.data['evidence']['release_provenance']
        self.assertEqual(gate.evaluate(self.data,self.root,self.now,stage='publication',source_revision='a'*40)['decision'],'NO-GO')

    def test_source_approval_cannot_be_reused_for_another_commit(self):
        self.data['approved_actions']=['publication']
        self.assertEqual(gate.evaluate(self.data,self.root,self.now,stage='publication',source_revision='b'*40)['decision'],'NO-GO')

if __name__=='__main__':unittest.main()
