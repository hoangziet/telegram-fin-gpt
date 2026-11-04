import type { TotalsReport } from '../api/finances';
import { formatMoney } from './financeFormatters';

export interface CurrencyBreakdown {
  currency: string;
  income: string;
  expense: string;
  net: string;
}

/** Keep currency totals separate and perform subtraction with integers, not floats. */
export function currencyBreakdown(report: TotalsReport | null): CurrencyBreakdown[] {
  if (!report) return [];
  const currencies = new Map<string, { income: bigint; expense: bigint }>();
  for (const row of report.totals) {
    const current = currencies.get(row.currency) ?? { income: 0n, expense: 0n };
    current[row.kind] += scaledAmount(row.amount);
    currencies.set(row.currency, current);
  }
  return [...currencies.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([currency, totals]) => ({
      currency,
      income: money(totals.income, currency),
      expense: money(totals.expense, currency),
      net: money(totals.income - totals.expense, currency),
    }));
}

function scaledAmount(value: string): bigint {
  const match = /^(\d+)(?:\.(\d{1,4}))?$/.exec(value);
  if (!match) throw new Error('Invalid aggregate amount.');
  return BigInt(match[1] ?? '0') * 10_000n + BigInt((match[2] ?? '').padEnd(4, '0'));
}

function money(value: bigint, currency: string): string {
  const sign = value < 0n ? '-' : '';
  const absolute = value < 0n ? -value : value;
  const amount = `${sign}${absolute / 10_000n}.${String(absolute % 10_000n).padStart(4, '0')}`;
  return formatMoney({ amount, currency });
}
