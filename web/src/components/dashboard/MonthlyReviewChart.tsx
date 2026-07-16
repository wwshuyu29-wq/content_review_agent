import type { DashboardMonthlyReview } from "../../api";

export default function MonthlyReviewChart({ months }: { months: DashboardMonthlyReview[] }) {
  const max = Math.max(1, ...months.map((item) => item.reviewed_count));
  const total = months.reduce((sum, item) => sum + item.reviewed_count, 0);
  return (
    <section className="dashboard-panel monthly-review-panel">
      <div className="panel-heading">
        <div>
          <h3>每月累计审核篇数</h3>
          <p className="small">近 6 个月已完成 AI 审核的稿件量</p>
        </div>
        <strong>{total} 篇</strong>
      </div>
      <div className="monthly-bars" aria-label="每月累计审核篇数柱状图">
        {months.map((item) => (
          <div className="monthly-bar" key={item.month}>
            <div className="monthly-bar-track">
              <i style={{ height: `${Math.max(8, (item.reviewed_count / max) * 100)}%` }} />
            </div>
            <b>{item.reviewed_count}</b>
            <span>{item.month.slice(5)}月</span>
          </div>
        ))}
      </div>
    </section>
  );
}
