// Single-series bar chart with a hover tooltip per bar; numbers are also shown in a table on each page
export default function BarChart({
  bars,
  label,
  unit = "",
}: {
  bars: { key: string; value: number; tooltip: string[] }[];
  label: string;
  unit?: string;
}) {
  const max = Math.max(...bars.map((b) => b.value), 0);
  return (
    <div>
      <div className="mt-4 flex h-48 items-end gap-0.5 border-b border-border" role="img" aria-label={label}>
        {bars.map((b) => (
          <div key={b.key} className="group relative flex h-full flex-1 items-end">
            {/* the hover area is as tall as the chart, so thin bars are easy to hit */}
            <div
              className="w-full rounded-t bg-gray-800 group-hover:bg-gray-600"
              style={{ height: max ? `${(100 * b.value) / max}%` : 0, minHeight: b.value ? 2 : 0 }}
            />
            <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 hidden w-48 -translate-x-1/2 rounded-md border border-border bg-background p-2 text-xs shadow-md group-hover:block">
              {b.tooltip.map((line, i) => (
                <p key={i} className={i === 0 ? "font-medium" : "text-text-secondary"}>
                  {line}
                </p>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-xs text-text-secondary">
        <span>{bars[0]?.key}</span>
        <span>
          max {max.toLocaleString()}
          {unit} / day
        </span>
        <span>{bars[bars.length - 1]?.key}</span>
      </div>
    </div>
  );
}

// Continuous day axis: days without data show as empty bars instead of being skipped
export function lastDays(n: number) {
  return Array.from({ length: n }, (_, i) => new Date(Date.now() - (n - 1 - i) * 86_400_000).toISOString().slice(0, 10));
}
