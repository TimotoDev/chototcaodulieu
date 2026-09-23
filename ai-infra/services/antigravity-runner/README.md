# Antigravity runner — bootstrap only, automation NOT enabled

Official CLI 1.2.5 Linux ARM64 is installed in this image, with release SHA512
verified at build. No subscription credentials, API keys or repository are baked
into the image. No model billing provider is configured.

ECR: `timoto-ai/repair-agent:antigravity-cli-1.2.5`
Digest: `sha256:30019cb8d1d28786962c89e216c8335228818dde5e04fc8971ffba9c20632b27`

`smoke_fargate.py` runs only `agy --version` in a one-off Fargate task, with no
task IAM role, no secrets and no inbound access. It reuses the public network
configuration; no NAT or always-running service is created.

Local container version/help succeeded. Unauthenticated print mode requests an
interactive Google login, so it is not ready for automatic incident processing.
Do not treat this image or the version smoke as a functioning repair agent.

Remaining gates:
1. User completes official Google OAuth; verify subscription entitlement/quota.
2. Verify supported encrypted session persistence across task replacement.
   Do not extract tokens from the IDE or store login codes in logs/images.
3. Disable additional AI-credit usage; no Gemini API billing fallback.
4. Provide scoped repository access, isolated execution, incident lease/dedup,
   timeout and retry caps, evidence/output validation and tests.
5. Verify one staging incident before enabling automatic dispatch.
6. PR-only workflow; merge/deployment need separate authorization.

Nova remains disabled. Existing incident collection service is unchanged.
The image storage and one-off Fargate smoke incur AWS usage, not a new permanent
compute service. Code is not committed/pushed to Git.
