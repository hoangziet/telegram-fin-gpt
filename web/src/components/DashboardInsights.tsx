import { useEffect, useState } from 'react';
import { getInsight, type TotalsReport, type TransactionRecord } from '../api/finances';
import { formatMoney } from '../utils/financeFormatters';
import { SparklesIcon } from './Icons';
import overviewEmpty from '../assets/overview-empty.png';

interface DashboardInsightsProps {
  report: TotalsReport | null;
  transactions: TransactionRecord[];
}

const COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
];

function positiveAmount(value: string): number {
  const amount = Number(value);
  return Number.isFinite(amount) && amount > 0 ? amount : 0;
}

function scaledAmount(value: string): bigint {
  const match = /^(\d+)(?:\.(\d{1,4}))?$/.exec(value);
  if (!match) return 0n;
  return BigInt(match[1] ?? '0') * 10_000n + BigInt((match[2] ?? '').padEnd(4, '0'));
}

function decimalAmount(value: bigint): string {
  const fraction = String(value % 10_000n)
    .padStart(4, '0')
    .replace(/0+$/, '');
  return `${value / 10_000n}${fraction ? `.${fraction}` : ''}`;
}

function percentage(amount: bigint, total: bigint): number {
  return total === 0n ? 0 : Number((amount * 10_000n) / total) / 100;
}

export default function DashboardInsights({ report, transactions }: DashboardInsightsProps) {
  const [insight, setInsight] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void getInsight().then((answer) => {
      if (active) setInsight(answer);
    });
    return () => {
      active = false;
    };
  }, []);
  const income = report?.totals.find((row) => row.kind === 'income' && row.currency === 'VND');
  const expense = report?.totals.find((row) => row.kind === 'expense' && row.currency === 'VND');
  const incomeLabel = formatMoney({ amount: income?.amount ?? '0', currency: 'VND' });
  const expenseLabel = formatMoney({ amount: expense?.amount ?? '0', currency: 'VND' });
  const incomeAmount = positiveAmount(income?.amount ?? '0');
  const expenseAmount = positiveAmount(expense?.amount ?? '0');
  const largest = Math.max(incomeAmount, expenseAmount, 1);

  const categoryAmounts = new Map<string, bigint>();
  for (const item of transactions) {
    if (item.kind !== 'expense' || item.amount.currency !== 'VND') continue;
    const category = item.category || 'Khác';
    categoryAmounts.set(
      category,
      (categoryAmounts.get(category) ?? 0n) + scaledAmount(item.amount.amount),
    );
  }
  const categories = [...categoryAmounts].sort((a, b) =>
    a[1] === b[1] ? 0 : a[1] > b[1] ? -1 : 1,
  );
  const total = categories.reduce((sum, [, value]) => sum + value, 0n);
  const segments = categories.map((_, index) => {
    const start = percentage(
      categories.slice(0, index).reduce((sum, [, value]) => sum + value, 0n),
      total,
    );
    const end =
      index === categories.length - 1
        ? 100
        : percentage(
            categories.slice(0, index + 1).reduce((sum, [, value]) => sum + value, 0n),
            total,
          );
    return `${COLORS[index % COLORS.length]} ${start}% ${end}%`;
  });

  return (
    <>
      {insight && (
        <p className="insight-narrative">
          <SparklesIcon size={16} />
          {insight}
        </p>
      )}
      <section className="insights-grid" aria-label={'Biểu đồ dòng tiền và danh mục chi tiêu'}>
        <div className="bento-card insight-card">
          <div className="insight-heading">
            <div>
              <span className="eyebrow">{'Phân tích tháng'}</span>
              <h2 className="card-title">{'Dòng tiền tổng quan'}</h2>
            </div>
            <span className="insight-period">{report?.fromDate.slice(0, 7) ?? 'Tháng này'}</span>
          </div>
          <p className="insight-caption">{'So sánh tiền vào và tiền ra đã xác nhận · VND'}</p>
          {report ? (
            <div
              className="cashflow-chart"
              role="img"
              aria-label={`Thu ${incomeLabel}; chi ${expenseLabel}`}
            >
              <div className="cashflow-row">
                <span>{'Thu nhập'}</span>
                <div className="cashflow-track">
                  <span
                    className="cashflow-fill cashflow-income"
                    style={{ width: `${(incomeAmount / largest) * 100}%` }}
                  />
                </div>
                <strong>{incomeLabel}</strong>
              </div>
              <div className="cashflow-row">
                <span>{'Chi tiêu'}</span>
                <div className="cashflow-track">
                  <span
                    className="cashflow-fill cashflow-expense"
                    style={{ width: `${(expenseAmount / largest) * 100}%` }}
                  />
                </div>
                <strong>{expenseLabel}</strong>
              </div>
            </div>
          ) : (
            <p className="insight-empty">{'Đang tải số liệu tháng này…'}</p>
          )}
          <p className="insight-footnote">{'Tổng quan chỉ hiển thị VND.'}</p>
        </div>
        <div className="bento-card insight-card">
          <div className="insight-heading">
            <div>
              <span className="eyebrow">{'Giao dịch gần đây'}</span>
              <h2 className="card-title">{'Chi tiêu theo danh mục'}</h2>
            </div>
            <span className="insight-period">{transactions.length} khoản</span>
          </div>
          {total > 0n ? (
            <div className="category-chart">
              <div
                className="category-donut"
                style={{ background: `conic-gradient(${segments.join(', ')})` }}
                aria-hidden="true"
              >
                <div className="donut-center">
                  <span>{'Tổng gần đây'}</span>
                  <strong>{formatMoney({ amount: decimalAmount(total), currency: 'VND' })}</strong>
                </div>
              </div>
              <ul className="category-legend">
                {categories.map(([category, amount], index) => (
                  <li key={category}>
                    <span className="legend-name">
                      <span
                        className="legend-dot"
                        style={{ background: COLORS[index % COLORS.length] }}
                      />
                      {category}
                    </span>
                    <strong>{Math.round(percentage(amount, total))}%</strong>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="insight-empty">
              <img src={overviewEmpty} alt="" />
              <p>{'Chưa có khoản chi VND trong các giao dịch gần đây.'}</p>
            </div>
          )}
          <p className="insight-footnote">
            {'Phân bổ từ các giao dịch gần đây đang hiển thị, không phải toàn bộ tháng.'}
          </p>
        </div>
      </section>
    </>
  );
}
