import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("release", Path(__file__).with_name("prepare-ecs-release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.task = {"taskDefinition": {
            "family": "llm-parity-demo-inference", "revision": 1,
            "taskRoleArn": "existing-role", "cpu": "256",
            "containerDefinitions": [{
                "name": "demo-inference-api",
                "image": "655469962246.dkr.ecr.us-east-2.amazonaws.com/demo-inference-api:v1",
                "environment": [{"name": "PORT", "value": "3000"}],
                "secrets": [{"name": "TOKEN", "valueFrom": "existing-secret"}],
            }],
        }}

    def test_preserves_runtime_configuration_and_original(self):
        result = release.prepare(self.task, "a" * 40, "sha256:" + "b" * 64)
        self.assertEqual(result["taskRoleArn"], "existing-role")
        self.assertEqual(result["containerDefinitions"][0]["secrets"],
                         self.task["taskDefinition"]["containerDefinitions"][0]["secrets"])
        self.assertNotIn("revision", result)
        self.assertEqual(self.task["taskDefinition"]["revision"], 1)
        self.assertTrue(result["containerDefinitions"][0]["image"].endswith("@sha256:" + "b" * 64))

    def test_rejects_tag_in_place_of_digest(self):
        with self.assertRaises(ValueError):
            release.prepare(self.task, "a" * 40, "latest")

    def test_rejects_invalid_release(self):
        with self.assertRaises(ValueError):
            release.prepare(self.task, "latest", "sha256:" + "b" * 64)
        self.task["taskDefinition"]["family"] = "production"
        with self.assertRaises(ValueError):
            release.prepare(self.task, "a" * 40, "sha256:" + "b" * 64)


if __name__ == "__main__":
    unittest.main()
