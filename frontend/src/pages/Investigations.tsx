import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { Card, Empty, ErrorBox, Loading, Modal } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { api, query } from '@/lib/api'
import type { Investigation } from '@/types'

const ENTITY_TYPES = ['PERSON', 'ORGANIZATION', 'COMPANY', 'DOMAIN', 'PSEUDONYM', 'OTHER']

export default function Investigations() {
  const [params] = useSearchParams()
  const entityType = params.get('entity_type') ?? ''
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const { can } = useAuth()

  const { data, isLoading, error } = useQuery({
    queryKey: ['investigations', entityType, search],
    queryFn: () =>
      api.get<Investigation[]>(`/investigations${query({ entity_type: entityType, search })}`),
  })

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Investigation case files</h1>
          <p className="text-sm text-slate-500">
            {entityType ? `Filter: ${entityType}` : 'All case files'}
          </p>
        </div>
        {can('ANALYST') && (
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            + New case file
          </button>
        )}
      </header>

      <input
        className="input max-w-md"
        placeholder="Search case files by title..."
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />

      {isLoading && <Loading />}
      {error != null && <ErrorBox error={error} />}

      {data && (
        <Card>
          {data.length === 0 ? (
            <Empty message="No case file yet. Create one to start an investigation." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Automation</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {data.map((investigation) => (
                  <tr key={investigation.id}>
                    <td>
                      <Link
                        to={`/investigations/${investigation.id}`}
                        className="text-accent hover:underline font-medium"
                      >
                        {investigation.title}
                      </Link>
                      {investigation.description && (
                        <span className="block text-xs text-slate-600 line-clamp-1">
                          {investigation.description}
                        </span>
                      )}
                    </td>
                    <td className="text-slate-400">{investigation.entity_type}</td>
                    <td className="text-slate-400">{investigation.status}</td>
                    <td className="text-slate-400">
                      {investigation.automation_enabled ? 'enabled' : 'disabled'}
                    </td>
                    <td className="text-slate-500 text-xs">
                      {new Date(investigation.updated_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {creating && <CreateModal onClose={() => setCreating(false)} />}
    </div>
  )
}

function CreateModal({ onClose }: { onClose: () => void }) {
  const client = useQueryClient()
  const [form, setForm] = useState({
    title: '',
    entity_type: 'PERSON',
    description: '',
    legal_basis: '',
    default_depth: 1,
    automation_enabled: true,
  })

  const mutation = useMutation({
    mutationFn: () => api.post<Investigation>('/investigations', form),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['investigations'] })
      onClose()
    },
  })

  return (
    <Modal title="New case file" onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate()
        }}
      >
        {mutation.error != null && <ErrorBox error={mutation.error} />}

        <div>
          <label className="label">Title</label>
          <input
            className="input"
            required
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Entity type</label>
            <select
              className="input"
              value={form.entity_type}
              onChange={(event) => setForm({ ...form, entity_type: event.target.value })}
            >
              {ENTITY_TYPES.map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Default depth</label>
            <select
              className="input"
              value={form.default_depth}
              onChange={(event) =>
                setForm({ ...form, default_depth: Number(event.target.value) })
              }
            >
              {[1, 2, 3, 4].map((depth) => (
                <option key={depth} value={depth}>Level {depth}</option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className="label">Description</label>
          <textarea
            className="input h-20"
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
          />
        </div>

        <div>
          <label className="label">Legal basis / purpose</label>
          <textarea
            className="input h-16"
            placeholder="e.g. supplier due diligence, client mandate no..., search authorised by..."
            value={form.legal_basis}
            onChange={(event) => setForm({ ...form, legal_basis: event.target.value })}
          />
          <p className="text-[11px] text-slate-600 mt-1">
            Free text, but recommended: it documents why the data is collected.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={form.automation_enabled}
            onChange={(event) =>
              setForm({ ...form, automation_enabled: event.target.checked })
            }
          />
          Allow searches to chain automatically
        </label>

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={mutation.isPending}>
            {mutation.isPending ? 'Creating...' : 'Create'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
