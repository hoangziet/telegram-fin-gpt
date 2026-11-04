# FinGPT development rules

This file applies to every change in this repository. Keep it aligned with the
approved requirements and the actual code. Do not treat a proposal as approval to
implement or deploy it.

## Git and deployment

- Never commit unless the user explicitly requests a commit.
- Never push unless the user explicitly requests a push. A request to commit does
  not authorize a push; a request to edit, test, or configure CI/CD authorizes
  neither action.
- Never deploy to Azure or change cloud resources, credentials, or production
  data solely because a plan describes them. Follow the user's approved scope.
- Inspect the working tree before edits. Preserve unrelated user changes.
- After implementing and deploying an approved change through an existing,
  already-configured pipeline, report the resulting live URL and what to test
  back to the user in chat.

## Architecture and behavior

- Keep the backend responsible for business rules, authorization, data writes,
  Telegram, and AI integration. The browser must not own secrets or trusted
  transaction logic.
- Design module boundaries, data ownership, and permissions to support multiple
  users and bots later, while keeping the initial experience simple.
- Rebuild the product cleanly. Use the old code for ideas and feature discovery,
  not as a source to migrate mechanically. No SQLite data migration is required.
- Require a review and explicit confirmation before creating or changing
  transactions. Store pending work durably and reject stale confirmations.
- Parse a text message with several expenses into separate proposed transactions.
  Allow each complete item to be confirmed independently while incomplete items
  remain pending. For a single uploaded image, propose one transaction for its
  total instead of recording each visible line item separately.
- Store the source evidence associated with each action so the user can inspect
  it later in the web UI, including an uploaded receipt when applicable.
- Never infer a missing transaction date or day period from message arrival,
  upload time, or worker execution time. Ask for missing information.
- When the user supplies a specific clock time, preserve it and derive the day
  period using the approved time ranges. A clock time without a transaction date
  still needs clarification; do not assume the date of the message.
- Use exact money types, explicit currency and rounding rules, database
  constraints, versioned migrations, ownership checks, and audit history.
- Treat model output, Telegram messages, CSV files, and images as untrusted input.
  Validate them before any business action. Never let a model execute arbitrary SQL.
- Make retries and repeated callbacks idempotent; do not assume exactly-once
  delivery from a queue or webhook.
- Do not log secrets or raw financial content. Protect private source files and
  provide a defined retention and deletion policy before storing them in cloud.
- LLM-based routing or specialist agents may classify intent or assist
  extraction, but only the deterministic confirmation endpoints may create or
  modify a transaction. No agent may call a write or confirm path directly, and
  any agent must degrade safely (fallback or honest unavailability) rather than
  block the user or fabricate a result.

## Code quality

- Prefer small, cohesive modules and clear names. Keep business logic out of
  routes, UI components, and infrastructure adapters. Avoid duplication and
  needless dependencies.
- Keep each handwritten source file under 1,000 lines and each source line under
  120 characters. Prefer substantially shorter files and functions where it
  improves readability; do not split code mechanically to satisfy a limit.
- Aim for functions of at most 50 meaningful lines and cognitive complexity of
  at most 15 where an appropriate analyzer supports that metric. Explain narrow
  exceptions in the review or nearby documentation.
- Use Python type hints for public interfaces and TypeScript strict mode. Avoid
  `any`, unchecked casts, broad exception handling, silent failures, and fake
  success responses.
- Add docstrings to public Python APIs and non-obvious business logic. Add TSDoc
  when a TypeScript interface or behavior is not obvious. Avoid decorative comments.
- Add meaningful tests for changed business behavior, especially money, dates,
  permissions, confirmation, undo, and retry handling. Run relevant lint, format,
  type, test, and build checks before reporting completion.
- Treat SonarSource guidance as a quality reference. Ruff, ESLint, and formatters
  are not a SonarQube quality gate. Do not claim Sonar analysis or its metrics
  passed unless a Sonar analyzer and gate were actually configured and run.
- Exclude generated code, lockfiles, and generated migrations from the file-length
  limit. Keep exceptions to line length narrow, such as an indivisible URL.
- Update affected requirements, architecture, development, and deployment docs
  when behavior, API, configuration, or deployment changes.

## UI

- Use the Hallmark design skill from https://github.com/nutlope/hallmark for the
  approved web redesign. Preserve functional content and interactions. Verify
  keyboard access, contrast, reduced motion, and mobile layouts in a browser.
