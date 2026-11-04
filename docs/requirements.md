# FinGPT requirements

This document is the current product requirements source of truth. It separates
approved behavior from items that still need a decision.

## Product and scope

- Personal finance assistant on Telegram and web. The web UI offers Vietnamese
  and English; transaction extraction remains conservative and Vietnamese-first.
- Initial use is by the owner. Keep user and bot ownership boundaries so the
  product can expand to multiple users and bots later.
- Rebuild the application cleanly. Use the existing repository to discover
  useful product ideas and behavior; do not carry over its implementation or
  migrate its SQLite data.
- Preserve and improve useful existing capabilities: natural language entry,
  income and expense, receipt capture, history, reports, CSV import/export,
  search, edit, delete, undo, and web dashboard. Add queued Telegram processing,
  reliable drafts, multi-transaction parsing, and image/CSV previews.
- Do not add bank synchronization, multiple wallets, recurring transactions, or
  a spending budget in the first release unless separately approved.

## Approved decisions

- Web: React, TypeScript, and Vite. The backend owns business rules, access
  control, secrets, and all trusted writes.
- Backend: retain Python and Flask as the API foundation; serve the built web
  app on the same origin where practical.
- Cloud: Azure Container Apps, PostgreSQL Flexible Server, Service Bus, private
  Blob Storage, Azure AI Foundry, Key Vault, Azure Monitor/Application Insights,
  and ACR are the current architecture choices, subject to deployment validation.
- Infrastructure: Bicep. CI/CD: GitHub Actions with OIDC where configured.
- Authentication: username and password only. Do not use Microsoft Entra for
  product login. No public self-registration in the initial owner-only release.
- All changes that create, edit, or delete transactions require a preview and
  an explicit confirmation before the write.
- Exact currency amounts; default currency is VND. Default timezone is
  `Asia/Ho_Chi_Minh`.
- A text-originated transaction must have a date, a day period, and an exact
  local clock time before confirmation. Ask only for missing facts; never
  substitute message arrival, image upload, or worker processing time. Preserve
  a supplied clock time and derive its period where needed.
- Image and CSV imports retain their source-specific required fields; requiring
  an exact clock time for text must not invent one from receipt or import data.
- Text listing multiple items creates separate proposed transactions. Complete
  items can be confirmed individually while incomplete items remain drafts.
- A single receipt image proposes one transaction for the receipt total, not
  separate transactions for each line item. Keep the original image as evidence
  and make it viewable from the web UI.
- Keep evidence for text, images, and imported files linked to the resulting
  transaction or draft so the user can inspect how it was recorded.
- No Git commit or push without a separate, explicit user request for each
  action. A commit request does not authorize a push.
- Cloud deployment must never assign Azure roles. List needed assignments in
  `RBAC.md` for the owner to perform.

## Transaction time interpretation

- A date or day period without an exact clock time stays in draft and asks for
  the missing field(s).
- A clock time without a date stays in draft and prompts for the date.
- An explicit date and clock time are stored as local transaction date and time
  in `Asia/Ho_Chi_Minh`; the day period is derived from the clock time.
- An explicit date and day period without a clock time stay in draft and ask for
  the clock time.
- Proposed period boundaries for review: morning 05:00–10:59, noon
  11:00–13:59, afternoon 14:00–17:59, and evening 18:00–04:59. Store the exact
  clock time separately; never invent a clock time from a period.
- Relative dates are resolved from the intake timestamp in the owner's timezone,
  not from a later worker retry.

## Data and interaction rules

- On an empty web chat, show a minimal welcome and composer without sample
  prompts. Do not automatically open an old draft; resume a pending draft only
  after an explicit user action. Put transaction review fields in the side panel
  when space permits.
- Web chat is one unified conversation coordinated by the backend across two
  specialists: read-only financial Q&A and transaction intake. Q&A can use
  owner-scoped monthly aggregates and bounded prior turns, but cannot perform
  transaction writes. Transaction intake creates a durable preview only;
  clarifications update missing draft facts and still require separate review
  and confirmation. Ambiguous messages default to Q&A rather than creating a
  transaction proposal.
- The web transaction history filters by calendar month, exact day, or all
  dates, with type/search/deleted filters and server-side pagination. Summaries
  for a selected date range reflect all matching active transactions (not just
  the visible page) and never mix currencies. CSV import is removed from the
  transaction screen; historical CSV evidence and the existing API remain intact.
- Language preference is stored locally in the browser. If AI chat is not
  configured, explain that it is unavailable instead of generating fake answers.

- Drafts are durable across restarts and carry a version. Stale or already-used
  confirmation actions must not create another write.
- Text items from one message retain a link to the original message and are
  independently reviewable. A receipt image is one total transaction with the
  original image attached as evidence.
- A single clear action followed by a comma-separated list and one total is one
  grouped text proposal. If multiple actions share one amount and ownership of
  that amount is unclear, do not assign it to the last item.
- Reports are calculated from database records, not from model-generated sums.
- Net income minus expenses is labeled as net cash flow, not a bank balance.
- Undo is revision-based and must not overwrite later edits.
- CSV import maps columns, reports row-level errors, previews rows, and requires
  confirmation. Similar-looking transactions are never silently discarded.
- Historical SQLite data is not migrated into the rebuilt product.

## Open decisions to carry into milestone reviews

- Confirm or adjust the proposed time-of-day boundaries above.
- Verify the owner-selected Azure subscription by subscription ID and tenant;
  do not infer authorization from its display name.
- Select a Foundry deployment after evaluating Vietnamese extraction, image
  quality, structured output, latency, and token use.
- Set evidence and conversation retention/deletion periods.
- Set expected workload, response-time goals, recovery targets, and measurable
  model quality thresholds after a baseline evaluation.
- Confirm the production deployment approval flow and GitHub repository features.

## Acceptance behavior

- `Ăn phở 50k` creates a draft and asks for date and day period.
- `Hôm qua ăn phở 50k` asks for day period; it does not write.
- `Sáng hôm qua ăn phở 50k` asks only for the exact clock time; it writes only
  after the completed preview is separately confirmed.
- `8h sáng ăn bánh mì 15k` asks for the date; it retains the explicit clock time.
- `Hôm qua ăn phở 50k, xem phim 100k` creates two separate proposals. Each is
  confirmed independently; missing time facts remain on that item only.
- `Sáng nay ăn bánh xèo, bánh căn bánh bèo 30k` creates one grouped 30,000 VND
  draft and asks only for the missing clock time.
- A receipt image containing several line items proposes one transaction at the
  receipt total, shows a preview, and retains the image as evidence.
- Replaying an update or pressing an old confirmation twice creates at most one
  transaction.
- A report exactly matches the transaction data used for its date range.
