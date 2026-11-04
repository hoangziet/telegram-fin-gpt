import { useState } from 'react';
import {
  cancelTransactionChange,
  confirmTransactionChange,
  getTransactionRevisions,
  listProposedChanges,
  proposeTransactionChange,
  proposeTransactionUndo,
  type TransactionChange,
  type TransactionRecord,
  type TransactionRevision,
} from '../api/finances';
import './transaction-actions.css';
import { t, useLanguage, type Language } from '../i18n';

interface Props {
  transaction: TransactionRecord;
  csrfToken: string;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
}

interface EditValues {
  description: string;
  amount: string;
  kind: TransactionRecord['kind'];
  transactionDate: string;
  dayPeriod: string;
  localTime: string;
}

export default function TransactionActions({ transaction, csrfToken, onChanged, onError }: Props) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<EditValues>(() => valuesFrom(transaction));
  const [proposal, setProposal] = useState<TransactionChange | null>(null);
  const [revisions, setRevisions] = useState<TransactionRevision[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function prepare(action: 'updated' | 'deleted') {
    setBusy(true);
    onError('');
    try {
      const pending = await listProposedChanges(transaction.id);
      setProposal(
        pending[0] ?? (await proposeTransactionChange(transaction, values, csrfToken, action)),
      );
      setEditing(false);
    } catch (error) {
      onError(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function prepareUndo() {
    setBusy(true);
    onError('');
    try {
      const pending = await listProposedChanges(transaction.id);
      setProposal(pending[0] ?? (await proposeTransactionUndo(transaction, csrfToken)));
      setEditing(false);
    } catch (error) {
      onError(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!proposal) return;
    setBusy(true);
    onError('');
    try {
      await confirmTransactionChange(proposal, csrfToken);
      setProposal(null);
      await onChanged();
    } catch (error) {
      onError(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!proposal) return;
    setBusy(true);
    onError('');
    try {
      await cancelTransactionChange(proposal, csrfToken);
      setProposal(null);
    } catch (error) {
      onError(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function showHistory() {
    setBusy(true);
    onError('');
    try {
      setRevisions(await getTransactionRevisions(transaction.id));
    } catch (error) {
      onError(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="transaction-actions">
      {proposal ? (
        <ProposalPreview
          transaction={transaction}
          proposal={proposal}
          busy={busy}
          onConfirm={confirm}
          onCancel={cancel}
        />
      ) : (
        <div className="transaction-action-buttons">
          {transaction.isDeleted ? (
            <button type="button" className="text-button" onClick={prepareUndo} disabled={busy}>
              {label('Hoàn tác')}
            </button>
          ) : (
            <>
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setValues(valuesFrom(transaction));
                  setEditing(!editing);
                }}
                disabled={busy}
              >
                {label('Sửa')}
              </button>
              <button
                type="button"
                className="text-button danger-text"
                onClick={() => void prepare('deleted')}
                disabled={busy}
              >
                {label('Xóa')}
              </button>
            </>
          )}
          <button type="button" className="text-button" onClick={showHistory} disabled={busy}>
            {label('Lịch sử')}
          </button>
        </div>
      )}
      {editing && !transaction.isDeleted && (
        <EditPreviewForm
          values={values}
          currency={transaction.amount.currency}
          busy={busy}
          onChange={setValues}
          onSubmit={() => void prepare('updated')}
          onCancel={() => setEditing(false)}
        />
      )}
      {revisions && <RevisionHistory revisions={revisions} onClose={() => setRevisions(null)} />}
    </div>
  );
}

function EditPreviewForm(props: {
  values: EditValues;
  currency: string;
  busy: boolean;
  onChange: (values: EditValues) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const update = <K extends keyof EditValues>(key: K, value: EditValues[K]) =>
    props.onChange({ ...props.values, [key]: value });
  return (
    <form
      className="transaction-edit-form"
      onSubmit={(event) => {
        event.preventDefault();
        props.onSubmit();
      }}
    >
      <label className="field">
        <span>{label('Nội dung')}</span>
        <input
          autoFocus
          value={props.values.description}
          onChange={(event) => update('description', event.currentTarget.value)}
          maxLength={2_000}
        />
      </label>
      <label className="field">
        <span>{label('Loại')}</span>
        <select
          value={props.values.kind}
          onChange={(event) =>
            update('kind', event.currentTarget.value === 'income' ? 'income' : 'expense')
          }
        >
          <option value="expense">{label('Tiền ra')}</option>
          <option value="income">{label('Tiền vào')}</option>
        </select>
      </label>
      <label className="field">
        <span>
          {label('Số tiền')} ({props.currency})
        </span>
        <input
          inputMode="decimal"
          value={props.values.amount}
          onChange={(event) => update('amount', event.currentTarget.value)}
          required
        />
      </label>
      <label className="field">
        <span>{label('Ngày')}</span>
        <input
          type="date"
          value={props.values.transactionDate}
          onChange={(event) => update('transactionDate', event.currentTarget.value)}
          required
        />
      </label>
      <label className="field">
        <span>{label('Buổi')}</span>
        <select
          value={props.values.dayPeriod}
          onChange={(event) => update('dayPeriod', event.currentTarget.value)}
        >
          <option value="morning">{label('Sáng')}</option>
          <option value="noon">{label('Trưa')}</option>
          <option value="afternoon">{label('Chiều')}</option>
          <option value="evening">{label('Tối')}</option>
        </select>
      </label>
      <label className="field">
        <span>{label('Giờ cụ thể (không bắt buộc)')}</span>
        <input
          type="time"
          value={props.values.localTime}
          onChange={(event) => update('localTime', event.currentTarget.value)}
        />
      </label>
      <div className="transaction-form-actions">
        <button type="submit" className="primary-button" disabled={props.busy}>
          {label('Xem trước thay đổi')}
        </button>
        <button type="button" className="text-button" onClick={props.onCancel}>
          {label('Bỏ qua')}
        </button>
      </div>
    </form>
  );
}

function ProposalPreview(props: {
  transaction: TransactionRecord;
  proposal: TransactionChange;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const actionLabel =
    props.proposal.action === 'deleted'
      ? 'xóa'
      : props.proposal.action === 'restored'
        ? 'khôi phục'
        : 'sửa';
  const state = props.proposal.proposedState;
  const before = transactionSummary(
    {
      note: props.transaction.description,
      amount: props.transaction.amount.amount,
      currency: props.transaction.amount.currency,
      transactionDate: props.transaction.transactionDate,
      dayPeriod: props.transaction.dayPeriod,
      localTime: props.transaction.localTime,
    },
    language,
  );
  const after = transactionSummary(state, language);
  return (
    <section className="change-preview" aria-label={label('Bản xem trước thay đổi')}>
      <strong>
        {label('Xem trước:')} {label(actionLabel)} {label('giao dịch')}
      </strong>
      <p>
        <strong>{label('Hiện tại:')}</strong> {before}
      </p>
      <p>
        <strong>{label('Sau khi xác nhận:')}</strong> {after}
      </p>
      {state.isDeleted && <p>{label('Giao dịch sẽ được ẩn khỏi sổ và báo cáo.')}</p>}
      <div className="transaction-form-actions">
        <button
          type="button"
          className="primary-button"
          onClick={props.onConfirm}
          disabled={props.busy}
        >
          {label(props.busy ? 'Đang lưu…' : 'Xác nhận thay đổi')}
        </button>
        <button
          type="button"
          className="text-button"
          onClick={props.onCancel}
          disabled={props.busy}
        >
          {label('Hủy bản xem trước')}
        </button>
      </div>
    </section>
  );
}

function RevisionHistory(props: { revisions: TransactionRevision[]; onClose: () => void }) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  return (
    <section className="revision-history" aria-label={label('Lịch sử giao dịch')}>
      <div className="revision-heading">
        <h3>{label('Lịch sử thay đổi')}</h3>
        <button type="button" className="text-button" onClick={props.onClose}>
          {label('Đóng')}
        </button>
      </div>
      {props.revisions.map((revision) => (
        <article className="revision-entry" key={revision.id}>
          <strong>
            {label('Phiên bản')} {revision.revisionNumber} ·{' '}
            {label(revisionActionLabel(revision.action))}
          </strong>
          <time dateTime={revision.createdAt}>
            {new Date(revision.createdAt).toLocaleString('en-US')}
          </time>
          <p>
            {revision.afterState
              ? revisionSummary(revision.afterState, language)
              : label('Không còn dữ liệu.')}
          </p>
        </article>
      ))}
      {!props.revisions.length && <p>{label('Chưa có lịch sử thay đổi.')}</p>}
    </section>
  );
}

function valuesFrom(transaction: TransactionRecord): EditValues {
  return {
    description: transaction.description ?? '',
    amount: transaction.amount.amount.replace(/\.0+$/, ''),
    kind: transaction.kind,
    transactionDate: transaction.transactionDate,
    dayPeriod: transaction.dayPeriod,
    localTime: transaction.localTime ?? '',
  };
}

function revisionSummary(
  state: NonNullable<TransactionRevision['afterState']>,
  language: Language,
): string {
  return `${transactionSummary(state, language)}${state.isDeleted ? ` · ${t(language, 'Đã xóa')}` : ''}`;
}

function transactionSummary(
  state: {
    note: string | null;
    amount: string;
    currency: string;
    transactionDate: string;
    dayPeriod: string;
    localTime: string | null;
  },
  language: Language,
): string {
  const summary = [
    state.note || t(language, 'Chưa có nội dung'),
    formatAmount(state.amount, state.currency),
    state.transactionDate,
    t(language, periodLabel(state.dayPeriod)),
    state.localTime,
  ];
  return summary.filter(Boolean).join(' · ');
}

function formatAmount(amount: string, currency: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
  if (!match) return `${amount} ${currency}`;
  const sign = match[1] ?? '';
  const whole = (match[2] ?? '0').replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const fraction = (match[3] ?? '').replace(/0+$/, '');
  return `${sign}${whole}${fraction ? `,${fraction}` : ''} ${currency}`;
}

function periodLabel(value: string): string {
  const labels: Record<string, string> = {
    morning: 'Sáng',
    noon: 'Trưa',
    afternoon: 'Chiều',
    evening: 'Tối',
  };
  return labels[value] ?? value;
}

function revisionActionLabel(action: TransactionRevision['action']): string {
  const labels: Record<TransactionRevision['action'], string> = {
    created: 'Tạo mới',
    updated: 'Đã sửa',
    deleted: 'Đã xóa',
    restored: 'Đã khôi phục',
  };
  return labels[action];
}

function messageFor(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'Không thể kết nối. Hãy thử lại khi mạng ổn định.';
}
