export default function KpiCard({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return (
    <article className="dashboard-kpi">
      <span>{label}</span>
      <b>{value}</b>
      <small>{detail}</small>
    </article>
  );
}
