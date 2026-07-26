# PJ23 encrypted runtime

This directory contains only the generic encryption helper and encrypted PJ23
runtime state. Portfolio data, generated brief HTML, ledgers, and prompts must
never be committed as plaintext in this repository.

- `runtime.enc`: AES-256-GCM encrypted runtime bundle.
- `../site/investment/latest.enc`: encrypted last-known-good HTML.
- Key: GitHub Actions secret `PJ23_FEED_KEY`; never store it in Git.
- Triggers: scheduled and manual dispatch only. Fork and pull-request triggers
  are intentionally forbidden.

If a run fails before both encrypted artifacts are validated, it must not
commit or deploy anything. Disable `daily-investment-brief.yml` to stop the
cloud runner without deleting the previous good ciphertext.
