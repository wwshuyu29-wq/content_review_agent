import type { DashboardIssueCluster } from "../../api";
import { categoryLabel, severityLabel } from "../../reviewLabels";

export default function IssueClusterPanel({ clusters }: { clusters: DashboardIssueCluster[] }) {
  const max = Math.max(1, ...clusters.map((cluster) => cluster.issue_count));
  return (
    <section className="dashboard-panel issue-cluster-panel">
      <div className="panel-heading">
        <div>
          <h3>聚类问题</h3>
          <p className="small">分类查看稿件风险来源</p>
        </div>
      </div>
      {clusters.length === 0 ? <p className="panel-state">当前月份暂无结构化问题。</p> : (
        <div className="cluster-layout">
          <div className="cluster-list">
            {clusters.map((cluster) => (
              <article className="cluster-row" key={cluster.category}>
                <div>
                  <b>{categoryLabel(cluster.category)}</b>
                  <span>{cluster.manuscript_count} 篇稿件 · 高风险 {cluster.high_count}</span>
                </div>
                <div className="cluster-bar"><i style={{ width: `${Math.max(8, (cluster.issue_count / max) * 100)}%` }} /></div>
                <strong>{cluster.issue_count}</strong>
              </article>
            ))}
          </div>
          <div className="cluster-manuscripts">
            {clusters[0]?.manuscripts.slice(0, 4).map((manuscript) => (
              <article key={manuscript.content_id}>
                <div>
                  <b>{manuscript.title}</b>
                  <span>{severityLabel(manuscript.severity)}</span>
                </div>
                <p>{manuscript.reason}</p>
              </article>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
