import type { DraftItem, TotalsReport } from '../api/finances';
import { ApiError } from '../api/client';

export type View = 'dashboard' | 'history' | 'chat';

export function formatMoney(value: { amount: string; currency: string } | null): string {
  if (!value) return 'Thiếu số tiền';
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.amount);
  if (!match) return `${value.amount} ${value.currency}`;
  const sign = match[1] ?? '';
  const integer = match[2] ?? '0';
  const fraction = (match[3] ?? '').replace(/0+$/, '');
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${sign}${grouped}${fraction ? `,${fraction}` : ''} ${value.currency}`;
}

export function totalFor(report: TotalsReport | null, kind: 'income' | 'expense'): string {
  const row = report?.totals.find((item) => item.kind === kind && item.currency === 'VND');
  return report ? formatMoney({ amount: row?.amount ?? '0', currency: 'VND' }) : '—';
}

export function subtractMoney(income: string, expense: string): string {
  if (income === '—' || expense === '—') return '—';
  const incomeAmount = decimalToScaled(displayedToDecimal(income));
  const expenseAmount = decimalToScaled(displayedToDecimal(expense));
  return formatMoney({ amount: scaledToDecimal(incomeAmount - expenseAmount), currency: 'VND' });
}

function displayedToDecimal(value: string): string {
  const amount = value.split(' ')[0] ?? '0';
  return amount.replaceAll('.', '').replace(',', '.');
}

function decimalToScaled(value: string): bigint {
  const [whole = '0', fraction = ''] = value.split('.');
  const sign = whole.startsWith('-') ? -1n : 1n;
  const integer = whole.replace('-', '');
  return sign * (BigInt(integer) * 10_000n + BigInt(fraction.padEnd(4, '0').slice(0, 4)));
}

function scaledToDecimal(value: bigint): string {
  const sign = value < 0n ? '-' : '';
  const absolute = value < 0n ? -value : value;
  const integer = absolute / 10_000n;
  const fraction = String(absolute % 10_000n)
    .padStart(4, '0')
    .replace(/0+$/, '');
  return `${sign}${integer}${fraction ? `.${fraction}` : ''}`;
}

export function currentMonthRange(): { from: string; to: string } {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Ho_Chi_Minh',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(new Date());
  const year = parts.find((part) => part.type === 'year')?.value ?? '1970';
  const month = parts.find((part) => part.type === 'month')?.value ?? '01';
  const lastDay = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  return {
    from: `${year}-${month}-01`,
    to: `${year}-${month}-${String(lastDay).padStart(2, '0')}`,
  };
}

export function readViewFromUrl(): View {
  const view = new URLSearchParams(window.location.search).get('view');
  return view === 'history' || view === 'chat' ? view : 'dashboard';
}

export function periodLabel(value: string | null): string {
  if (value === 'morning') return 'Sáng';
  if (value === 'noon') return 'Trưa';
  if (value === 'afternoon') return 'Chiều';
  if (value === 'evening') return 'Tối';
  return value || 'Thiếu buổi';
}

export function missingFieldLabel(value: string): string {
  if (value === 'transaction_date' || value === 'transactionDate') return 'ngày';
  if (value === 'day_period' || value === 'dayPeriod') return 'buổi';
  if (value === 'local_time' || value === 'localTime') return 'giờ';
  if (value === 'amount') return 'số tiền';
  if (value === 'currency') return 'đơn vị tiền';
  if (value === 'description') return 'nội dung';
  return value;
}

export function csvErrorLabel(field: string, code: string): string {
  const fieldNames: Record<string, string> = {
    amount: 'số tiền',
    category: 'danh mục',
    currency: 'đơn vị tiền',
    dayPeriod: 'buổi',
    description: 'nội dung',
    kind: 'loại thu chi',
    localTime: 'giờ',
    row: 'cấu trúc dòng',
    transactionDate: 'ngày',
  };
  const reasons: Record<string, string> = {
    conflicting_time_period: 'không khớp với giờ',
    invalid_amount: 'không hợp lệ',
    invalid_date: 'không hợp lệ',
    invalid_day_period: 'không hợp lệ',
    invalid_kind: 'không hợp lệ',
    invalid_local_time: 'không hợp lệ',
    invalid_row_shape: 'không đúng số cột',
    invalid_transaction_time: 'không hợp lệ',
    missing_amount: 'đang trống',
    missing_currency: 'đang trống',
    missing_date: 'đang trống',
    missing_day_period: 'đang trống',
    missing_description: 'đang trống',
    description_too_long: 'quá dài',
    category_too_long: 'quá dài',
  };
  return `${fieldNames[field] ?? field}: ${reasons[code] ?? 'cần kiểm tra'}`;
}

export function statusLabel(status: DraftItem['status']): string {
  if (status === 'ready') return 'Sẵn sàng ghi';
  if (status === 'confirmed') return 'Đã ghi sổ';
  if (status === 'cancelled') return 'Đã hủy';
  return 'Cần bổ sung';
}

export function messageFor(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Không thể kết nối. Hãy thử lại khi mạng ổn định.';
  if (error.statusCode === 401) return 'Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.';
  if (error.statusCode === 409) {
    return 'Dữ liệu đã thay đổi. Hãy tải lại bản xem trước rồi thử lại.';
  }
  if (error.statusCode === 422) return error.message;
  return error.message;
}
