import type { DashboardQuality } from "../../api";

const pct = (value: number) => `${Math.round(value * 100)}%`;

export default function QualityPanel({ quality }: { quality: DashboardQuality }) {
  return (
    <section className="dashboard-panel quality-panel">
      <div className="panel-heading">
        <div>
          <h3>内容质量</h3>
          <p className="small">上传表格内容通过率</p>
        </div>
        <strong>{pct(quality.pass_rate)}</strong>
      </div>
      <div className="quality-ring" style={{ ["--pass-rate" as string]: `${quality.pass_rate * 360}deg` }}>
        <div>
          <b>{quality.passed_count}</b>
          <span>/ {quality.total_count}</span>
        </div>
      </div>
      <div className="quality-batches">
        {quality.batches.length === 0 ? <p className="panel-state">当前月份暂无批次。</p> : quality.batches.slice(0, 5).map((batch) => (
          <div className="quality-batch" key={batch.batch_id}>
            <span>{batch.batch_name}</span>
            <div className="quality-mini-bar"><i style={{ width: pct(batch.pass_rate) }} /></div>
            <b>{pct(batch.pass_rate)}</b>
          </div>
        ))}
      </div>
    </section>
  );
}
