/** Plugin registry: state, health, quotas, secrets and security audit. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { Card, ErrorBox, Loading, Modal, StatusBadge, Warning } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { api } from '@/lib/api'
import type { Plugin, PluginAudit } from '@/types'

export default function Plugins() {
  const client = useQueryClient()
  const { can } = useAuth()
  const [auditing, setAuditing] = useState<string | null>(null)
  const [secretFor, setSecretFor] = useState<Plugin | null>(null)
  const [confirming, setConfirming] = useState<Plugin | null>(null)

  const plugins = useQuery({
    queryKey: ['plugins'],
    queryFn: () => api.get<Plugin[]>('/plugins'),
  })

  const toggle = useMutation({
    mutationFn: ({ name, enabled, ack }: { name: string; enabled: boolean; ack: boolean }) =>
      api.post(`/plugins/${name}/toggle`, { enabled, acknowledge_risks: ack }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['plugins'] })
      setConfirming(null)
    },
  })

  const health = useMutation({
    mutationFn: (name: string) => api.post(`/plugins/${name}/health`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['plugins'] }),
  })

  if (plugins.isLoading) return <Loading />
  if (plugins.error) return <ErrorBox error={plugins.error} />

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Plugins OSINT</h1>
        <p className="text-sm text-slate-500">
          Every third-party tool runs in its own container, with no access to the
          database or the host. Audit before enabling.
        </p>
      </header>

      {toggle.error != null && <ErrorBox error={toggle.error} />}
      {health.error != null && <ErrorBox error={health.error} />}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {(plugins.data ?? []).map((plugin) => (
          <Card
            key={plugin.name}
            title={
              <span className="flex items-center gap-2">
                {plugin.name}
                <span className="text-xs text-slate-600 normal-case">v{plugin.version}</span>
                <StatusBadge
                  status={plugin.enabled ? 'CONFIRMED' : 'UNKNOWN'}
                  label={plugin.enabled ? 'ENABLED' : 'DISABLED'}
                />
                <StatusBadge status={plugin.risk_level} label={`risk ${plugin.risk_level}`} />
              </span>
            }
          >
            <div className="space-y-3 text-sm">
              <p className="text-slate-400">{plugin.description}</p>

              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <dt className="text-slate-600">Repository</dt>
                <dd className="truncate">
                  {plugin.repository?.startsWith('http') ? (
                    <a
                      className="text-accent hover:underline"
                      href={plugin.repository}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {plugin.repository}
                    </a>
                  ) : (
                    <span className="text-slate-400">{plugin.repository}</span>
                  )}
                </dd>
                <dt className="text-slate-600">Licence</dt>
                <dd className="text-slate-400">{plugin.license}</dd>
                <dt className="text-slate-600">Identifiers</dt>
                <dd className="text-slate-400">{plugin.supported_identifiers.join(', ')}</dd>
                <dt className="text-slate-600">Queue / container</dt>
                <dd className="text-slate-400">{plugin.queue}</dd>
                <dt className="text-slate-600">Quotas</dt>
                <dd className="text-slate-400">
                  {plugin.limits.requests_per_minute} req/min ·{' '}
                  {plugin.limits.concurrency} in parallel · timeout{' '}
                  {plugin.limits.timeout_seconds}s
                </dd>
                <dt className="text-slate-600">Health</dt>
                <dd className="text-slate-400">
                  {plugin.health_status ?? 'not tested'}
                  {plugin.health_message && (
                    <span className="block text-slate-600">{plugin.health_message}</span>
                  )}
                </dd>
                <dt className="text-slate-600">Last audit</dt>
                <dd className="text-slate-400">
                  {plugin.last_audit_at
                    ? new Date(plugin.last_audit_at).toLocaleString()
                    : 'never'}
                </dd>
              </dl>

              {plugin.risk_notes.length > 0 && (
                <Warning>
                  <ul className="list-disc ml-4 space-y-0.5">
                    {plugin.risk_notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                </Warning>
              )}

              {plugin.requires_secrets.length > 0 && (
                <p className="text-xs text-slate-500">
                  Required secrets:{' '}
                  {plugin.requires_secrets
                    .map(
                      (key) =>
                        `${key} (${plugin.secrets_configured[key] ? 'configured' : 'missing'})`,
                    )
                    .join(', ')}
                </p>
              )}

              <div className="flex flex-wrap gap-2 pt-2 border-t border-line">
                {can('ADMIN') && (
                  <button
                    className={`btn ${plugin.enabled ? 'btn-danger' : 'btn-primary'}`}
                    onClick={() => {
                      if (!plugin.enabled && plugin.risk_notes.length > 0) {
                        setConfirming(plugin)
                      } else {
                        toggle.mutate({ name: plugin.name, enabled: !plugin.enabled, ack: true })
                      }
                    }}
                  >
                    {plugin.enabled ? 'Disable' : 'Enable'}
                  </button>
                )}
                {can('ADMIN') && (
                  <button className="btn" onClick={() => setAuditing(plugin.name)}>
                    Security audit
                  </button>
                )}
                {can('ADMIN') && (
                  <button
                    className="btn"
                    disabled={health.isPending}
                    onClick={() => health.mutate(plugin.name)}
                  >
                    Test health
                  </button>
                )}
                {can('ADMIN') && plugin.requires_secrets.length > 0 && (
                  <button className="btn" onClick={() => setSecretFor(plugin)}>
                    Configure secrets
                  </button>
                )}
              </div>
            </div>
          </Card>
        ))}
      </div>

      {auditing && <AuditModal name={auditing} onClose={() => setAuditing(null)} />}
      {secretFor && <SecretModal plugin={secretFor} onClose={() => setSecretFor(null)} />}
      {confirming && (
        <Modal title={`Enable ${confirming.name}?`} onClose={() => setConfirming(null)}>
          <div className="space-y-3">
            <Warning>
              <ul className="list-disc ml-4 space-y-1">
                {confirming.risk_notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Warning>
            <p className="text-sm text-slate-400">
              By enabling this plugin you confirm that you have read these warnings
              and accept using the tool within an authorised framework.
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn" onClick={() => setConfirming(null)}>Cancel</button>
              <button
                className="btn btn-primary"
                onClick={() =>
                  toggle.mutate({ name: confirming.name, enabled: true, ack: true })
                }
              >
                I have read this, enable
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

function AuditModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['plugin-audit', name],
    queryFn: () => api.get<PluginAudit>(`/plugins/${name}/audit`),
  })

  return (
    <Modal title={`Security report — ${name}`} onClose={onClose}>
      {isLoading && <Loading label="Analysing the code..." />}
      {error != null && <ErrorBox error={error} />}
      {data && (
        <div className="space-y-4 text-sm">
          <div className="flex items-center gap-3">
            <StatusBadge status={data.risk_level} label={`Risk ${data.risk_level}`} />
            <span className="text-slate-500 text-xs">
              {data.files_scanned} file(s) analysed
            </span>
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
            {[
              ['Repository', data.repository],
              ['License', data.license],
              ['Upstream version', data.version],
              ['Last upstream update', data.last_upstream_update],
              ['Last review', data.last_reviewed],
              ['Network access', data.network_access],
              ['Filesystem access', data.filesystem_access],
              ['Subprocess', data.subprocess],
              ['Dynamic downloads', data.dynamic_downloads],
              ['Privileged operations', data.privileged_operations],
              ['Docker socket', data.docker_socket],
              ['Hardcoded secrets', data.hardcoded_secrets],
              ['Suspicious behaviour', data.suspicious_behavior],
            ].map(([label, value]) => (
              <div key={String(label)} className="contents">
                <dt className="text-slate-600">{label}</dt>
                <dd
                  className={
                    value === 'YES' && String(label).match(/Docker|privilegiees|dur/)
                      ? 'text-danger'
                      : 'text-slate-300'
                  }
                >
                  {value ?? '—'}
                </dd>
              </div>
            ))}
          </dl>

          {data.dependencies.length > 0 && (
            <details>
              <summary className="text-xs text-slate-600 cursor-pointer">
                Dependencies ({data.dependencies.length})
              </summary>
              <pre className="mt-1 p-2 bg-base-900 border border-line rounded text-xs overflow-x-auto text-slate-400">
                {data.dependencies.join('\n')}
              </pre>
            </details>
          )}

          {data.signals.length > 0 && (
            <details>
              <summary className="text-xs text-slate-600 cursor-pointer">
                Signals detected ({data.signals.length})
              </summary>
              <ul className="mt-1 space-y-1 text-xs">
                {data.signals.map((signal, index) => (
                  <li key={index} className="font-mono">
                    <span
                      className={
                        signal.severity === 'CRITICAL' || signal.severity === 'HIGH'
                          ? 'text-danger'
                          : 'text-slate-500'
                      }
                    >
                      [{signal.severity}]
                    </span>{' '}
                    {signal.file}:{signal.line} — {signal.code}
                    <span className="block text-slate-600 font-sans ml-4">
                      {signal.explanation}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {data.errors.length > 0 && (
            <Warning>
              <ul className="list-disc ml-4">
                {data.errors.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </Warning>
          )}

          <p className="text-xs text-slate-500 border-t border-line pt-2">{data.disclaimer}</p>
        </div>
      )}
    </Modal>
  )
}

function SecretModal({ plugin, onClose }: { plugin: Plugin; onClose: () => void }) {
  const client = useQueryClient()
  const [key, setKey] = useState(plugin.requires_secrets[0] ?? '')
  const [value, setValue] = useState('')

  const save = useMutation({
    mutationFn: () => api.put(`/plugins/${plugin.name}/secrets`, { key, value }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['plugins'] })
      setValue('')
      onClose()
    },
  })

  return (
    <Modal title={`Secrets — ${plugin.name}`} onClose={onClose}>
      <div className="space-y-3">
        <Warning>
          NEVER enter an account password. Only tokens or session cookies are
          accepted. The value is encrypted before storage and is never displayed again
          nor logged.
        </Warning>

        {save.error != null && <ErrorBox error={save.error} />}

        <div>
          <label className="label">Key</label>
          <select className="input" value={key} onChange={(event) => setKey(event.target.value)}>
            {plugin.requires_secrets.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="label">Value</label>
          <input
            className="input font-mono"
            type="password"
            autoComplete="off"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          {plugin.name === 'toutatis' && (
            <p className="text-[11px] text-slate-600 mt-1">
              `sessionid` cookie taken manually from your own Instagram session
              (browser developer tools, Application &gt; Cookies tab). This cookie grants
              access to the account: use a dedicated account and revoke it by logging out
              once you no longer need it.
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary"
            disabled={!value || save.isPending}
            onClick={() => save.mutate()}
          >
            Save (encrypted)
          </button>
        </div>
      </div>
    </Modal>
  )
}
