import clsx from "clsx";

interface Props {
  value: number;
}

export function ConfidenceBadge({ value }: Props) {
  const pct = Math.round(value * 100);
  return (
    <span
      className={clsx(
        "inline-block px-2 py-0.5 rounded text-xs font-semibold",
        pct >= 75 ? "bg-green-100 text-green-800" :
        pct >= 50 ? "bg-yellow-100 text-yellow-800" :
                    "bg-red-100 text-red-800"
      )}
    >
      {pct}%
    </span>
  );
}
