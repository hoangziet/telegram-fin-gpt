import { ApiError } from './client';

export interface MoneyValue {
  amount: string;
  currency: string;
}

export interface DraftItem {
  id: string;
  index: number;
  status: 'incomplete' | 'ready' | 'confirmed' | 'cancelled';
  version: number;
  rowNumber: number | null;
  category: string;
  rowErrors: Record<string, string>;
  description: string | null;
  kind: 'income' | 'expense';
  amount: MoneyValue | null;
  currency: string | null;
  transactionDate: string | null;
  dayPeriod: string | null;
  localTime: string | null;
  missingFields: string[];
}

export interface DraftPreview {
  id: string;
  evidence: {
    id: string;
    sourceType: string;
    sourceText: string | null;
    originalFilename: string | null;
    mediaType: string | null;
    contentUrl: string | null;
  };
  items: DraftItem[];
}

export interface TransactionRecord {
  id: string;
  version: number;
  kind: 'income' | 'expense';
  amount: MoneyValue;
  description: string | null;
  transactionDate: string;
  localTime: string | null;
  dayPeriod: string;
  timezone: string;
  category: string;
  isDeleted: boolean;
}

export interface TransactionState {
  kind: 'income' | 'expense';
  amount: string;
  currency: string;
  category: string;
  note: string | null;
  transactionDate: string;
  localTime: string | null;
  dayPeriod: string;
  timezone: string;
  isDeleted: boolean;
}

type LegacyTransactionState = Omit<TransactionState, 'isDeleted'> & {
  isDeleted: boolean | null;
};

export interface TransactionChange {
  id: string;
  transactionId: string;
  action: 'updated' | 'deleted' | 'restored';
  status: 'proposed' | 'confirmed' | 'cancelled' | 'stale';
  expectedTransactionVersion: number;
  version: number;
  proposedState: TransactionState;
}

export interface TransactionRevision {
  id: string;
  revisionNumber: number;
  action: 'created' | 'updated' | 'deleted' | 'restored';
  beforeState: TransactionState | null;
  afterState: TransactionState | null;
  createdAt: string;
}

export interface TransactionPage {
  items: TransactionRecord[];
  total: number;
}

export interface TotalsReport {
  fromDate: string;
  toDate: string;
  totals: Array<{ kind: 'income' | 'expense'; currency: string; amount: string }>;
}

export interface EvidenceRecord {
  id: string;
  sourceType: string;
  sourceText: string | null;
  originalFilename: string | null;
  mediaType: string | null;
  byteSize: number;
  sha256: string | null;
  createdAt: string;
  contentUrl: string;
}

export async function createDraft(text: string, csrfToken: string): Promise<DraftPreview> {
  const payload = await request('/api/drafts', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ text }),
  });
  if (!isRecord(payload) || !isRecord(payload.draft)) throw invalidResponse();
  return parseDraft(payload.draft);
}

export async function uploadCsv(
  file: File,
  mapping: Record<string, string>,
  csrfToken: string,
  allowDuplicate: boolean,
  idempotencyKey: string,
): Promise<DraftPreview> {
  const form = new FormData();
  form.set('file', file);
  form.set('mapping', JSON.stringify(mapping));
  form.set('allowDuplicate', String(allowDuplicate));
  const payload = await request('/api/imports/csv', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken, 'Idempotency-Key': idempotencyKey },
    body: form,
  });
  if (!isRecord(payload) || !isRecord(payload.draft)) throw invalidResponse();
  return parseDraft(payload.draft);
}

export async function uploadImage(
  file: File,
  csrfToken: string,
  allowDuplicate: boolean,
  idempotencyKey: string,
): Promise<DraftPreview> {
  const form = new FormData();
  form.set('file', file);
  form.set('allowDuplicate', String(allowDuplicate));
  const payload = await request('/api/imports/image', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken, 'Idempotency-Key': idempotencyKey },
    body: form,
  });
  if (!isRecord(payload) || !isRecord(payload.draft)) throw invalidResponse();
  return parseDraft(payload.draft);
}

export async function listOpenDrafts(): Promise<DraftPreview[]> {
  const payload = await request('/api/drafts?limit=20');
  if (!isRecord(payload) || !Array.isArray(payload.items) || !payload.items.every(isRecord)) {
    throw invalidResponse();
  }
  return payload.items.map(parseDraft);
}

export type ChatMessageResult =
  | { intent: 'question'; answer: string }
  | { intent: 'clarification'; item: DraftItem }
  | { intent: 'transaction'; draft: DraftPreview };

export interface ChatDraftContext {
  draftId: string;
  itemId: string;
  expectedVersion: number;
}

export async function sendChatMessage(
  message: string,
  language: 'en',
  csrfToken: string,
  history: Array<{ role: 'user' | 'assistant'; text: string }>,
  idempotencyKey: string,
  draftContext?: ChatDraftContext,
): Promise<ChatMessageResult> {
  const payload = await request('/api/chat/message', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({
      message,
      language,
      ...(draftContext ? { draftContext } : {}),
      history: history.slice(-8).map((turn) => ({
        role: turn.role,
        text: turn.text.slice(0, 1_000),
      })),
    }),
  });
  if (!isRecord(payload)) throw invalidResponse();
  if (
    payload.intent === 'question' &&
    typeof payload.answer === 'string' &&
    payload.answer.trim()
  ) {
    return { intent: 'question', answer: payload.answer };
  }
  if (payload.intent === 'clarification' && isRecord(payload.item)) {
    return { intent: 'clarification', item: parseDraftItem(payload.item) };
  }
  if (payload.intent === 'transaction' && isRecord(payload.draft)) {
    return { intent: 'transaction', draft: parseDraft(payload.draft) };
  }
  throw invalidResponse();
}

export async function updateDraftItem(
  draftId: string,
  item: DraftItem,
  values: {
    transactionDate: string;
    dayPeriod: string;
    description: string;
    amount: string;
    currency: string;
    localTime?: string;
    kind?: DraftItem['kind'];
    category?: string;
  },
  csrfToken: string,
): Promise<DraftItem> {
  const payload = await request(`/api/drafts/${draftId}/items/${item.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({
      expectedVersion: item.version,
      ...(values.transactionDate ? { transactionDate: values.transactionDate } : {}),
      ...(values.dayPeriod ? { dayPeriod: values.dayPeriod } : {}),
      ...(values.description.trim() ? { description: values.description.trim() } : {}),
      ...(values.localTime ? { localTime: values.localTime } : {}),
      ...(values.kind ? { kind: values.kind } : {}),
      ...(values.category?.trim() ? { category: values.category.trim() } : {}),
      ...(values.amount.trim() && values.currency
        ? { amount: { amount: values.amount.trim(), currency: values.currency } }
        : {}),
    }),
  });
  if (!isRecord(payload) || !isRecord(payload.item)) throw invalidResponse();
  return parseDraftItem(payload.item);
}

export async function confirmDraftItem(
  draftId: string,
  item: DraftItem,
  csrfToken: string,
): Promise<TransactionRecord> {
  const payload = await request(`/api/drafts/${draftId}/items/${item.id}/confirm`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ expectedVersion: item.version }),
  });
  if (!isRecord(payload) || !isRecord(payload.transaction)) throw invalidResponse();
  return parseTransaction(payload.transaction);
}

export async function listTransactions(
  options: {
    query?: string;
    from?: string;
    to?: string;
    kind?: 'income' | 'expense';
    includeDeleted?: boolean;
    limit?: number;
    offset?: number;
  } = {},
): Promise<TransactionPage> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 100),
    offset: String(options.offset ?? 0),
  });
  if (options.query) params.set('q', options.query);
  if (options.from) params.set('from', options.from);
  if (options.to) params.set('to', options.to);
  if (options.kind) params.set('kind', options.kind);
  if (options.includeDeleted) params.set('includeDeleted', 'true');
  const payload = await request(`/api/transactions?${params}`);
  if (
    !isRecord(payload) ||
    !Array.isArray(payload.items) ||
    typeof payload.total !== 'number' ||
    !payload.items.every(isTransactionRecord)
  ) {
    throw invalidResponse();
  }
  return { items: payload.items, total: payload.total };
}

export async function getTransactionRevisions(
  transactionId: string,
): Promise<TransactionRevision[]> {
  const payload = await request(`/api/transactions/${transactionId}/revisions`);
  if (!isRecord(payload) || !Array.isArray(payload.items) || !payload.items.every(isRecord)) {
    throw invalidResponse();
  }
  return payload.items.map(parseRevision);
}

export async function listProposedChanges(transactionId: string): Promise<TransactionChange[]> {
  const payload = await request(`/api/transactions/${transactionId}/changes`);
  if (!isRecord(payload) || !Array.isArray(payload.items) || !payload.items.every(isRecord)) {
    throw invalidResponse();
  }
  return payload.items.map(parseChange);
}

export async function proposeTransactionChange(
  transaction: TransactionRecord,
  values: {
    description: string;
    amount: string;
    kind: TransactionRecord['kind'];
    transactionDate: string;
    dayPeriod: string;
    localTime: string;
  },
  csrfToken: string,
  action: 'updated' | 'deleted' = 'updated',
): Promise<TransactionChange> {
  const payload = await request(`/api/transactions/${transaction.id}/changes`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({
      action,
      expectedVersion: transaction.version,
      ...(action === 'updated'
        ? {
            description: values.description,
            amount: { amount: values.amount, currency: transaction.amount.currency },
            kind: values.kind,
            transactionDate: values.transactionDate,
            dayPeriod: values.dayPeriod,
            localTime: values.localTime.trim() || null,
          }
        : {}),
    }),
  });
  if (!isRecord(payload) || !isRecord(payload.change)) throw invalidResponse();
  return parseChange(payload.change);
}

export async function proposeTransactionUndo(
  transaction: TransactionRecord,
  csrfToken: string,
): Promise<TransactionChange> {
  const payload = await request(`/api/transactions/${transaction.id}/undo`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ expectedVersion: transaction.version }),
  });
  if (!isRecord(payload) || !isRecord(payload.change)) throw invalidResponse();
  return parseChange(payload.change);
}

export async function confirmTransactionChange(
  change: TransactionChange,
  csrfToken: string,
): Promise<TransactionRecord> {
  const payload = await request(`/api/transactions/changes/${change.id}/confirm`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ expectedVersion: change.version }),
  });
  if (!isRecord(payload) || !isRecord(payload.transaction)) throw invalidResponse();
  return parseTransaction(payload.transaction);
}

export async function cancelTransactionChange(
  change: TransactionChange,
  csrfToken: string,
): Promise<void> {
  await request(`/api/transactions/changes/${change.id}/cancel`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ expectedVersion: change.version }),
  });
}

export async function getTotals(
  from: string,
  to: string,
  options: { query?: string; kind?: 'income' | 'expense' } = {},
): Promise<TotalsReport> {
  const params = new URLSearchParams({ from, to });
  if (options.query) params.set('q', options.query);
  if (options.kind) params.set('kind', options.kind);
  const payload = await request(`/api/transactions/reports/totals?${params}`);
  if (
    !isRecord(payload) ||
    typeof payload.fromDate !== 'string' ||
    typeof payload.toDate !== 'string' ||
    !Array.isArray(payload.totals) ||
    !payload.totals.every(isTotalsRow)
  ) {
    throw invalidResponse();
  }
  return { fromDate: payload.fromDate, toDate: payload.toDate, totals: payload.totals };
}

export async function getEvidence(transactionId: string): Promise<EvidenceRecord> {
  const payload = await request(`/api/transactions/${transactionId}/evidence`);
  if (!isRecord(payload) || !isRecord(payload.evidence)) throw invalidResponse();
  return parseEvidence(payload.evidence, `/api/transactions/${transactionId}/evidence/content`);
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: 'application/json', ...init.headers },
    credentials: 'same-origin',
    cache: 'no-store',
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const errorMessage =
      isRecord(payload) && isRecord(payload.error) && typeof payload.error.message === 'string'
        ? payload.error.message
        : 'Yêu cầu không thể hoàn tất.';
    throw new ApiError(errorMessage, response.status);
  }
  return payload;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function invalidResponse(): ApiError {
  return new ApiError('Máy chủ trả về dữ liệu không hợp lệ.', 502);
}

function parseDraft(value: Record<string, unknown>): DraftPreview {
  if (
    typeof value.id !== 'string' ||
    !isRecord(value.evidence) ||
    typeof value.evidence.id !== 'string' ||
    typeof value.evidence.sourceType !== 'string' ||
    !isNullableString(value.evidence.sourceText) ||
    !isNullableString(value.evidence.originalFilename) ||
    !isNullableString(value.evidence.mediaType) ||
    !Array.isArray(value.items) ||
    !value.items.every(isDraftItem)
  ) {
    throw invalidResponse();
  }
  return {
    id: value.id,
    evidence: {
      id: value.evidence.id,
      sourceType: value.evidence.sourceType,
      sourceText: value.evidence.sourceText,
      originalFilename: value.evidence.originalFilename,
      mediaType: value.evidence.mediaType,
      contentUrl:
        value.evidence.sourceType === 'text' ? null : `/api/drafts/${value.id}/evidence/content`,
    },
    items: value.items,
  };
}

function parseDraftItem(value: Record<string, unknown>): DraftItem {
  if (!isDraftItem(value)) throw invalidResponse();
  return value;
}

function isDraftItem(value: unknown): value is DraftItem {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.index === 'number' &&
    isDraftStatus(value.status) &&
    typeof value.version === 'number' &&
    (value.rowNumber === null || typeof value.rowNumber === 'number') &&
    typeof value.category === 'string' &&
    isNullableString(value.currency) &&
    isRecord(value.rowErrors) &&
    Object.values(value.rowErrors).every((error) => typeof error === 'string') &&
    isNullableString(value.description) &&
    (value.kind === 'income' || value.kind === 'expense') &&
    (value.amount === null || isMoneyValue(value.amount)) &&
    isNullableString(value.transactionDate) &&
    isNullableString(value.dayPeriod) &&
    isNullableString(value.localTime) &&
    Array.isArray(value.missingFields) &&
    value.missingFields.every((field) => typeof field === 'string')
  );
}

function isTransactionRecord(value: unknown): value is TransactionRecord {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.version === 'number' &&
    (value.kind === 'income' || value.kind === 'expense') &&
    isMoneyValue(value.amount) &&
    isNullableString(value.description) &&
    typeof value.transactionDate === 'string' &&
    isNullableString(value.localTime) &&
    typeof value.dayPeriod === 'string' &&
    typeof value.timezone === 'string' &&
    typeof value.category === 'string' &&
    typeof value.isDeleted === 'boolean'
  );
}

function parseRevision(value: Record<string, unknown>): TransactionRevision {
  if (
    typeof value.id !== 'string' ||
    typeof value.revisionNumber !== 'number' ||
    !isRevisionAction(value.action) ||
    !(value.beforeState === null || isRevisionState(value.beforeState)) ||
    !(value.afterState === null || isRevisionState(value.afterState)) ||
    typeof value.createdAt !== 'string'
  ) {
    throw invalidResponse();
  }
  return {
    id: value.id,
    revisionNumber: value.revisionNumber,
    action: value.action,
    beforeState: value.beforeState === null ? null : parseTransactionState(value.beforeState),
    afterState: value.afterState === null ? null : parseTransactionState(value.afterState),
    createdAt: value.createdAt,
  };
}

function parseChange(value: Record<string, unknown>): TransactionChange {
  if (
    typeof value.id !== 'string' ||
    typeof value.transactionId !== 'string' ||
    !isChangeAction(value.action) ||
    !isChangeStatus(value.status) ||
    typeof value.expectedTransactionVersion !== 'number' ||
    typeof value.version !== 'number' ||
    !isTransactionState(value.proposedState)
  ) {
    throw invalidResponse();
  }
  return {
    id: value.id,
    transactionId: value.transactionId,
    action: value.action,
    status: value.status,
    expectedTransactionVersion: value.expectedTransactionVersion,
    version: value.version,
    proposedState: parseTransactionState(value.proposedState),
  };
}

function parseTransactionState(value: unknown): TransactionState {
  if (!isRevisionState(value)) throw invalidResponse();
  return {
    kind: value.kind,
    amount: value.amount,
    currency: value.currency,
    category: value.category,
    note: value.note,
    transactionDate: value.transactionDate,
    localTime: value.localTime,
    dayPeriod: value.dayPeriod,
    timezone: value.timezone,
    isDeleted: value.isDeleted === true,
  };
}

function isTransactionState(value: unknown): value is TransactionState {
  return (
    isRecord(value) &&
    (value.kind === 'income' || value.kind === 'expense') &&
    typeof value.amount === 'string' &&
    typeof value.currency === 'string' &&
    typeof value.category === 'string' &&
    isNullableString(value.note) &&
    typeof value.transactionDate === 'string' &&
    isNullableString(value.localTime) &&
    typeof value.dayPeriod === 'string' &&
    typeof value.timezone === 'string' &&
    typeof value.isDeleted === 'boolean'
  );
}

function isRevisionState(value: unknown): value is TransactionState | LegacyTransactionState {
  return (
    isRecord(value) &&
    (value.kind === 'income' || value.kind === 'expense') &&
    typeof value.amount === 'string' &&
    typeof value.currency === 'string' &&
    typeof value.category === 'string' &&
    isNullableString(value.note) &&
    typeof value.transactionDate === 'string' &&
    isNullableString(value.localTime) &&
    typeof value.dayPeriod === 'string' &&
    typeof value.timezone === 'string' &&
    (typeof value.isDeleted === 'boolean' || value.isDeleted === null)
  );
}

function isRevisionAction(value: unknown): value is TransactionRevision['action'] {
  return value === 'created' || value === 'updated' || value === 'deleted' || value === 'restored';
}

function isChangeAction(value: unknown): value is TransactionChange['action'] {
  return value === 'updated' || value === 'deleted' || value === 'restored';
}

function isChangeStatus(value: unknown): value is TransactionChange['status'] {
  return (
    value === 'proposed' || value === 'confirmed' || value === 'cancelled' || value === 'stale'
  );
}

function parseTransaction(value: Record<string, unknown>): TransactionRecord {
  if (!isTransactionRecord(value)) throw invalidResponse();
  return value;
}

function isMoneyValue(value: unknown): value is MoneyValue {
  return isRecord(value) && typeof value.amount === 'string' && typeof value.currency === 'string';
}

function isTotalsRow(value: unknown): value is TotalsReport['totals'][number] {
  return (
    isRecord(value) &&
    (value.kind === 'income' || value.kind === 'expense') &&
    typeof value.currency === 'string' &&
    typeof value.amount === 'string'
  );
}

function parseEvidence(value: Record<string, unknown>, contentUrl: string): EvidenceRecord {
  if (
    typeof value.id !== 'string' ||
    typeof value.sourceType !== 'string' ||
    !isNullableString(value.sourceText) ||
    !isNullableString(value.originalFilename) ||
    !isNullableString(value.mediaType) ||
    typeof value.byteSize !== 'number' ||
    !isNullableString(value.sha256) ||
    typeof value.createdAt !== 'string'
  ) {
    throw invalidResponse();
  }
  return {
    id: value.id,
    sourceType: value.sourceType,
    sourceText: value.sourceText,
    originalFilename: value.originalFilename,
    mediaType: value.mediaType,
    byteSize: value.byteSize,
    sha256: value.sha256,
    createdAt: value.createdAt,
    contentUrl,
  };
}

function isDraftStatus(value: unknown): value is DraftItem['status'] {
  return (
    value === 'incomplete' || value === 'ready' || value === 'confirmed' || value === 'cancelled'
  );
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null;
}
