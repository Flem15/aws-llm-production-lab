"""Clone the running development task, changing only its image and version."""
import copy
import json
import re
import sys


def prepare(document, image_tag, image_digest):
    if not re.fullmatch(r"[0-9a-f]{40}", image_tag):
        raise ValueError("Expected a full immutable Git SHA")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise ValueError("Expected an immutable registry digest")
    task = copy.deepcopy(document["taskDefinition"])
    if task["family"] != "llm-parity-demo-inference":
        raise ValueError("Unexpected task family")
    containers = task["containerDefinitions"]
    if len(containers) != 1 or containers[0]["name"] != "demo-inference-api":
        raise ValueError("Unexpected container configuration")
    container = containers[0]
    repository = "655469962246.dkr.ecr.us-east-2.amazonaws.com/demo-inference-api"
    if not container["image"].startswith((repository + ":", repository + "@")):
        raise ValueError("Unexpected image repository")
    container["image"] = repository + "@" + image_digest
    environment = [item for item in container.get("environment", []) if item["name"] != "APP_VERSION"]
    container["environment"] = environment + [{"name": "APP_VERSION", "value": image_tag}]
    for field in ("taskDefinitionArn", "revision", "status", "requiresAttributes",
                  "compatibilities", "registeredAt", "registeredBy", "deregisteredAt"):
        task.pop(field, None)
    return task


if __name__ == "__main__":
    with open(sys.argv[1]) as source:
        print(json.dumps(prepare(json.load(source), sys.argv[2], sys.argv[3]), indent=2))
