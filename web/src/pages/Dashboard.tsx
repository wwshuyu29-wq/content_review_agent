import { useEffect, useMemo, useState } from "react";
import { api, type Config, type DashboardOverview } from "../api";
import ApiSetupCard from "../components/dashboard/ApiSetupCard";
import IssueClusterPanel from "../components/dashboard/IssueClusterPanel";
import KpiCard from "../components/dashboard/KpiCard";
import MonthlyReviewChart from "../components/dashboard/MonthlyReviewChart";
import SupplierQualityPanel from "../components/dashboard/SupplierQualityPanel";

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

const pct = (value: number) => `${Math.round(value * 100)}%`;

export default function Dashboard() {
  const [month, setMonth] = useState(currentMonth());
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setMessage(null);
    Promise.all([api.dashboardOverview(month), api.config()])
      .then(([dashboard, cfg]) => {
        if (cancelled) return;
        setOverview(dashboard);
        setConfig(cfg);
      })
      .catch((error: Error) => {
        if (!cancelled) setMessage(error.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [month]);

  const totals = useMemo(() => {
    if (!overview) return { currentReviewed: 0, sixMonthReviewed: 0, suppliers: 0, issues: 0 };
    const currentMonth = overview.monthly_reviews.find((item) => item.month === overview.month);
    const issueContentIds = new Set(overview.issue_clusters.flatMap((cluster) => cluster.manuscripts.map((item) => item.content_id)));
    return {
      currentReviewed: currentMonth?.reviewed_count || 0,
      sixMonthReviewed: overview.monthly_reviews.reduce((sum, item) => sum + item.reviewed_count, 0),
      suppliers: overview.supplier_quality.length,
      issues: issueContentIds.size,
    };
  }, [overview]);

  return (
    <div className="dashboard-page">
      <div className="page-heading dashboard-heading">
        <div>
          <h2>概览</h2>
          <p>月度审核量、供应商质量与聚类问题</p>
        </div>
        <div className="field month-field">
          <label htmlFor="dashboard-month">月份</label>
          <input id="dashboard-month" type="month" value={month} onChange={(event) => setMonth(event.target.value)} />
        </div>
      </div>

      {message && <div className="msg err">{message}</div>}
      {loading && <p className="empty">正在加载看板...</p>}

      {overview && (
        <>
          <section className="dashboard-kpi-grid">
            <KpiCard label="本月审核篇数" value={totals.currentReviewed} detail={`${overview.month} 已审稿件`} />
            <KpiCard label="近 6 月累计" value={totals.sixMonthReviewed} detail="按审核完成稿件去重统计" />
            <KpiCard label="供应商数" value={totals.suppliers} detail="当前月份有内容的供应商" />
            <KpiCard label="问题稿件数" value={totals.issues} detail="按五个维度去重统计" />
          </section>

          <div className="dashboard-grid">
            <div className="dashboard-main-column" aria-label="月度审核与供应商质量">
              <MonthlyReviewChart months={overview.monthly_reviews} />
              <SupplierQualityPanel suppliers={overview.supplier_quality} />
            </div>
            <div className="dashboard-side-column" aria-label="聚类问题与模型配置">
              <IssueClusterPanel clusters={overview.issue_clusters} />
              <ApiSetupCard config={config} onSaved={setConfig} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
