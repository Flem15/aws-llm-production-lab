# Publication review inputs

The producer builds and scans all three OCI archives, signs their manifest digests, verifies those signatures, and produces the signed `approved-release-evidence` artifact. It never assumes an AWS role or pushes images. The publisher verifies the run identity, operator-reviewed artifact archive SHA256, signed evidence manifest, all file hashes, exact OCI manifest/config/layer digests and fresh scan findings before assuming its ECR-only session.

A fresh `gate-input.json` and its referenced review files must be reviewed and promoted with the source before dispatch. Use `gate-input.example.json` as the schema, not as passing evidence. The example is intentionally NO-GO. Do not copy old pass records or invent receipt IDs. Keep secrets and broker credentials out of public evidence.

Provide factual review evidence for the current deployed release-role permissions and OIDC trust, active GitHub protections, npm disposition, and explicit scoped change approval. The `cloud_controls` file requires account `655469962246`, region `us-east-2`, fresh `checked_at`, and verified `release_chain_removed` and `github_authorization_active` values. Include the underlying readbacks and policy simulation results as hashed evidence; `true` fields alone are not proof.

The producer supplies current source SHA, expected immutable image digests, image/config scan records, database dates, required-CI results, and cryptographically verified provenance. It sets only the local-validation and image-provenance checks from those results. The remaining human-reviewed checks are not automatically accepted. It preserves review collection timestamps and rejects expired reviews. Publication-only approval must not include deployment.

All required review check keys must remain present, including operational checks which can be pending for publication. Runtime/alarm/recovery checks continue blocking application deployment. No producer or publisher workflow success authorizes trading.

Dispatch the producer on protected main with `confirmation=development-evidence` only after workflow/source promotion and explicit authorization. Review its completed output and artifact digest. A separate authorized publisher dispatch uses the same main SHA, producer run ID, archive SHA256 and `confirmation=development-publication`. If main advances, regenerate evidence for that revision. No rebuilding occurs in the publisher. Never re-attest an old ECR image as a new build.

Failure: keep both release/deployment workflows disabled until approved. Do not weaken gates or fabricate passing input to make an artifact appear. Genuine signed project-image evidence requires the authorized GitHub run; local mocked signature tests are not that evidence.
