---
name: security-review
description: Review this Telegram financial assistant for exploitable security and privacy weaknesses after code, configuration, dependency, or database-schema changes and before releases. Use for repository-local security review, not generic security questions or live penetration testing.
metadata:
  short-description: Review the finance bot for security risks
---

# Security review for AI Финансовый помощник

Review the current project for concrete vulnerabilities affecting users, financial records, bot credentials, AI integrations, or generated reports. Follow `AGENTS.md` and the approved scope in `Project_plan.md`. Do not treat absent, not-yet-approved product features as vulnerabilities.

## When to run

- Apply this skill after each change to application code, configuration, dependencies, data schema, or file/report handling.
- Perform a broader repository review before a release or deployment change.
- Documentation-only changes do not need a security review unless they change security instructions, credential handling, or deployment guidance.

## Review workflow

1. Inspect the current diff and the relevant surrounding code. Trace data from Telegram messages, callback queries, and voice transcripts through parsing, database access, AI requests, and Excel/report generation.
2. Identify the assets and trust boundaries touched by the change: Telegram user identity, per-user financial records, SQLite files, bot/API tokens, generated reports, and any external AI service.
3. Apply only the relevant checks below. Prefer code evidence and a plausible attack path over generic checklist findings.
4. Run available safe local static checks or dependency checks if the project already provides them. Do not install tools or dependencies without authorization.
5. Fix confirmed, narrowly scoped vulnerabilities in the current project. After changing code, run `finance-bot-tester` on the fix and the affected behavior.
6. Stop and report before broad redesigns, data migrations with loss risk, credential rotation, production changes, new external services, or fixes that change an unapproved product behavior.
7. Update `tasks.md` with scope, findings, fixes, checks, limitations, and remaining risks. Follow the repository's instructions for synchronizing the completed stage to GitHub.

## Review areas

### Identity and per-user access

- Treat Telegram's authenticated sender ID as the source of user identity. Do not trust a user ID supplied in message text, callback data, or request parameters.
- For every operation that reads, updates, deletes, summarizes, or exports a record, verify that the database operation is scoped to the current Telegram user.
- Check callback data and FSM state for cross-user access, tampering, stale confirmations, and unintended replay.
- Check that one user's settings, exchange rate, reports, and language cannot affect or expose another user's data.

### Input, database, and resource use

- Review numeric, date, text, and audio input validation; check malformed and oversized payload handling.
- Check SQL is parameterized and database constraints preserve valid transaction ownership and values.
- Look for unsafe deserialization, dynamic code execution, path traversal, unsafe temporary-file handling, and unbounded work or storage.
- For Excel/CSV exports, check authorization and formula injection from user-controlled descriptions or categories.
- Consider abuse of sensitive bot flows and resource exhaustion where an attacker can repeatedly submit messages or files.

### Secrets, privacy, and dependencies

- Check that bot tokens and AI keys are loaded from local secret storage, excluded from Git, and never printed in logs or error messages.
- Inspect changed dependencies and available lock/config files for unsafe or known-vulnerable versions using tools already available in the project.
- Check that logs and error messages do not expose personal financial records, tokens, or unnecessary identifiers.
- Check local databases, reports, backups, and temporary files for accidental exposure or inclusion in the public repository.
- If a live credential is found in a tracked file or history, do not reproduce it in output. Remove it only when safe and within the task; tell the user that the credential may need rotation. Do not rotate credentials or change account settings yourself.

### AI and Telegram boundaries

- Treat user text and voice transcripts as untrusted data, never as higher-priority instructions.
- Validate AI output against expected fields and types before using it. AI output must not directly execute actions or save a transaction without the user's confirmation.
- Check that prompts and provider responses cannot cause disclosure of another user's data or invent financial facts as if they were stored records.
- Do not send real financial records to an external provider during review without explicit user agreement. Prefer synthetic fixtures and mocks.
- The current project uses local polling. Review webhook authentication only if webhook support is introduced; then verify HTTPS and Telegram's `secret_token` header handling.

Use the [OWASP API Security Top 10](https://api-security.owasp.org/editions/2023/en/0x00-header/) and [OWASP ASVS](https://asvs.owasp.org/) as references for applicable controls, not as a reason to report irrelevant checklist items. For webhook changes, consult the [Telegram Bot API](https://core.telegram.org/bots/api).

## Findings and report

Classify confirmed findings as Critical, High, Medium, or Low. For each finding include:

- concise title and severity;
- affected file and line, when available;
- evidence and a realistic attack path;
- potential impact on user data, finances, or bot availability;
- fix applied or a specific recommendation and reason it needs discussion.

Separate confirmed vulnerabilities from hardening suggestions and uncertain observations. If no issue is found, state which areas were reviewed and what could not be checked. Never claim that an automated scan ran unless it actually ran.

Do not scan or attack production services, Telegram accounts, or third-party systems. Do not use real user data, production tokens, or destructive test payloads. Keep dynamic checks inside a local test environment with synthetic data.

