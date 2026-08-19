/** Shared UI building blocks (dark SOC theme). */
import type { ReactNode } from 'react'

import type { Score, VerificationStatus } from '@/types'

export function Card({
  title,
  actions,
  children,
  className = '',
}: {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-header flex items-center justify-between gap-3">
          <span>{title}</span>
          <span className="flex items-center gap-2 normal-case tracking-normal">{actions}</span>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

const STATUS_STYLES: Record<string, string> = {
  CONFIRMED: 'bg-ok/15 text-ok border-ok/40',
  PROBABLE: 'bg-warn/15 text-warn border-warn/40',
  HYPOTHESIS: 'bg-hypo/15 text-hypo border-hypo/40',
  UNKNOWN: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
  REJECTED: 'bg-danger/15 text-danger border-danger/40',
  NEW: 'bg-accent/15 text-accent border-accent/40',
  UNVERIFIED: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
  OUTDATED: 'bg-slate-500/10 text-slate-500 border-slate-500/30',
  CONTRADICTED: 'bg-danger/15 text-danger border-danger/40',
  SUCCESS: 'bg-ok/15 text-ok border-ok/40',
  RUNNING: 'bg-accent/15 text-accent border-accent/40',
  PENDING: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
  FAILED: 'bg-danger/15 text-danger border-danger/40',
  PARTIAL: 'bg-warn/15 text-warn border-warn/40',
  LOW: 'bg-ok/15 text-ok border-ok/40',
  MEDIUM: 'bg-warn/15 text-warn border-warn/40',
  HIGH: 'bg-danger/15 text-danger border-danger/40',
  CRITICAL: 'bg-danger/25 text-danger border-danger/60',
}

const STATUS_LABELS: Record<string, string> = {
  CONFIRMED: 'Confirmed',
  PROBABLE: 'Probable',
  HYPOTHESIS: 'Hypothesis',
  UNKNOWN: 'To verify',
  REJECTED: 'Rejected',
  NEW: 'New',
  UNVERIFIED: 'Unverified',
  OUTDATED: 'Outdated',
  CONTRADICTED: 'Contradicted',
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const style = STATUS_STYLES[status] ?? 'bg-slate-500/10 text-slate-400 border-slate-500/30'
  return (
    <span className={`inline-block px-2 py-0.5 rounded border text-[11px] font-medium ${style}`}>
      {label ?? STATUS_LABELS[status] ?? status}
    </span>
  )
}

export function ConfidenceBar({ value }: { value: number }) {
  const percent = Math.round(Math.max(0, Math.min(1, value)) * 100)
  const color = percent >= 75 ? 'bg-ok' : percent >= 50 ? 'bg-warn' : percent >= 25 ? 'bg-hypo' : 'bg-slate-600'
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <div className="h-1.5 flex-1 bg-base-900 rounded overflow-hidden border border-line">
        <div className={`h-full ${color}`} style={{ width: `${percent}%` }} />
      </div>
      <span className="text-xs text-slate-400 font-mono w-8 text-right">{percent}%</span>
    </div>
  )
}

/** Shows the full breakdown: a score without justification is not usable. */
export function ScorePanel({ score }: { score: Score }) {
  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3">
        <span className="text-3xl font-semibold text-slate-100">{score.score}%</span>
        <StatusBadge status={score.verdict} label={verdictLabel(score.verdict)} />
      </div>
      <div>
        <p className="text-xs uppercase tracking-wide text-slate-500 mb-2">Why?</p>
        <ul className="space-y-1">
          {score.breakdown.length === 0 && (
            <li className="text-sm text-slate-500">No usable signal yet.</li>
          )}
          {score.breakdown.map((item) => (
            <li key={item.code} className="flex items-start gap-2 text-sm">
              <span
                className={`font-mono w-10 shrink-0 text-right ${
                  item.points >= 0 ? 'text-ok' : 'text-danger'
                }`}
              >
                {item.points >= 0 ? `+${item.points}` : item.points}
              </span>
              <span className="text-slate-300">
                {item.label}
                {item.detail && <span className="text-slate-500"> - {item.detail}</span>}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-slate-500 border-t border-line pt-2">{score.disclaimer}</p>
    </div>
  )
}

function verdictLabel(verdict: string): string {
  const labels: Record<string, string> = {
    STRONG_MATCH: 'Strong match',
    POSSIBLE_MATCH: 'Possible match',
    WEAK_SIGNAL: 'Weak signal',
    INSUFFICIENT: 'Insufficient',
    CONFIRMED: 'Confirmed by an analyst',
    REJECTED: 'Rejected by an analyst',
  }
  return labels[verdict] ?? verdict
}

export function Empty({ message }: { message: string }) {
  return <p className="text-sm text-slate-500 py-6 text-center">{message}</p>
}

export function Loading({ label = 'Loading...' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500 py-6 justify-center">
      <span className="w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin" />
      {label}
    </div>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div className="border border-danger/40 bg-danger/10 text-danger text-sm rounded-md px-3 py-2">
      {message}
    </div>
  )
}

export function Warning({ children }: { children: ReactNode }) {
  return (
    <div className="border border-warn/40 bg-warn/10 text-warn text-sm rounded-md px-3 py-2 flex gap-2">
      <span aria-hidden>⚠</span>
      <span>{children}</span>
    </div>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="text-2xl font-semibold text-slate-100 mt-1">{value}</p>
      {hint && <p className="text-xs text-slate-500 mt-1">{hint}</p>}
    </div>
  )
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  return (
    <div
      className="fixed inset-0 bg-black/70 z-50 flex items-start justify-center p-6 overflow-y-auto"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-2xl mt-10"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="card-header flex items-center justify-between">
          <span>{title}</span>
          <button className="text-slate-500 hover:text-slate-200" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  )
}

export const STATUS_OPTIONS: VerificationStatus[] = [
  'UNKNOWN',
  'HYPOTHESIS',
  'PROBABLE',
  'CONFIRMED',
  'REJECTED',
]
