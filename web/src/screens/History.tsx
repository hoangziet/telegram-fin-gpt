import { useEffect, useMemo, useState } from 'react';
import {
  getTotals,
  listTransactions,
  type EvidenceRecord,
  type TotalsReport,
  type TransactionRecord,
} from '../api/finances';
import TransactionActions from '../components/TransactionActions';
import { FileSpreadsheetIcon, ReceiptIcon, SearchIcon } from '../components/Icons';
import { exportTransactionsCsv } from '../utils/exportTransactionsCsv';
import { formatMoney, periodLabel } from '../utils/financeFormatters';
import { currencyBreakdown } from '../utils/historyAnalysis';
import transactionsEmpty from '../assets/transactions-empty.png';

interface HistoryProps {
  csrfToken: string;
  query: string;
  onQueryChange: (value: string) => void;
  onEvidence: (id: string) => void;
  evidence: EvidenceRecord | null;
  evidenceLoading: boolean;
  onCloseEvidence: () => void;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
}

type KindFilter = 'all' | 'income' | 'expense';
type DateFilter = 'month' | 'day' | 'all';
const PAGE_SIZE = 20;

function currentLocalDate(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Ho_Chi_Minh',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function dateRange(
  mode: DateFilter,
  month: string,
  day: string,
): { from: string; to: string } | null {
  if (mode === 'all') return null;
  if (mode === 'day') {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
    const parsed = new Date(`${day}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== day) return null;
    return { from: day, to: day };
  }
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return null;
  const [year = '0', selectedMonth = '0'] = month.split('-');
  const lastDay = new Date(Date.UTC(Number(year), Number(selectedMonth), 0)).getUTCDate();
  return { from: `${month}-01`, to: `${month}-${String(lastDay).padStart(2, '0')}` };
}

export default function History(props: HistoryProps) {
  const { query, onError } = props;
  const [kind, setKind] = useState<KindFilter>('all');
  const [dateMode, setDateMode] = useState<DateFilter>('month');
  const [month, setMonth] = useState(() => currentLocalDate().slice(0, 7));
  const [day, setDay] = useState(currentLocalDate);
  const [showDeleted, setShowDeleted] = useState(false);
  const [page, setPage] = useState(0);
  const [revision, setRevision] = useState(0);
  const [items, setItems] = useState<TransactionRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<TotalsReport | null>(null);
  const [loading, setLoading] = useState(true);
  const range = useMemo(() => dateRange(dateMode, month, day), [dateMode, month, day]);
  const from = range?.from;
  const to = range?.to;
  const invalidDate = dateMode !== 'all' && !range;
  const displayLoading = loading && !invalidDate;
  const displayItems = invalidDate ? [] : items;
  const displayTotal = invalidDate ? 0 : total;

  useEffect(() => {
    if (invalidDate) return;
    let active = true;
    const selectedKind = kind === 'all' ? undefined : kind;
    Promise.all([
      listTransactions({
        query,
        ...(from && to ? { from, to } : {}),
        ...(selectedKind ? { kind: selectedKind } : {}),
        includeDeleted: showDeleted,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
      from && to
        ? getTotals(from, to, { query, ...(selectedKind ? { kind: selectedKind } : {}) })
        : Promise.resolve(null),
    ])
      .then(([result, totals]) => {
        if (!active) return;
        setItems(result.items);
        setTotal(result.total);
        setSummary(totals);
        setLoading(false);
        if (result.total > 0 && page * PAGE_SIZE >= result.total) {
          setPage(Math.floor((result.total - 1) / PAGE_SIZE));
        }
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setItems([]);
        setTotal(0);
        setSummary(null);
        setLoading(false);
        onError(reason instanceof Error ? reason.message : 'Không thể tải giao dịch.');
      });
    return () => {
      active = false;
    };
  }, [query, onError, kind, from, to, invalidDate, showDeleted, page, revision]);

  function updateFilter(change: () => void) {
    setLoading(true);
    setSummary(null);
    setPage(0);
    change();
  }

  async function refreshAfterChange() {
    await props.onChanged();
    setLoading(true);
    setRevision((current) => current + 1);
  }

  function downloadCsv() {
    const url = URL.createObjectURL(
      new Blob([exportTransactionsCsv(displayItems)], { type: 'text/csv;charset=utf-8' }),
    );
    const period = dateMode === 'day' ? day : dateMode === 'month' ? month : 'all';
    const link = document.createElement('a');
    link.href = url;
    link.download = `fingpt-transactions-${period}-page-${page + 1}.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return (
    <div className="history-view">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Phân tích giao dịch</span>
          <h1 className="page-title">Lịch sử giao dịch</h1>
          <p className="page-subtitle">Mọi giao dịch, được sắp xếp rõ ràng.</p>
        </div>
        <button
          type="button"
          className="btn-secondary history-export"
          onClick={downloadCsv}
          disabled={displayLoading || !displayItems.length}
        >
          <FileSpreadsheetIcon size={16} /> {'Xuất CSV trang đang xem'}
        </button>
      </div>

      <section className="bento-card history-filters" aria-label={'Bộ lọc giao dịch'}>
        <div className="history-filter-top">
          <div className="history-date-options" role="group" aria-label={'Khoảng thời gian'}>
            {(['month', 'day', 'all'] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={dateMode === option}
                className={`filter-chip ${dateMode === option ? 'is-active' : ''}`}
                onClick={() => updateFilter(() => setDateMode(option))}
              >
                {option === 'month'
                  ? 'Theo tháng'
                  : option === 'day'
                    ? 'Theo ngày'
                    : 'Mọi thời gian'}
              </button>
            ))}
          </div>
          {dateMode === 'month' && (
            <label className="history-date-field">
              {'Chọn tháng'}
              <input
                type="month"
                value={month}
                aria-label={'Chọn tháng'}
                onChange={(event) => updateFilter(() => setMonth(event.currentTarget.value))}
              />
            </label>
          )}
          {dateMode === 'day' && (
            <label className="history-date-field">
              {'Chọn ngày'}
              <input
                type="date"
                value={day}
                aria-label={'Chọn ngày'}
                onChange={(event) => updateFilter(() => setDay(event.currentTarget.value))}
              />
            </label>
          )}
        </div>
        <div className="history-filter-bottom">
          <div className="search-input-wrapper">
            <SearchIcon size={18} className="search-icon" />
            <input
              id="transaction-search"
              aria-label={'Tìm giao dịch'}
              className="search-input"
              type="search"
              maxLength={128}
              value={props.query}
              onChange={(event) =>
                updateFilter(() => props.onQueryChange(event.currentTarget.value))
              }
              placeholder={'Tìm theo nội dung giao dịch...'}
            />
            {props.query && (
              <button
                type="button"
                className="btn-clear-search"
                aria-label={'Xóa tìm kiếm'}
                onClick={() => updateFilter(() => props.onQueryChange(''))}
              >
                ✕
              </button>
            )}
          </div>
          <div className="filter-chips-group" role="group" aria-label={'Loại giao dịch'}>
            {(['all', 'income', 'expense'] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={kind === option}
                className={`filter-chip ${kind === option ? 'is-active' : ''}`}
                onClick={() => updateFilter(() => setKind(option))}
              >
                {option === 'all' ? 'Tất cả' : option === 'income' ? 'Tiền vào (+)' : 'Tiền ra (-)'}
              </button>
            ))}
          </div>
          <label className="toggle-label history-deleted-toggle">
            <input
              type="checkbox"
              checked={showDeleted}
              onChange={(event) => updateFilter(() => setShowDeleted(event.currentTarget.checked))}
            />
            {'Hiện giao dịch đã xóa'}
          </label>
        </div>
        {invalidDate && (
          <p className="history-filter-error" role="alert">
            {'Vui lòng chọn ngày hoặc tháng hợp lệ.'}
          </p>
        )}
      </section>

      {range && (
        <section className="history-analysis" aria-label={'Tổng hợp theo đơn vị tiền'}>
          <div className="history-analysis-heading">
            <h2>{'Dòng tiền trong kỳ'}</h2>
            <span>{range.from === range.to ? range.from : `${range.from} — ${range.to}`}</span>
          </div>
          {currencyBreakdown(summary).length ? (
            <div className="history-currency-grid">
              {currencyBreakdown(summary).map((row) => (
                <article key={row.currency} className="bento-card history-currency-card">
                  <h3>{row.currency}</h3>
                  <dl>
                    <div>
                      <dt>{'Thu nhập'}</dt>
                      <dd className="amount-income">{row.income}</dd>
                    </div>
                    <div>
                      <dt>{'Chi tiêu'}</dt>
                      <dd className="amount-expense">{row.expense}</dd>
                    </div>
                    <div className="history-net">
                      <dt>{'Dòng tiền ròng'}</dt>
                      <dd>{row.net}</dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          ) : (
            <p className="history-summary-empty">
              {displayLoading ? 'Đang tải tổng hợp…' : 'Chưa có giao dịch đã ghi trong kỳ.'}
            </p>
          )}
          {showDeleted && (
            <p className="history-summary-note">{'Tổng hợp không tính các giao dịch đã xóa.'}</p>
          )}
        </section>
      )}

      <section className="bento-card history-card" aria-label={'Danh sách giao dịch'}>
        <div className="history-table-heading">
          <div>
            <h2>{'Chi tiết giao dịch'}</h2>
            <p>
              {'Mới nhất trước; tổng số tính trên toàn bộ kết quả khớp, không chỉ trang đang xem.'}
            </p>
          </div>
          <strong aria-live="polite">
            {displayTotal} {'khoản phù hợp'}
          </strong>
        </div>
        {displayLoading ? (
          <div className="loading-container" role="status">
            <span className="spinner" />
            <span>{'Đang tải danh sách giao dịch…'}</span>
          </div>
        ) : displayItems.length ? (
          <div className="table-wrapper">
            <table className="modern-table transaction-history-table">
              <thead>
                <tr>
                  <th>{'Khoản giao dịch'}</th>
                  <th>{'Ngày / buổi'}</th>
                  <th className="numeric">{'Số tiền'}</th>
                  <th>{'Chứng từ'}</th>
                  <th>{'Thao tác'}</th>
                </tr>
              </thead>
              <tbody>
                {displayItems.map((item) => (
                  <tr key={item.id} className={item.isDeleted ? 'row-deleted' : ''}>
                    <td>
                      <div className="item-cell">
                        <span className="item-desc">{item.description || 'Chưa có nội dung'}</span>
                        <div className="tags-row">
                          <span className={`kind-tag kind-${item.kind}`}>
                            {item.kind === 'income' ? 'Thu (+)' : 'Chi (-)'}
                          </span>
                          {item.category && item.category !== 'uncategorized' && (
                            <span className="category-tag">{item.category}</span>
                          )}
                          {item.isDeleted && <span className="deleted-tag">{'Đã xóa'}</span>}
                        </div>
                      </div>
                    </td>
                    <td className="history-date-cell">
                      <time dateTime={item.transactionDate}>{item.transactionDate}</time>
                      <span>
                        {periodLabel(item.dayPeriod)}
                        {item.localTime ? ` · ${item.localTime}` : ''}
                      </span>
                    </td>
                    <td className={`numeric tabular-nums amount-${item.kind}`}>
                      {item.kind === 'income' ? '+' : '-'} {formatMoney(item.amount)}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-link"
                        onClick={() => props.onEvidence(item.id)}
                      >
                        <ReceiptIcon size={14} /> {'Chứng từ'}
                      </button>
                    </td>
                    <td>
                      <TransactionActions
                        transaction={item}
                        csrfToken={props.csrfToken}
                        onChanged={refreshAfterChange}
                        onError={props.onError}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-box">
            <img src={transactionsEmpty} alt="" />
            <p>
              {props.query.trim() || kind !== 'all'
                ? 'Không tìm thấy giao dịch phù hợp. Thử thay đổi từ khóa hoặc bộ lọc.'
                : dateMode === 'all' && !showDeleted
                  ? 'Chưa có giao dịch nào được ghi nhận.'
                  : 'Không có giao dịch trong khoảng thời gian đã chọn.'}
            </p>
          </div>
        )}
        <nav className="history-pagination" aria-label={'Phân trang giao dịch'}>
          <span>
            {displayTotal
              ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, displayTotal)} / ${displayTotal}`
              : `0 / 0`}
          </span>
          <div>
            <button
              type="button"
              className="btn-secondary"
              disabled={displayLoading || page === 0}
              onClick={() => {
                setLoading(true);
                setPage(page - 1);
              }}
            >
              {'Trang trước'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={displayLoading || (page + 1) * PAGE_SIZE >= displayTotal}
              onClick={() => {
                setLoading(true);
                setPage(page + 1);
              }}
            >
              {'Trang sau'}
            </button>
          </div>
        </nav>
      </section>

      {props.evidenceLoading && (
        <div className="bento-card notice-card" role="status">
          <span className="spinner" />
          <span>{'Đang mở chứng từ gốc…'}</span>
        </div>
      )}
      {props.evidence && (
        <section className="bento-card evidence-drawer-card" aria-label={'Chứng từ gốc'}>
          <div className="drawer-header">
            <h3>{'Chứng từ gốc'}</h3>
            <button
              type="button"
              className="btn-close"
              aria-label={'Đóng chứng từ'}
              onClick={props.onCloseEvidence}
            >
              ✕
            </button>
          </div>
          <p className="evidence-type-badge">
            {props.evidence.sourceType === 'text'
              ? 'Tin nhắn gốc'
              : props.evidence.originalFilename || 'Tệp đính kèm'}
          </p>
          {props.evidence.sourceText && (
            <blockquote className="evidence-quote">{props.evidence.sourceText}</blockquote>
          )}
          {props.evidence.mediaType?.startsWith('image/') && (
            <img
              className="evidence-drawer-img"
              src={props.evidence.contentUrl}
              alt={'Ảnh chứng từ gốc'}
            />
          )}
          {props.evidence.sourceType === 'csv' && (
            <a className="evidence-link" href={props.evidence.contentUrl} download>
              <FileSpreadsheetIcon size={16} /> {'Tải tệp CSV gốc'}
            </a>
          )}
          <div className="evidence-meta-list">
            <div>
              <span>{'Thời gian lưu:'}</span>
              <strong>{new Date(props.evidence.createdAt).toLocaleString('en-US')}</strong>
            </div>
            <div>
              <span>{'Dung lượng:'}</span>
              <strong>{props.evidence.byteSize.toLocaleString('en-US')} bytes</strong>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
