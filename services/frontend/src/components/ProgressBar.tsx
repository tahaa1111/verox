interface Props {
  pct: number;
}

export function ProgressBar({ pct }: Props) {
  const clamped = Math.min(100, Math.max(0, pct));
  return (
    <div className="w-full bg-gray-200 rounded-full h-2.5">
      <div
        className="bg-brand-500 h-2.5 rounded-full transition-all duration-500"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
