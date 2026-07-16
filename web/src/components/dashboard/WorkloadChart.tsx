import type { DashboardWorkloadRow } from "../../api";

function total(row: DashboardWorkloadRow): number {
  const month = row.months[0];
  return month ? month.uploaded_count + month.audit_started_count + month.human_decision_count : 0;
}

export default function WorkloadChart({ rows }: { rows: DashboardWorkloadRow[] }) {
  const sorted = [...rows].sort((left, right) => total(right) - total(left));
  const max = Math.max(1, ...sorted.map(total));
  return (
    <section className="dashboard-panel workload-panel">
      <div className="panel-heading">
        <div>
          <h3>团队月度工作量</h3>
          <p className="small">上传稿件、启动审核、人工处理</p>
        </div>
      </div>
      <div className="workload-list">
        {sorted.map((row) => {
          const month = row.months[0];
          const uploaded = month?.uploaded_count || 0;
          const audits = month?.audit_started_count || 0;
          const decisions = month?.human_decision_count || 0;
          const rowTotal = uploaded + audits + decisions;
          const uploadedWidth = rowTotal ? (uploaded / rowTotal) * 100 : 0;
          const auditWidth = rowTotal ? (audits / rowTotal) * 100 : 0;
          const decisionWidth = rowTotal ? (decisions / rowTotal) * 100 : 0;
          return (
            <div className="workload-row" key={row.user_id}>
              <div className="workload-person">
                <b>{row.display_name}</b>
                <span>{row.username}</span>
              </div>
              <div className="workload-bar-wrap" style={{ ["--row-scale" as string]: `${Math.max(8, (rowTotal / max) * 100)}%` }}>
                <div className="workload-bar">
                  <i className="segment-upload" style={{ width: `${uploadedWidth}%` }} />
                  <i className="segment-audit" style={{ width: `${auditWidth}%` }} />
                  <i className="segment-human" style={{ width: `${decisionWidth}%` }} />
                </div>
              </div>
              <div className="workload-numbers">
                <b>{rowTotal}</b>
                <span>{uploaded}/{audits}/{decisions}</span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="dashboard-legend">
        <span><i className="segment-upload" />上传</span>
        <span><i className="segment-audit" />审核</span>
        <span><i className="segment-human" />人工</span>
      </div>
    </section>
  );
}
