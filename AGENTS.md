# Project working agreements

## Shared validation environment

- Reuse and extend the existing dev-scaffold dummy project for manual validation,
  deployment checks, and review walkthroughs.
- Add scenarios to that project's existing application and services. Do not
  create another dummy application, test service, or separately named Compose
  project for a feature or PR unless the user explicitly requests one.
- Use the existing operator/developer commands for that environment. If its
  location or configuration is unclear, locate it or ask the user before
  provisioning a replacement.
- Apply this rule to PR descriptions, documentation, and copy-paste review
  commands too. Do not embed feature-specific Compose project names or image
  prefixes that recreate a separate stack when a reviewer follows them.
