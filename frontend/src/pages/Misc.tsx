/** Secondary pages: sources, history, settings. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Card, ConfidenceBar, Empty, ErrorBox, Loading, StatusBadge, Warning } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { api, query } from '@/lib/api'
import type { Platform, Search, Source, User } from '@/types'

export function SourcesPage() {
  const [plugin, setPlugin] = useState('')
  const { data, isLoading } = useQuery({
    queryKey: ['all-sources', plugin],
    queryFn: () => api.get<Source[]>(`/sources${query({ plugin, limit: 200 })}`),
  })

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Sources</h1>
        <p className="text-sm text-slate-500">
          Every stored item comes from a timestamped, rated source.
        </p>
      </header>

      <input
        className="input max-w-xs"
        placeholder="Filter by plugin (sherlock, holehe...)"
        value={plugin}
        onChange={(event) => setPlugin(event.target.value)}
      />

      <Card title={`${data?.length ?? 0} source(s)`}>
        {isLoading && <Loading />}
        {data?.length === 0 && <Empty message="No sources." />}
        {data && data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Title / URL</th>
                <th>Plugin</th>
                <th>Reliability</th>
                <th>Discovered</th>
              </tr>
            </thead>
            <tbody>
              {data.map((source) => (
                <tr key={source.id}>
                  <td className="text-xs text-slate-500">{source.kind}</td>
                  <td>
                    <span className="text-slate-300">{source.title ?? '—'}</span>
                    {source.url && (
                      <a
                        className="block text-xs text-accent hover:underline break-all"
                        href={source.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {source.url}
                      </a>
                    )}
                  </td>
                  <td className="text-slate-400">{source.plugin ?? '—'}</td>
                  <td><ConfidenceBar value={source.reliability} /></td>
                  <td className="text-xs text-slate-500">
                    {source.date_discovered
                      ? new Date(source.date_discovered).toLocaleString()
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}

export function HistoryPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['all-searches'],
    queryFn: () => api.get<Search[]>('/searches?limit=100'),
    refetchInterval: 10_000,
  })

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Search history</h1>
        <p className="text-sm text-slate-500">
          Every campaign is recorded: target, depth, plugins, results.
        </p>
      </header>

      <Card>
        {isLoading && <Loading />}
        {data?.length === 0 && <Empty message="No searches." />}
        {data && data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Target</th>
                <th>Depth</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Results</th>
                <th>Person</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {data.map((search) => (
                <tr key={search.id}>
                  <td className="font-mono text-xs">
                    {search.target_type}={search.target_value}
                  </td>
                  <td>{search.depth}</td>
                  <td className="text-xs text-slate-500">
                    {search.differential ? 'differential' : 'full'}
                  </td>
                  <td><StatusBadge status={search.status} /></td>
                  <td className="text-xs text-slate-400">
                    {String((search.stats as { items?: number } | null)?.items ?? '—')}
                  </td>
                  <td>
                    {search.person_id ? (
                      <Link
                        className="text-accent hover:underline text-xs"
                        to={`/persons/${search.person_id}`}
                      >
                        open
                      </Link>
                    ) : (
                      <span className="text-slate-600 text-xs">—</span>
                    )}
                  </td>
                  <td className="text-xs text-slate-500">
                    {new Date(search.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}

export function SettingsPage() {
  const { user, can } = useAuth()
  const client = useQueryClient()
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' })

  const users = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<User[]>('/users'),
    enabled: can('ADMIN'),
  })
  const platforms = useQuery({
    queryKey: ['platforms'],
    queryFn: () => api.get<Platform[]>('/platforms'),
  })

  const changePassword = useMutation({
    mutationFn: () => api.post<{ detail: string }>('/auth/password', passwords),
    onSuccess: () => setPasswords({ current_password: '', new_password: '' }),
  })

  const toggleUser = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.patch(`/users/${id}`, { is_active }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['users'] }),
  })

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
        <p className="text-sm text-slate-500">Account, users and known platforms.</p>
      </header>

      <Card title="My account">
        <div className="space-y-3 max-w-md">
          <p className="text-sm text-slate-400">
            {user?.email} — role <span className="text-slate-200">{user?.role}</span>
          </p>

          {changePassword.error != null && <ErrorBox error={changePassword.error} />}
          {changePassword.data && (
            <p className="text-sm text-ok">{changePassword.data.detail}</p>
          )}

          <form
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault()
              changePassword.mutate()
            }}
          >
            <div>
              <label className="label">Current password</label>
              <input
                className="input"
                type="password"
                autoComplete="current-password"
                value={passwords.current_password}
                onChange={(event) =>
                  setPasswords({ ...passwords, current_password: event.target.value })
                }
              />
            </div>
            <div>
              <label className="label">New password (12 characters minimum)</label>
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={passwords.new_password}
                onChange={(event) =>
                  setPasswords({ ...passwords, new_password: event.target.value })
                }
              />
            </div>
            <button className="btn btn-primary" disabled={changePassword.isPending}>
              Change password
            </button>
            <p className="text-[11px] text-slate-600">
              Changing it revokes every active session.
            </p>
          </form>
        </div>
      </Card>

      {can('ADMIN') && (
        <Card title="Users">
          <Warning>
            Roles: ADMIN (configuration, plugins, secrets), ANALYST (investigations),
            READ_ONLY (read access). Every sensitive action is logged.
          </Warning>
          {users.isLoading && <Loading />}
          {users.data && (
            <table className="table mt-3">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Active</th>
                  <th>Last login</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.data.map((item) => (
                  <tr key={item.id}>
                    <td className="text-slate-200">{item.email}</td>
                    <td className="text-slate-400">{item.role}</td>
                    <td>
                      <StatusBadge
                        status={item.is_active ? 'CONFIRMED' : 'REJECTED'}
                        label={item.is_active ? 'active' : 'disabled'}
                      />
                    </td>
                    <td className="text-xs text-slate-500">
                      {item.last_login_at
                        ? new Date(item.last_login_at).toLocaleString()
                        : 'never'}
                    </td>
                    <td className="text-right">
                      {item.id !== user?.id && (
                        <button
                          className="btn px-2 py-1"
                          onClick={() =>
                            toggleUser.mutate({ id: item.id, is_active: !item.is_active })
                          }
                        >
                          {item.is_active ? 'Deactivate' : 'Reactivate'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-slate-600 mt-3">
            Accounts are created through the API or the CLI:{' '}
            <code className="font-mono">osint user create --email ... --role ANALYST</code>
          </p>
        </Card>
      )}

      <Card title={`Known platforms (${platforms.data?.length ?? 0})`}>
        {platforms.isLoading && <Loading />}
        <div className="flex flex-wrap gap-2">
          {(platforms.data ?? []).map((platform) => (
            <span
              key={platform.id}
              className="px-2 py-1 rounded bg-base-700 border border-line text-xs"
            >
              {platform.name}
              <span className="text-slate-600"> · {platform.category}</span>
            </span>
          ))}
        </div>
      </Card>
    </div>
  )
}
