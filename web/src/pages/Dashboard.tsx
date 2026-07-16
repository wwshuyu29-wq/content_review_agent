import { useEffect, useMemo, useState } from "react";
import { api, type Config, type DashboardOverview } from "../api";
import ApiSetupCard from "../components/dashboard/ApiSetupCard";
import IssueClusterPanel from "../components/dashboard/IssueClusterPanel";
import KpiCard from "../components/dashboard/KpiCard";
import ProjectQualityPanel from "../components/dashboard/ProjectQualityPanel";
import QualityPanel from "../components/dashboard/QualityPanel";
import WorkloadChart from "../components/dashboard/WorkloadChart";

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
    if (!overview) return { workload: 0, audits: 0, decisions: 0, issues: 0 };
    return overview.workload.reduce((acc, row) => {
      const item = row.months[0];
      acc.workload += (item?.uploaded_count || 0) + (item?.audit_started_count || 0) + (item?.human_decision_count || 0);
      acc.audits += item?.audit_started_count || 0;
      acc.decisions += item?.human_decision_count || 0;
      return acc;
    }, { workload: 0, audits: 0, decisions: 0, issues: overview.issue_clusters.reduce((sum, cluster) => sum + cluster.issue_count, 0) });
  }, [overview]);

  return (
    <div className="dashboard-page">
      <div className="page-heading dashboard-heading">
        <div>
          <h2>概览</h2>
          <p>团队工作量、内容质量与聚类问题</p>
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
            <KpiCard label="工作量" value={totals.workload} detail={`${overview.month} 团队总量`} />
            <KpiCard label="审核启动" value={totals.audits} detail="单篇与批次审核" />
            <KpiCard label="人工处理" value={totals.decisions} detail="已关闭任务动作" />
            <KpiCard label="通过率" value={pct(overview.quality.pass_rate)} detail={`${overview.quality.passed_count}/${overview.quality.total_count} 篇`} />
          </section>

          <div className="dashboard-grid">
            <div className="dashboard-main-column">
              <WorkloadChart rows={overview.workload} />
              <QualityPanel quality={overview.quality} />
            </div>
            <div className="dashboard-side-column">
              <ApiSetupCard config={config} onSaved={setConfig} />
              <ProjectQualityPanel projects={overview.project_quality} />
            </div>
            <IssueClusterPanel clusters={overview.issue_clusters} />
          </div>
        </>
      )}
    </div>
  );
}
