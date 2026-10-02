// Bar chart for daily values; a bar can be split into stacked segments (e.g. by route).
// Each bar has a hover tooltip, and every chart on the dashboard also has its numbers in a table.
export type Bar = { key: string; segments: { value: number; className: string }[]; tooltip: string[] };

export default function BarChart({ bars, label, format = String }: { bars: Bar[]; label: string; format?: (v: number) => string }) {
  const totals = bars.map((b) => b.segments.reduce((s, x) => s + x.value, 0));
  const max = Math.max(...totals, 0);
  return (
    <div>
      <div className="flex h-40 items-end gap-1 border-b border-gray-400" role="img" aria-label={label}>
        {bars.map((b, i) => (
          <div key={b.key} className="group relative flex h-full flex-1 items-end">
            {/* stack, with a 2px surface gap between segments and a rounded top */}
            <div
              className="flex w-full flex-col-reverse gap-[2px] overflow-hidden rounded-t-[4px] transition-opacity group-hover:opacity-80"
              style={{ height: max ? `${(100 * totals[i]) / max}%` : 0, minHeight: totals[i] ? 3 : 0 }}
            >
              {b.segments
                .filter((s) => s.value > 0)
                .map((s, j) => (
                  // grow by share of the bar: grow factors summing below 1 (e.g. costs) would leave the bar unfilled
                  <div key={j} className={s.className} style={{ flexGrow: s.value / totals[i], flexBasis: 0, minHeight: 1 }} />
                ))}
            </div>
            <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 hidden w-48 -translate-x-1/2 rounded-lg border border-gray-400 bg-white p-2.5 text-xs shadow-lg group-hover:block">
              {b.tooltip.map((line, k) => (
                <p key={k} className={k === 0 ? "mb-0.5 font-medium" : "text-text-secondary"}>
                  {line}
                </p>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-xs text-text-secondary">
        <span>{bars[0]?.key.slice(5)}</span>
        <span>max {format(max)}</span>
        <span>{bars[bars.length - 1]?.key.slice(5)}</span>
      </div>
    </div>
  );
}

// Continuous day axis: days without data show as empty bars instead of being skipped
export function lastDays(n: number) {
  return Array.from({ length: n }, (_, i) => new Date(Date.now() - (n - 1 - i) * 86_400_000).toISOString().slice(0, 10));
}
