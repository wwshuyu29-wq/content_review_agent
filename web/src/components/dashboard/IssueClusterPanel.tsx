import type { DashboardIssueCluster } from "../../api";
import { categoryLabel } from "../../reviewLabels";

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
        <div className="cluster-list">
          {clusters.map((cluster) => (
            <article className="cluster-row" key={cluster.category}>
              <div>
                <b>{categoryLabel(cluster.category)}</b>
                <span>{cluster.manuscript_count} 篇稿件</span>
                <p className="cluster-reason">主要原因：{cluster.manuscripts[0]?.reason || "待复核稿件信息"}</p>
              </div>
              <div className="cluster-bar"><i style={{ width: `${Math.max(8, (cluster.issue_count / max) * 100)}%` }} /></div>
              <strong>{cluster.issue_count}</strong>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
