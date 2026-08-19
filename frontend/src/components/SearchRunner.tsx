/**
 * Launching a search plus live monitoring of the plugin runs.
 * The plan is shown before execution: the analyst sees what will be queried.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { Card, ErrorBox, StatusBadge, Warning } from '@/components/ui'
import { api, query } from '@/lib/api'
import type { IdentifierType, Search, SearchProgress } from '@/types'

interface PlanResponse {
  normalized_value: string
  planned: Array<{ plugin: string; type: string; value: string }>
  compatible_plugins: Array<{ name: string; description: string; contextual: boolean }>
  note: string
}

export default function SearchRunner({
  targetType,
  targetValue,
  personId,
  investigationId,
  defaultDepth = 1,
  onFinished,
}: {
  targetType: IdentifierType
  targetValue: string
  personId?: string
  investigationId?: string
  defaultDepth?: number
  onFinished?: () => void
}) {
  const client = useQueryClient()
  const [depth, setDepth] = useState(defaultDepth)
  const [force, setForce] = useState(false)
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [searchId, setSearchId] = useState<string | null>(null)

  const plan = useQuery({
    queryKey: ['plan', targetType, targetValue, personId, force],
    queryFn: () =>
      api.get<PlanResponse>(
        `/searches/preview/plan${query({
          target_type: targetType,
          target_value: targetValue,
          person_id: personId,
          force,
        })}`,
      ),
    enabled: targetValue.trim().length > 0,
  })

  const launch = useMutation({
    mutationFn: () => {
      const selected = (plan.data?.compatible_plugins ?? [])
        .map((item) => item.name)
        .filter((name) => !excluded.has(name))
      return api.post<Search>('/searches', {
        target_type: targetType,
        target_value: targetValue,
        person_id: personId,
        investigation_id: investigationId,
        depth,
        differential: !force,
        force,
        plugins: selected.length ? selected : undefined,
      })
    },
    onSuccess: (search) => setSearchId(search.id),
  })

  const progress = useQuery({
    queryKey: ['search-progress', searchId],
    queryFn: () => api.get<SearchProgress>(`/searches/${searchId}/progress`),
    enabled: Boolean(searchId),
    refetchInterval: (queryInfo) => {
      const state = queryInfo.state.data
      if (!state) return 2000
      const done = ['SUCCESS', 'FAILED', 'PARTIAL', 'SKIPPED'].includes(state.status)
      if (done) {
        client.invalidateQueries({ queryKey: ['person'] })
        onFinished?.()
        return false
      }
      return 2000
    },
  })

  const planned = plan.data?.planned ?? []

  return (
    <div className="space-y-4">
      <Card title="Execution plan">
        {plan.error != null && <ErrorBox error={plan.error} />}
        {plan.data && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              Normalised value:{' '}
              <span className="font-mono text-slate-300">{plan.data.normalized_value}</span>
            </p>

            <div className="space-y-1">
              {plan.data.compatible_plugins.length === 0 && (
                <p className="text-sm text-slate-500">
                  No enabled plugin supports this identifier type.
                </p>
              )}
              {plan.data.compatible_plugins.map((item) => {
                const willRun = planned.some((step) => step.plugin === item.name)
                return (
                  <label key={item.name} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={!excluded.has(item.name)}
                      onChange={(event) => {
                        const next = new Set(excluded)
                        event.target.checked ? next.delete(item.name) : next.add(item.name)
                        setExcluded(next)
                      }}
                    />
                    <span>
                      <span className="text-slate-200">{item.name}</span>
                      {!willRun && (
                        <span className="text-slate-600">
                          {' '}
                          — already run recently (differential search)
                        </span>
                      )}
                      {item.contextual && (
                        <span className="text-warn"> — contextual trigger only</span>
                      )}
                      <span className="block text-xs text-slate-600">{item.description}</span>
                    </span>
                  </label>
                )
              })}
            </div>

            <div className="flex flex-wrap items-center gap-4 pt-2 border-t border-line">
              <div className="flex items-center gap-2">
                <span className="text-xs uppercase tracking-wide text-slate-500">Depth</span>
                {[1, 2, 3, 4].map((level) => (
                  <button
                    key={level}
                    className={`btn px-2.5 py-1 ${depth === level ? 'btn-primary' : ''}`}
                    onClick={() => setDepth(level)}
                    type="button"
                  >
                    {level}
                  </button>
                ))}
              </div>

              <label className="flex items-center gap-2 text-sm text-slate-400">
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(event) => setForce(event.target.checked)}
                />
                Re-run everything (ignore the differential)
              </label>

              <button
                className="btn btn-primary ml-auto"
                disabled={launch.isPending || planned.length === 0}
                onClick={() => launch.mutate()}
              >
                {launch.isPending ? 'Starting...' : 'Start the search'}
              </button>
            </div>

            <p className="text-[11px] text-slate-600">{plan.data.note}</p>
            {depth > 2 && (
              <Warning>
                Depth {depth}: discovered entities will be queried in turn. The
                request volume grows fast — check the plugin quotas.
              </Warning>
            )}
          </div>
        )}
        {launch.error != null && <ErrorBox error={launch.error} />}
      </Card>

      {progress.data && (
        <Card title={`Run — ${progress.data.status}`}>
          <div className="space-y-2">
            {progress.data.runs.map((run) => {
              const percent =
                run.status === 'SUCCESS' || run.status === 'FAILED'
                  ? 100
                  : run.status === 'RUNNING'
                    ? 60
                    : 10
              return (
                <div key={run.id} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-200">
                      {run.plugin}
                      <span className="text-slate-600 text-xs font-mono">
                        {' '}
                        {run.target_type}={run.target_value} (level {run.depth})
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-xs text-slate-500">
                        {run.items_found} item(s)
                      </span>
                      <StatusBadge status={run.status} />
                    </span>
                  </div>
                  <div className="h-1.5 bg-base-900 rounded overflow-hidden border border-line">
                    <div
                      className={`h-full transition-all ${
                        run.status === 'FAILED' ? 'bg-danger' : 'bg-accent'
                      }`}
                      style={{ width: `${percent}%` }}
                    />
                  </div>
                  {run.error && <p className="text-xs text-danger">{run.error}</p>}
                  {run.logs && run.logs.length > 0 && (
                    <details className="text-xs">
                      <summary className="text-slate-600 cursor-pointer">Logs</summary>
                      <pre className="mt-1 p-2 bg-base-900 border border-line rounded overflow-x-auto text-slate-400">
                        {run.logs.join('\n')}
                      </pre>
                    </details>
                  )}
                </div>
              )
            })}
          </div>
        </Card>
      )}
    </div>
  )
}
