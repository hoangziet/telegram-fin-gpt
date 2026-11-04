import type { TransactionRecord } from '../api/finances';

function csvCell(value: string): string {
  const safe = /^\s*[-=+@]/.test(value) || /^[\t\r]/.test(value) ? `'${value}` : value;
  return `"${safe.replaceAll('"', '""')}"`;
}

/** Export only the visible page, preserving exact decimal strings and neutralizing spreadsheet formulas. */
export function exportTransactionsCsv(items: TransactionRecord[]): string {
  const header = [
    'Ngày',
    'Giờ',
    'Buổi',
    'Loại',
    'Số tiền',
    'Tiền tệ',
    'Nội dung',
    'Danh mục',
    'Đã xóa',
  ];
  const rows = items.map((item) => [
    item.transactionDate,
    item.localTime ?? '',
    item.dayPeriod,
    item.kind,
    item.amount.amount,
    item.amount.currency,
    item.description ?? '',
    item.category,
    item.isDeleted ? 'Có' : 'Không',
  ]);
  return `\uFEFF${[header, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n')}\r\n`;
}
