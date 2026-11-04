import type { DraftItem } from '../api/finances';
import {
  formatMoney,
  periodLabel,
  statusLabel,
  missingFieldLabel,
  csvErrorLabel,
} from '../utils/financeFormatters';
import { CheckCircleIcon, AlertCircleIcon, CalendarIcon, ClockIcon } from './Icons';

export interface DraftEditInputs {
  transactionDate?: string;
  dayPeriod?: string;
  description?: string;
  amount?: string;
  currency?: string;
  kind?: DraftItem['kind'];
  category?: string;
  localTime?: string;
}

interface DraftItemViewProps {
  item: DraftItem;
  busy: boolean;
  dateValue: string;
  periodValue: string;
  descriptionValue: string;
  amountValue: string;
  currencyValue: string;
  kindValue: DraftItem['kind'];
  categoryValue: string;
  localTimeValue: string;
  onDateChange: (value: string) => void;
  onPeriodChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onAmountChange: (value: string) => void;
  onCurrencyChange: (value: string) => void;
  onKindChange: (value: DraftItem['kind']) => void;
  onCategoryChange: (value: string) => void;
  onLocalTimeChange: (value: string) => void;
  onSave: () => void;
  onConfirm: () => void;
}

const COMMON_CATEGORIES = ['Ăn uống', 'Di chuyển', 'Mua sắm', 'Hóa đơn', 'Lương', 'Giải trí'];

export default function DraftItemView(props: DraftItemViewProps) {
  const item = props.item;
  const hasUnsavedChanges =
    props.descriptionValue !== (item.description ?? '') ||
    props.amountValue !== (item.amount?.amount ?? '') ||
    props.currencyValue !== (item.currency ?? '') ||
    props.dateValue !== (item.transactionDate ?? '') ||
    props.periodValue !== (item.dayPeriod ?? '') ||
    props.kindValue !== item.kind ||
    props.categoryValue !== item.category ||
    props.localTimeValue !== (item.localTime ?? '');

  const isReady = item.status === 'ready';
  const isConfirmed = item.status === 'confirmed';

  return (
    <article className={`draft-item draft-item-${item.status}`}>
      <div className="draft-item-header">
        <div className="draft-item-info">
          <span className="item-number-badge">
            {item.rowNumber === null
              ? String(item.index + 1).padStart(2, '0')
              : `D${item.rowNumber}`}
          </span>
          <div className="item-headings">
            <h3 className="item-title">{item.description || 'Chưa có nội dung'}</h3>
            <p className="item-meta">
              <span className="meta-amount">{formatMoney(item.amount)}</span>
              <span className="meta-sep">•</span>
              <span className="meta-date">
                <CalendarIcon size={14} className="inline-icon" />
                {item.transactionDate ?? 'Thiếu ngày'}
              </span>
              <span className="meta-sep">•</span>
              <span className="meta-period">
                <ClockIcon size={14} className="inline-icon" />
                {periodLabel(item.dayPeriod)}
              </span>
            </p>
          </div>
        </div>
        <div className={`status-pill status-${item.status}`}>
          {isReady ? (
            <CheckCircleIcon size={14} />
          ) : isConfirmed ? (
            <CheckCircleIcon size={14} />
          ) : (
            <AlertCircleIcon size={14} />
          )}
          <span>{statusLabel(item.status)}</span>
        </div>
      </div>

      {!isConfirmed && (
        <div className="draft-item-body">
          <div className="draft-fields-grid">
            <label className="field-group" htmlFor={`desc-${item.id}`}>
              <span className="field-label">{'Nội dung giao dịch'}</span>
              <input
                id={`desc-${item.id}`}
                className="text-input"
                value={props.descriptionValue}
                onChange={(e) => props.onDescriptionChange(e.currentTarget.value)}
                maxLength={256}
                placeholder="Nội dung giao dịch"
              />
            </label>

            <label className="field-group" htmlFor={`kind-${item.id}`}>
              <span className="field-label">{'Loại thu / chi'}</span>
              <select
                id={`kind-${item.id}`}
                className="select-input"
                value={props.kindValue}
                onChange={(e) => props.onKindChange(e.currentTarget.value as DraftItem['kind'])}
              >
                <option value="expense">{'Chi tiêu (-)'}</option>
                <option value="income">{'Thu nhập (+)'}</option>
              </select>
            </label>

            <label className="field-group" htmlFor={`amount-${item.id}`}>
              <span className="field-label">{'Số tiền'}</span>
              <div className="input-with-affix">
                <input
                  id={`amount-${item.id}`}
                  className="text-input tabular-nums"
                  inputMode="decimal"
                  value={props.amountValue}
                  onChange={(e) => props.onAmountChange(e.currentTarget.value)}
                  placeholder="0"
                />
                <span className="input-affix">{props.currencyValue || 'VND'}</span>
              </div>
            </label>

            <label className="field-group" htmlFor={`currency-${item.id}`}>
              <span className="field-label">{'Tiền tệ'}</span>
              <select
                id={`currency-${item.id}`}
                className="select-input"
                value={props.currencyValue}
                onChange={(e) => props.onCurrencyChange(e.currentTarget.value)}
              >
                <option value="">{'Chọn tiền tệ'}</option>
                {['VND', 'USD', 'EUR', 'JPY', 'BHD', 'KWD'].map((code) => (
                  <option value={code} key={code}>
                    {code}
                  </option>
                ))}
              </select>
            </label>

            <label className="field-group" htmlFor={`date-${item.id}`}>
              <span className="field-label">{'Ngày giao dịch'}</span>
              <input
                id={`date-${item.id}`}
                className="date-input"
                type="date"
                value={props.dateValue}
                onChange={(e) => props.onDateChange(e.currentTarget.value)}
              />
            </label>

            <label className="field-group" htmlFor={`period-${item.id}`}>
              <span className="field-label">{'Buổi'}</span>
              <select
                id={`period-${item.id}`}
                className="select-input"
                value={props.periodValue}
                onChange={(e) => props.onPeriodChange(e.currentTarget.value)}
              >
                <option value="">{'Chọn buổi'}</option>
                <option value="morning">{'Sáng'}</option>
                <option value="noon">{'Trưa'}</option>
                <option value="afternoon">{'Chiều'}</option>
                <option value="evening">{'Tối'}</option>
              </select>
            </label>

            <label className="field-group" htmlFor={`time-${item.id}`}>
              <span className="field-label">{'Giờ cụ thể'}</span>
              <input
                id={`time-${item.id}`}
                className="time-input"
                type="time"
                value={props.localTimeValue}
                onChange={(e) => props.onLocalTimeChange(e.currentTarget.value)}
              />
            </label>

            <div className="field-group">
              <label className="field-label" htmlFor={`cat-${item.id}`}>
                {'Danh mục'}
              </label>
              <input
                id={`cat-${item.id}`}
                className="text-input"
                value={props.categoryValue}
                onChange={(e) => props.onCategoryChange(e.currentTarget.value)}
                maxLength={128}
                placeholder="chưa phân loại"
              />
              <div className="quick-tags" aria-label={'Gợi ý danh mục'}>
                {COMMON_CATEGORIES.map((cat) => (
                  <button
                    key={cat}
                    type="button"
                    className="quick-tag-chip"
                    onClick={() => props.onCategoryChange(cat)}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {item.missingFields.length > 0 && (
            <p className="missing-alert" role="alert">
              <AlertCircleIcon size={16} />
              <span>
                {'Cần bổ sung thông tin:'}{' '}
                {item.missingFields.map((field) => missingFieldLabel(field)).join(', ')}.
              </span>
            </p>
          )}

          {Object.keys(item.rowErrors).length > 0 && (
            <ul className="csv-row-errors" aria-label={'Lỗi của dòng nhập'}>
              {Object.entries(item.rowErrors).map(([field, code]) => (
                <li key={field}>{csvErrorLabel(field, code)}</li>
              ))}
            </ul>
          )}

          <div className="draft-item-actions">
            <button
              className={`btn-secondary ${hasUnsavedChanges ? 'highlight-save' : ''}`}
              type="button"
              onClick={props.onSave}
              disabled={props.busy || !hasUnsavedChanges}
            >
              {hasUnsavedChanges ? 'Lưu thay đổi đã sửa' : 'Đã lưu bản xem trước'}
            </button>

            {isReady && (
              <button
                className="btn-primary"
                type="button"
                disabled={props.busy || hasUnsavedChanges}
                onClick={props.onConfirm}
              >
                {props.busy ? 'Đang ghi sổ…' : 'Xác nhận ghi khoản này'}
              </button>
            )}
          </div>
        </div>
      )}

      {isConfirmed && (
        <div className="confirmed-banner">
          <CheckCircleIcon size={18} />
          <span>{'Khoản này đã được xác nhận và ghi vào sổ giao dịch của bạn.'}</span>
        </div>
      )}
    </article>
  );
}
