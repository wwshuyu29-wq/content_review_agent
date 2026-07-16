import type { DashboardSupplierQuality } from "../../api";

const pct = (value: number) => `${Math.round(value * 100)}%`;

export default function SupplierQualityPanel({ suppliers }: { suppliers: DashboardSupplierQuality[] }) {
  const max = Math.max(1, ...suppliers.map((supplier) => supplier.total_count));
  return (
    <section className="dashboard-panel supplier-quality-panel">
      <div className="panel-heading">
        <div>
          <h3>供应商质量</h3>
          <p className="small">按供应商统计通过率，用于复盘和推动内容质量</p>
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
                <i style={{ width: `${Math.max(8, (supplier.total_count / max) * 100)}%` }} />
              </div>
              <strong>{pct(supplier.pass_rate)}</strong>
              <small>{supplier.passed_count}/{supplier.total_count} 通过</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
