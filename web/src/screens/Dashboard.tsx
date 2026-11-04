import type { TotalsReport, TransactionRecord } from '../api/finances';
import DashboardInsights from '../components/DashboardInsights';
import { ArrowDownLeftIcon, ArrowUpRightIcon, ClockIcon, SparklesIcon } from '../components/Icons';
import { subtractMoney, totalFor } from '../utils/financeFormatters';

interface DashboardProps {
  report: TotalsReport | null;
  transactions: TransactionRecord[];
}

export default function Dashboard({ report, transactions }: DashboardProps) {
  const income = totalFor(report, 'income');
  const expense = totalFor(report, 'expense');
  const isEmpty = report !== null && report.totals.length === 0 && transactions.length === 0;

  return (
    <div className="dashboard-view">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Thu chi cá nhân</span>
          <h1 className="page-title">Tổng quan tài chính</h1>
          <p className="page-subtitle">Tiền của bạn, nhìn một lần là rõ.</p>
        </div>
        <div className="range-badge">
          <ClockIcon size={14} />
          <span>{report ? `${report.fromDate} — ${report.toDate}` : 'Tháng này'}</span>
        </div>
      </div>
      {isEmpty ? (
        <div className="bento-card dashboard-empty-state">
          <SparklesIcon size={28} />
          <h2 className="card-title">Chưa có giao dịch nào</h2>
          <p>Ghi lại khoản chi hoặc thu nhập đầu tiên để xem tổng quan tài chính ở đây.</p>
          <a className="btn-primary" href="/?view=chat">
            Ghi giao dịch đầu tiên
          </a>
        </div>
      ) : (
        <>
          <section className="bento-kpi-grid" aria-label={'Tổng quan dòng tiền trong tháng'}>
            <div className="bento-kpi-card kpi-income">
              <div className="kpi-header">
                <span className="kpi-label">{'Tổng thu nhập'}</span>
                <span className="kpi-icon-badge income-badge">
                  <ArrowDownLeftIcon size={18} />
                </span>
              </div>
              <p className="kpi-value tabular-nums">{income}</p>
              <span className="kpi-subtext">{'Đã ghi nhận trong tháng'}</span>
            </div>
            <div className="bento-kpi-card kpi-expense">
              <div className="kpi-header">
                <span className="kpi-label">{'Tổng chi tiêu'}</span>
                <span className="kpi-icon-badge expense-badge">
                  <ArrowUpRightIcon size={18} />
                </span>
              </div>
              <p className="kpi-value tabular-nums">{expense}</p>
              <span className="kpi-subtext">{'Đã ghi nhận trong tháng'}</span>
            </div>
            <div className="bento-kpi-card kpi-net">
              <div className="kpi-header">
                <span className="kpi-label">{'Dòng tiền ròng'}</span>
                <span className="kpi-icon-badge net-badge">
                  <SparklesIcon size={18} />
                </span>
              </div>
              <p className="kpi-value tabular-nums">{subtractMoney(income, expense)}</p>
              <span className="kpi-subtext">
                {'Chênh lệch thu - chi · không phải số dư ngân hàng'}
              </span>
            </div>
          </section>
          <DashboardInsights report={report} transactions={transactions} />
        </>
      )}
    </div>
  );
}
