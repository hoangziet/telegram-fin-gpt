# Transaction processing design

This document records the M3 transaction workflow contract for implementers
and future agents. It complements `docs/requirements.md`, which remains the
source of approved product behavior.

## M3 implementation boundary

- A submitted message creates a durable draft and preview only. Draft creation
  never writes a confirmed transaction.
- Each text item is an independent `DraftItem`. Ready items can be confirmed
  separately; incomplete items stay in the same draft and can be corrected.
- A receipt image is one total proposal with the original image retained as
  evidence. Image extraction and upload validation remain in M6.
- Evidence is private and user-scoped. Text evidence stores the original input
  and is returned only through authenticated, owner-filtered API responses.
- Current text interpretation is deterministic and conservative. Do not pass
  raw model or file output directly to persistence without boundary validation.

## Implemented API contract

### `POST /api/drafts`

Requires an authenticated username/password session, a CSRF token, and an
`Idempotency-Key` containing a random UUID. The browser must reuse that key if it
retries the same submission. A key reused with different input returns a
conflict; it never replays unrelated draft content.

Request body:

```json
{
  "text": "Hôm qua ăn phở 50k, xem phim 100k",
  "conversationId": null
}
```

The server captures intake time once and uses it to resolve relative dates in
the owner's timezone. A supplied conversation ID must belong to the same user.
If absent, the server creates a web conversation and returns its ID.

Response includes the draft ID/version/status, conversation ID, original text
evidence, and ordered item previews. Amounts are decimal strings with currency
codes. Missing fields are explicit. A new response is `201`; an exact retry is
`200` with `replayed: true`; the same key with different input is `409`.

### `PATCH /api/drafts/{draftId}/items/{itemId}`

Applies explicit corrections to the pending preview using `expectedVersion`.
Money is supplied as a decimal string and currency code; date and time use ISO
formats. A supplied local time derives its day period unless an explicit period
is supplied, in which case the two must agree. The item remains incomplete until
all required facts are present. Text-originated drafts also require an exact
clock time; replying with that time updates only the selected draft item. This
endpoint cannot modify a confirmed transaction.

### `GET /api/drafts/{draftId}`

Returns the same preview and original text evidence after a user-scoped lookup.
An ID owned by another account has the same `404` response as an unknown ID.

### `GET /api/drafts?limit=20`

Returns up to 50 recent owner-scoped drafts with at least one incomplete or
ready item, including each draft's source evidence. This supports reopening a
pending preview after navigation or a browser restart. Drafts whose items are
all confirmed or cancelled are omitted.

### `POST /api/chat/ask`

Authenticated, CSRF-protected, read-only financial Q&A. Body: `{ "question":
"How much have I spent?", "language": "en", "history": [] }`. `question` is
1–1,000 characters, language is `vi` or `en`, and `history` is optional with up
to eight `{role: "user" | "assistant", text: string}` turns of at most 1,000
characters each. Response: `{ "answer": "..." }`. The backend sends only
owner-scoped, non-deleted transaction totals grouped by kind and currency for
the current Asia/Ho_Chi_Minh month, not raw notes or evidence. No chat question
can execute SQL or create, update, or confirm transactions. Without a configured
Foundry model the route returns `503`, never a fabricated answer. Client-supplied
history is untrusted and ephemeral; it is not persisted server-side.

### `POST /api/chat/message`

The unified authenticated chat entry point accepts `{ "message": "...",
"language": "vi|en", "history": [] }`. It can also carry `draftContext` with
`draftId`, `itemId`, and `expectedVersion` when one incomplete item is active.
The backend coordinator routes explicit transaction language to deterministic
draft intake. For a pending item, a supported date/period reply goes to draft
clarification; direct questions go to read-only Q&A. Other or ambiguous messages
default to Q&A. Responses use `intent=question|transaction|clarification`; only
transaction intent includes a durable draft, and clarification returns the
updated item. Transaction messages require a UUID `Idempotency-Key`; exact
retries return the same owner-scoped draft. Draft creation never creates a
transaction. Unknown currency symbols/codes are rejected rather than interpreted
as VND. The two specialists and routing policy are backend responsibilities;
the browser does not classify message intent or own transaction rules. The older
`/api/chat/ask` endpoint remains for compatibility, while the web chat uses
`/api/chat/message`.

### `POST /api/chat/drafts/{draftId}/reply`

Authenticated, CSRF-protected clarification. Body: `{ "itemId": "<uuid>",
"expectedVersion": 1, "text": "2026-11-03 sáng" }`. The item must belong to the
owner and draft; only explicitly supplied missing date/period and a supported
clock time are applied. The response contains the updated item preview; stale
versions return `409`. Ambiguous or non-clarifying replies return `422`. A reply
never confirms an item; the user must review and confirm separately.

### Transaction search and analysis filters

`GET /api/transactions` accepts optional inclusive `from` and `to` dates,
`kind=income|expense`, `q` (up to 128 characters), `includeDeleted`, `limit`
(1–100), and `offset`. All filters are owner-scoped and pagination is applied
after filtering. `GET /api/transactions/reports/totals` accepts required `from`
and `to` (maximum 367 days), plus the same `kind` and `q` filters. It aggregates
all matching active records, independent of search pagination; deleted records
are excluded. Amounts are grouped by currency, never combined across currencies.

### Confirmation and later writes

`POST /api/drafts/{draftId}/items/{itemId}/confirm` is implemented as a
separate state-changing operation. It requires the item's expected version and
an idempotency UUID. The backend atomically creates one transaction and its
first immutable revision, then marks only that item confirmed. Replays of the
same intent return the original result; key reuse with a different payload, an
already-confirmed item, or a stale version returns a conflict. Incomplete items
are rejected. Creating a draft or rendering a preview never substitutes for
confirmation.

## Interpretation rules

- The default currency is VND. A bare numeric amount means that exact number of
  VND; suffixes such as `k`, `nghìn`, and `triệu` are explicit multipliers.
- Date, day period, and exact clock time are separate facts. Missing date or
  period remains a missing field. Intake time is not a transaction time.
- A time with an AM/PM-like Vietnamese qualifier is normalized to local 24-hour
  time. An unqualified 1–12 hour remains ambiguous for period assignment.
- `period_for_clock_time` currently implements the proposed day-period ranges
  in `docs/requirements.md`. Those ranges remain an open product decision and
  must be easy to revise without changing stored exact times.
- Text is split at line breaks, semicolons, and item-separating commas. Commas
  inside numeric thousands groups remain part of the amount.
- Unrecognized or invalid information is not silently rounded or converted to
  a confirmed record. It stays in a draft for user correction.

## Ownership and persistence invariants

- Every route derives `user_id` from the authenticated session. Never accept a
  trusted user ID from request JSON or headers.
- Scope draft, item, evidence, conversation, transaction, search, and report
  queries by owner. Composite foreign keys provide a second database boundary.
- Save source evidence and all draft items in one database transaction.
- Draft request idempotency is unique per user. Transaction idempotency is
  unique per user and draft item; the database constraint is the race guard.
- Never expose private file keys or public blob URLs. File bytes are reopened
  only through authenticated owner-scoped evidence endpoints.


The text draft endpoints and per-item confirmation are implemented. Their
request and response shapes match the contract above, including original source
evidence and ordered previews. Draft persistence and idempotency metadata are
covered by versioned Alembic migrations. Existing-transaction edit, delete, and
undo proposals require a separate preview and confirmation.

The initial CSV parser is a pure domain component. It requires explicit amount
and description column mappings, supports optional currency/kind/date/period/
time/category mappings, validates exact money precision, and returns every row
with its source line number and safe field errors. It caps files at 5 MiB and
10,000 rows. It does not infer missing transaction dates. The authenticated
upload, evidence storage, mapping UI, and preview/confirmation API are still in
progress; parser output alone never writes data.


### `GET /api/transactions` and `GET /api/transactions/reports/totals`

Search supports optional inclusive `from`/`to` ISO dates, income/expense kind,
a note query, limit, and offset. It returns only persisted, non-deleted records
for the authenticated owner unless `includeDeleted=true` is requested. Reports
require a bounded date range and aggregate income and expense separately by
currency using database values, never browser calculations; deleted records are
always excluded from reports.


### Existing transaction changes and undo

`POST /api/transactions/{transactionId}/changes` persists a versioned preview
for an edit or soft delete. `POST /api/transactions/{transactionId}/undo`
creates a preview from the latest immutable revision. Both return a durable change
ID and proposed full state; neither changes the transaction.
`GET /api/transactions/changes/{changeId}` reopens a private preview.
`POST /api/transactions/changes/{changeId}/confirm` applies it only after an
explicit client confirmation, with stale transaction-version checks and an
idempotency UUID. Applied changes increment the transaction version and append
an immutable before/after revision. Deletes are soft deletes, and search/report
queries exclude them by default. `GET /api/transactions/{transactionId}/changes`
lists that owner's pending previews so they can be resumed after leaving the
page. `POST /api/transactions/changes/{changeId}/cancel` cancels a pending
preview without changing the transaction. `GET
/api/transactions/{transactionId}/revisions` returns immutable snapshots in
newest-first order for the owner only.

### Reopening transaction evidence

`GET /api/transactions/{transactionId}/evidence` follows the transaction's
owned draft item to its original evidence. The response includes text and safe
metadata only; it never returns a storage key or public storage URL. The
authenticated CSV route
is `POST /api/imports/csv` with multipart fields `file`, `mapping`, and
`allowDuplicate`, plus a UUID `Idempotency-Key`. It returns one durable draft
item per source row, with original row numbers and field-keyed errors. A file
with invalid structure is rejected; individual invalid data rows remain visible
and cannot be confirmed until corrected. Each complete row can be confirmed
independently. The original CSV is retained as private evidence and can be
downloaded through the owner-scoped draft evidence endpoint. Identical evidence
requires `allowDuplicate=true`; an exact request retry with the same idempotency
key returns the first draft.

`POST /api/imports/image` accepts JPEG, PNG, or WebP up to 10 MiB and requires a
configured Foundry Responses endpoint. It creates one proposal from the receipt
total and retains the original image privately. Model output is validated as
untrusted data; missing date, time, amount, or currency remains incomplete. The
image can be opened from a draft preview or a confirmed transaction, and no
transaction is written until the owner confirms its item.

## Telegram ingress and queue handling

`POST /api/webhooks/telegram/{telegramBotId}` bypasses browser sessions only
for Telegram's webhook route. It verifies the dedicated webhook secret using a
constant-time comparison, requires a one-to-one private chat, and checks the
sender against the bot's configured allowlist. Text evidence, webhook receipt,
and an ID-only outbox event are committed atomically. Repeated Telegram update
IDs return the first receipt and do not create duplicate work.

The outbox dispatcher publishes stable event IDs to Azure Service Bus and marks
acknowledged events published. Delivery is at least once; consumers must remain
idempotent. The worker uses peek-lock settlement, creates or reopens a durable
draft for accepted text, and sends a Telegram preview with per-item confirmation
buttons. Confirm callbacks are checked against the configured bot owner and
expected draft-item version, and expire 24 hours after Telegram received them.
The original Telegram message timestamp is persisted with the callback receipt,
so a delayed click on an old inline button is rejected even when the webhook
received that click moments ago.
Pending previews also include a per-item cancel button. Cancelling one item is
version-checked and leaves sibling items available for review; it never creates
or changes a transaction. Callbacks are checked against their stored webhook
receipt, configured private-chat owner, strict callback shape, and expiration
window before their action is applied.
Pending items can also be corrected in the same private chat with
`/sua <draft-id> <item-number> <field> <value>`. Supported fields are `mo_ta`,
`so_tien`, `ngay` (ISO date), `buoi` (`sang`, `trua`, `chieu`, `toi`, or canonical
period), `gio` (local `HH:MM`), and `loai` (`income` or `expense`). Each command
changes one field, uses the existing money and date/time validation, increments
the item version, and sends a refreshed preview. It cannot edit a confirmed or
cancelled item and does not write a transaction. The correction, consumed
receipt, and preview outbox event commit together for safe redelivery.
Invalid events are dead-lettered; transient database,
queue, and Telegram failures are abandoned for redelivery. Raw finance text is
kept in private evidence storage and is not copied into queue payloads.
