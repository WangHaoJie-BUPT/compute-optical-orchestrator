import type { SpectrumBand } from "../types";

const lower = -120;
const upper = 40;
const span = upper - lower;

export function SpectrumMap({ bands }: { bands: SpectrumBand[] }) {
  return (
    <div className="spectrum-panel">
      <div className="spectrum-axis" aria-label="Spectrum allocation map">
        {bands.map((band) => {
          const left = ((band.start - lower) / span) * 100;
          const width = ((band.end - band.start) / span) * 100;
          return (
            <div
              key={`${band.owner}-${band.start}`}
              className={`spectrum-band spectrum-${band.status}`}
              style={{ left: `${left}%`, width: `${Math.max(width, 3)}%` }}
              title={`${band.owner}: ${band.start} to ${band.end}`}
            />
          );
        })}
        <div className="spectrum-ticks" aria-hidden="true">
          {[-120, -80, -40, 0, 40].map((tick) => (
            <span key={tick} style={{ left: `${((tick - lower) / span) * 100}%` }}>
              {tick}
            </span>
          ))}
        </div>
      </div>
      <div className="spectrum-list">
        {bands.map((band) => (
          <div className="spectrum-row" key={`${band.owner}-${band.start}-row`}>
            <span className={`spectrum-swatch spectrum-${band.status}`} />
            <strong>{band.owner}</strong>
            <span>{band.route}</span>
            <code>
              {band.start}..{band.end}
            </code>
          </div>
        ))}
      </div>
    </div>
  );
}
