import type { TotalsReport, TransactionRecord } from '../api/finances';
import DashboardInsights from '../components/DashboardInsights';
import { ArrowDownLeftIcon, ArrowUpRightIcon, ClockIcon, SparklesIcon } from '../components/Icons';
import { subtractMoney, totalFor } from '../utils/financeFormatters';
import { t, useLanguage } from '../i18n';

interface DashboardProps {
  report: TotalsReport | null;
  transactions: TransactionRecord[];
}

export default function Dashboard({ report, transactions }: DashboardProps) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const income = totalFor(report, 'income');
  const expense = totalFor(report, 'expense');

  return (
    <div className="dashboard-view">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Personal finance</span>
          <h1 className="page-title">Financial overview</h1>
          <p className="page-subtitle">Your money, at a glance.</p>
        </div>
        <div className="range-badge">
          <ClockIcon size={14} />
          <span>{report ? `${report.fromDate} — ${report.toDate}` : label('Tháng này')}</span>
        </div>
      </div>
      <section className="bento-kpi-grid" aria-label={'Monthly cash flow overview'}>
        <div className="bento-kpi-card kpi-income">
          <div className="kpi-header">
            <span className="kpi-label">{label('Tổng thu nhập')}</span>
            <span className="kpi-icon-badge income-badge">
              <ArrowDownLeftIcon size={18} />
            </span>
          </div>
          <p className="kpi-value tabular-nums">{income}</p>
          <span className="kpi-subtext">{label('Đã ghi nhận trong tháng')}</span>
        </div>
        <div className="bento-kpi-card kpi-expense">
          <div className="kpi-header">
            <span className="kpi-label">{label('Tổng chi tiêu')}</span>
            <span className="kpi-icon-badge expense-badge">
              <ArrowUpRightIcon size={18} />
            </span>
          </div>
          <p className="kpi-value tabular-nums">{expense}</p>
          <span className="kpi-subtext">{label('Đã ghi nhận trong tháng')}</span>
        </div>
        <div className="bento-kpi-card kpi-net">
          <div className="kpi-header">
            <span className="kpi-label">{label('Dòng tiền ròng')}</span>
            <span className="kpi-icon-badge net-badge">
              <SparklesIcon size={18} />
            </span>
          </div>
          <p className="kpi-value tabular-nums">{subtractMoney(income, expense)}</p>
          <span className="kpi-subtext">
            {label('Chênh lệch thu - chi · không phải số dư ngân hàng')}
          </span>
        </div>
      </section>
      <DashboardInsights report={report} transactions={transactions} />
    </div>
  );
}
