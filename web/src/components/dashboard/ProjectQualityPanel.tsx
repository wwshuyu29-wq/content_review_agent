import type { DashboardProjectQuality } from "../../api";

const pct = (value: number) => `${Math.round(value * 100)}%`;

export default function ProjectQualityPanel({ projects }: { projects: DashboardProjectQuality[] }) {
  return (
    <section className="dashboard-panel project-quality-panel">
      <div className="panel-heading">
        <div>
          <h3>项目通过率对比</h3>
          <p className="small">按当前月份统计不同项目质量</p>
        </div>
      </div>
      {projects.length === 0 ? <p className="panel-state">当前月份暂无项目数据。</p> : (
        <div className="project-quality-list">
          {projects.slice(0, 6).map((project) => (
            <article key={project.project_id}>
              <div>
                <b>{project.project_name}</b>
                <span>{project.passed_count}/{project.total_count} 通过</span>
              </div>
              <div className="quality-mini-bar"><i style={{ width: pct(project.pass_rate) }} /></div>
              <strong>{pct(project.pass_rate)}</strong>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
