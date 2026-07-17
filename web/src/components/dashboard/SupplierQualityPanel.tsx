import type { DashboardSupplierQuality } from "../../api";

const pct = (value: number) => `${Math.round(value * 100)}%`;

export default function SupplierQualityPanel({ suppliers }: { suppliers: DashboardSupplierQuality[] }) {
  return (
    <section className="dashboard-panel supplier-quality-panel">
      <div className="panel-heading">
        <div>
          <h3>内容质量</h3>
          <p className="small">按批次统计已分析稿件，便于复盘内容质量</p>
        </div>
      </div>
      {suppliers.length === 0 ? <p className="panel-state">当前月份暂无供应商数据。</p> : (
        <div className="supplier-quality-list">
          {suppliers.slice(0, 8).map((supplier) => (
            <article key={supplier.supplier_name}>
              <div>
                <b>{supplier.supplier_name}</b>
                <span>{supplier.project_names.slice(0, 3).join("、") || "未标记专项"}</span>
              </div>
              <div className="supplier-quality-meter">
                <i style={{ width: pct(supplier.pass_rate) }} />
              </div>
              <strong>{pct(supplier.pass_rate)}</strong>
              <small>{supplier.passed_count}/{supplier.total_count} 已分析</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
