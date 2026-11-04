# FinGPT web

React, TypeScript, and Vite application with authenticated dashboard,
transaction history, and chat. The warm editorial workspace uses a cream,
forest, sage, and terracotta palette with responsive top/bottom navigation.
Dashboard charts use
confirmed monthly VND totals and only the recent transactions loaded on the
dashboard; they do not represent bank balances or a complete category report.

The **Chat** screen answers questions and creates text or single-image draft previews using
the existing backend APIs. Attach JPEG/PNG/WebP with the plus button or paste
an image into the composer (10 MiB maximum). Text and image must be sent
separately because the image API does not accept accompanying text. Every
draft still requires review and explicit per-item confirmation. Conversation
turns are kept in memory for the current browser session.

The **Transactions** screen owns the ledger. Its CSV download exports only the
currently loaded, filtered page (up to 20 rows), not
the entire account history. The CSV includes optional clock time and escapes
spreadsheet formulas in untrusted fields. A blank time means no clock time
was recorded; the day period is still shown independently.

## Commands

```bash
pnpm install --frozen-lockfile
pnpm dev
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
node --test tests/*.test.mjs
```

The web app calls the same-origin backend API in production. Do not put secrets
or trusted transaction logic in this package.

For local browser testing, start the backend and run `pnpm dev`. Authentication
and real transaction flows require a locally bootstrapped test account; do not
use production credentials or financial records. API-mocked Playwright checks
exercise UI behavior but do not verify persistence or backend integration.
