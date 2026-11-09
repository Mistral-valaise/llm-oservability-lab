# Security policy

This is a demo project, but security issues are taken seriously.

Please report vulnerabilities privately through GitHub: *Security > Report a vulnerability* on this repository. Do not open public issues for security problems. Expect a first answer within 7 days.

Supported: the `main` branch and the latest image tag.

Verify an image before running it:

```bash
cosign verify ghcr.io/mistral-valaise/llm-observability-lab/ai-gateway:main \
  --certificate-identity-regexp 'https://github.com/Mistral-valaise/llm-observability-lab/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
