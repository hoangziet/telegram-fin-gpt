import type { TotalsReport } from '../api/finances';
import { formatMoney, totalFor, subtractMoney } from '../utils/financeFormatters';
import { ArrowDownLeftIcon, ArrowUpRightIcon, SparklesIcon } from '../components/Icons';
import { t, useLanguage } from '../i18n';

interface ReportsProps {
  loading: boolean;
  report: TotalsReport | null;
}

export default function Reports({ loading, report }: ReportsProps) {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const income = totalFor(report, 'income');
  const expense = totalFor(report, 'expense');
  const net = subtractMoney(income, expense);

  return (
    <div className="reports-view">
      <div className="page-heading">
        <div>
          <h1 className="page-title">{label('Báo cáo dòng tiền')}</h1>
          <p className="page-subtitle">{label('Thống kê thu nhập và chi tiêu theo chu kỳ')}</p>
        </div>
        <span className="range-badge">
          {report ? `${report.fromDate} — ${report.toDate}` : label('Tháng này')}
        </span>
      </div>

      {loading ? (
        <div className="loading-container" role="status">
          <span className="spinner" />
          <span>{label('Đang tạo báo cáo tổng hợp…')}</span>
        </div>
      ) : report ? (
        <div className="reports-content-grid">
          {/* Bento KPI Summary */}
          <section className="bento-kpi-grid">
            <div className="bento-kpi-card kpi-income">
              <div className="kpi-header">
                <span className="kpi-label">{label('Tổng thu nhập')}</span>
                <span className="kpi-icon-badge income-badge">
                  <ArrowDownLeftIcon size={18} />
                </span>
              </div>
              <p className="kpi-value tabular-nums">{income}</p>
              <span className="kpi-subtext">{label('Khoản đã xác nhận')}</span>
            </div>

            <div className="bento-kpi-card kpi-expense">
              <div className="kpi-header">
                <span className="kpi-label">{label('Tổng chi tiêu')}</span>
                <span className="kpi-icon-badge expense-badge">
                  <ArrowUpRightIcon size={18} />
                </span>
              </div>
              <p className="kpi-value tabular-nums">{expense}</p>
              <span className="kpi-subtext">{label('Khoản đã xác nhận')}</span>
            </div>

            <div className="bento-kpi-card kpi-net">
              <div className="kpi-header">
                <span className="kpi-label">{label('Dòng tiền ròng')}</span>
                <span className="kpi-icon-badge net-badge">
                  <SparklesIcon size={18} />
                </span>
              </div>
              <p className="kpi-value tabular-nums">{net}</p>
              <span className="kpi-subtext">{label('Chênh lệch dòng tiền')}</span>
            </div>
          </section>

          {/* Details Table */}
          <section className="bento-card report-card">
            <div className="card-header">
              <div>
                <h2 className="card-title">{label('Chi tiết dòng tiền theo loại')}</h2>
                <p className="card-description">
                  {label(
                    'Dữ liệu được tính toán dựa trên các giao dịch không bị xóa trong khoảng thời gian đã chọn.',
                  )}
                </p>
              </div>
            </div>

            <div className="table-wrapper">
              <table className="modern-table">
                <thead>
                  <tr>
                    <th>{label('Loại dòng tiền')}</th>
                    <th>{label('Đơn vị tiền')}</th>
                    <th className="numeric">{label('Tổng số tiền')}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.totals.map((row) => (
                    <tr key={`${row.kind}-${row.currency}`}>
                      <td>
                        <span className={`kind-tag kind-${row.kind}`}>
                          {label(row.kind === 'income' ? 'Tiền vào (+)' : 'Tiền ra (-)')}
                        </span>
                      </td>
                      <td>
                        <span className="currency-badge">{row.currency}</span>
                      </td>
                      <td className={`numeric tabular-nums amount-${row.kind}`}>
                        {formatMoney({ amount: row.amount, currency: row.currency })}
                      </td>
                    </tr>
                  ))}
                  {!report.totals.length && (
                    <tr>
                      <td colSpan={3} className="text-center">
                        {label('Chưa có dữ liệu giao dịch trong khoảng thời gian này.')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="report-disclaimer">
              <p>
                <strong>{label('Lưu ý:')}</strong>{' '}
                {label(
                  'Dòng tiền ròng là thu trừ chi đã ghi trong hệ thống, không phải số dư ngân hàng.',
                )}
              </p>
            </div>
          </section>
        </div>
      ) : (
        <div className="empty-box">
          <p>{label('Báo cáo hiện chưa tải được. Hãy thử lại sau.')}</p>
        </div>
      )}
    </div>
  );
}
