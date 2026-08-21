import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import GraphView from '@/components/GraphView'
import { Card, Empty, ErrorBox, Loading, Modal, Stat } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { api } from '@/lib/api'
import type { Investigation, Person } from '@/types'

export default function InvestigationDetail() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)

  const investigation = useQuery({
    queryKey: ['investigation', id],
    queryFn: () => api.get<Investigation>(`/investigations/${id}`),
  })
  const persons = useQuery({
    queryKey: ['investigation-persons', id],
    queryFn: () => api.get<Person[]>(`/investigations/${id}/persons`),
  })

  // Deletion is permanent server-side, so the button asks for the exact title.
  const remove = useMutation({
    mutationFn: () => api.delete(`/investigations/${id}?confirm=true`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['investigations'] })
      navigate('/investigations')
    },
  })

  if (investigation.isLoading) return <Loading />
  if (investigation.error) return <ErrorBox error={investigation.error} />
  if (!investigation.data) return null

  const data = investigation.data
  const stats = data.stats

  return (
    <div className="space-y-5">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">{data.title}</h1>
          <p className="text-sm text-slate-500">
            {data.entity_type} · {data.status} · automation{' '}
            {data.automation_enabled ? 'enabled' : 'disabled'}
          </p>
          {data.legal_basis && (
            <p className="text-xs text-slate-500 mt-1">Basis: {data.legal_basis}</p>
          )}
        </div>
        {can('ANALYST') && (
          <div className="flex gap-2">
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              + Add a person
            </button>
            <button
              className="btn btn-danger"
              disabled={remove.isPending}
              onClick={() => {
                const expected = data.title
                const typed = window.prompt(
                  `Permanently delete this case file and everything in it?

` +
                    `This cannot be undone. Type the title to confirm:
${expected}`,
                )
                if (typed === expected) remove.mutate()
                else if (typed !== null) window.alert('The title did not match: nothing was deleted.')
              }}
            >
              {remove.isPending ? 'Deleting...' : 'Delete case file'}
            </button>
          </div>
        )}
      </header>

      {remove.error != null && <ErrorBox error={remove.error} />}

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
          <Stat label="People" value={stats.persons} />
          <Stat label="Identifiers" value={stats.identifiers} />
          <Stat label="Usernames" value={stats.usernames} />
          <Stat label="Profiles" value={stats.social_profiles} />
          <Stat label="Findings" value={stats.findings} />
          <Stat label="Sources" value={stats.sources} />
          <Stat label="Relations" value={stats.relationships} />
          <Stat label="Contradictions" value={stats.open_contradictions} />
        </div>
      )}

      <Card title="People in this case file">
        {persons.isLoading && <Loading />}
        {persons.data?.length === 0 && (
          <Empty message="No person yet. Add one to start collecting." />
        )}
        {persons.data && persons.data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Case score</th>
                <th>Last search</th>
              </tr>
            </thead>
            <tbody>
              {persons.data.map((person) => (
                <tr key={person.id}>
                  <td>
                    <Link className="text-accent hover:underline" to={`/persons/${person.id}`}>
                      {person.display_name}
                    </Link>
                  </td>
                  <td className="font-mono text-xs">
                    {Math.round(person.confidence_score * 100)}%
                  </td>
                  <td className="text-slate-500 text-xs">
                    {person.last_search_at
                      ? new Date(person.last_search_at).toLocaleString()
                      : 'never'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Case file graph">
        <GraphView endpoint={`/investigations/${id}/graph`} height={480} />
      </Card>

      {creating && <PersonModal investigationId={id} onClose={() => setCreating(false)} />}
    </div>
  )
}

function PersonModal({
  investigationId,
  onClose,
}: {
  investigationId: string
  onClose: () => void
}) {
  const client = useQueryClient()
  const [form, setForm] = useState({
    display_name: '',
    first_name: '',
    last_name: '',
    profession: '',
    summary: '',
  })

  const mutation = useMutation({
    mutationFn: () =>
      api.post<Person>(
        `/investigations/${investigationId}/persons`,
        Object.fromEntries(Object.entries(form).filter(([, value]) => value !== '')),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['investigation-persons', investigationId] })
      client.invalidateQueries({ queryKey: ['investigation', investigationId] })
      onClose()
    },
  })

  return (
    <Modal title="New person" onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate()
        }}
      >
        {mutation.error != null && <ErrorBox error={mutation.error} />}
        <div>
          <label className="label">Display name</label>
          <input
            className="input"
            required
            value={form.display_name}
            onChange={(event) => setForm({ ...form, display_name: event.target.value })}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">First name</label>
            <input
              className="input"
              value={form.first_name}
              onChange={(event) => setForm({ ...form, first_name: event.target.value })}
            />
          </div>
          <div>
            <label className="label">Last name</label>
            <input
              className="input"
              value={form.last_name}
              onChange={(event) => setForm({ ...form, last_name: event.target.value })}
            />
          </div>
        </div>
        <div>
          <label className="label">Public occupation</label>
          <input
            className="input"
            value={form.profession}
            onChange={(event) => setForm({ ...form, profession: event.target.value })}
          />
        </div>
        <div>
          <label className="label">Context</label>
          <textarea
            className="input h-20"
            value={form.summary}
            onChange={(event) => setForm({ ...form, summary: event.target.value })}
          />
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={mutation.isPending}>Create</button>
        </div>
      </form>
    </Modal>
  )
}
