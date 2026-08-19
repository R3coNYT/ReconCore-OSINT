import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { Card, ConfidenceBar, Empty, ErrorBox, Loading, Stat, StatusBadge } from '@/components/ui'
import { api } from '@/lib/api'
import type { Dashboard as DashboardData } from '@/types'

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get<DashboardData>('/dashboard'),
    refetchInterval: 20_000,
  })

  if (isLoading) return <Loading />
  if (error) return <ErrorBox error={error} />
  if (!data) return null

  const counts = data.counts

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Dashboard</h1>
        <p className="text-sm text-slate-500">Overview of investigation activity.</p>
      </header>

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
        <Stat label="Case files" value={counts.investigations} />
        <Stat label="People" value={counts.persons} />
        <Stat label="Usernames" value={counts.usernames} />
        <Stat label="Social profiles" value={counts.social_profiles} />
        <Stat label="Sources" value={counts.sources} />
        <Stat
          label="Findings to triage"
          value={counts.new_findings}
          hint={`${counts.findings} in total`}
        />
      </div>

      {(counts.open_contradictions > 0 || counts.running_searches > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {counts.open_contradictions > 0 && (
            <div className="card p-4 border-warn/40">
              <p className="text-warn text-sm">
                {counts.open_contradictions} unresolved contradiction(s): an analyst
                must decide, the system never chooses for you.
              </p>
            </div>
          )}
          {counts.running_searches > 0 && (
            <div className="card p-4 border-accent/40">
              <p className="text-accent text-sm">
                {counts.running_searches} search(es) running.{' '}
                <Link to="/history" className="underline">Track progress</Link>
              </p>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Latest results to validate">
          {data.recent_findings.length === 0 ? (
            <Empty message="No pending results." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Plugin</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_findings.map((finding) => (
                  <tr key={finding.id}>
                    <td>
                      {finding.person_id ? (
                        <Link
                          className="text-accent hover:underline"
                          to={`/persons/${finding.person_id}?tab=findings`}
                        >
                          {finding.title}
                        </Link>
                      ) : (
                        finding.title
                      )}
                      <span className="block text-xs text-slate-600">{finding.type}</span>
                    </td>
                    <td className="text-slate-400">{finding.plugin ?? '-'}</td>
                    <td><ConfidenceBar value={finding.confidence} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Recent searches">
          {data.recent_searches.length === 0 ? (
            <Empty message="No search launched." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_searches.map((search) => (
                  <tr key={search.id}>
                    <td className="font-mono text-xs">{search.label ?? '-'}</td>
                    <td><StatusBadge status={search.status} /></td>
                    <td className="text-slate-500 text-xs">
                      {new Date(search.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="Activity per plugin">
        {data.plugin_activity.length === 0 ? (
          <Empty message="No plugin has run yet." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Plugin</th>
                <th>Runs</th>
                <th>Items produced</th>
              </tr>
            </thead>
            <tbody>
              {data.plugin_activity.map((row) => (
                <tr key={row.plugin}>
                  <td className="font-medium text-slate-200">{row.plugin}</td>
                  <td>{row.runs}</td>
                  <td>{row.items}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
