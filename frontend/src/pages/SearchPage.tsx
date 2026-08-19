/** Dedicated search pages: username, email, phone, advanced. */
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import SearchRunner from '@/components/SearchRunner'
import { Card, ConfidenceBar, Empty, Loading, StatusBadge, Warning } from '@/components/ui'
import { api, query } from '@/lib/api'
import type { Finding, IdentifierType, Investigation, Person } from '@/types'

interface Preset {
  type: IdentifierType
  title: string
  hint: string
  placeholder: string
  warning?: string
}

const PRESETS: Record<string, Preset> = {
  username: {
    type: 'USERNAME',
    title: 'Search by username',
    hint: 'Checks whether the username exists on many platforms (Sherlock).',
    placeholder: 'jdupont',
    warning:
      'A username found proves an account exists, not that the person you are looking ' +
      'for owns it. Look for converging evidence before concluding.',
  },
  email: {
    type: 'EMAIL',
    title: 'Search by email',
    hint: 'Identifies services where this address is in use (Holehe) plus web search.',
    placeholder: 'prenom.nom@exemple.fr',
    warning:
      'Obfuscated values returned by services (recovery email or phone) are ' +
      'cross-reference hints, never certain data.',
  },
  phone: {
    type: 'PHONE',
    title: 'Search by phone number',
    hint: 'Validation, carrier, geographic area and public searches (PhoneInfoga).',
    placeholder: '+33 6 12 34 56 78',
  },
  advanced: {
    type: 'NAME',
    title: 'Advanced search',
    hint: 'Choose the target type and which plugins to run.',
    placeholder: 'Jean Dupont',
  },
}

const ADVANCED_TYPES: IdentifierType[] = [
  'NAME', 'USERNAME', 'EMAIL', 'PHONE', 'DOMAIN', 'COMPANY', 'ORGANIZATION',
]

export default function SearchPage() {
  const { kind = 'username' } = useParams()
  const preset = PRESETS[kind] ?? PRESETS.username

  const [value, setValue] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [type, setType] = useState<IdentifierType>(preset.type)
  const [personId, setPersonId] = useState('')
  const [investigationId, setInvestigationId] = useState('')

  const investigations = useQuery({
    queryKey: ['investigations', 'picker'],
    queryFn: () => api.get<Investigation[]>('/investigations'),
  })
  const persons = useQuery({
    queryKey: ['investigation-persons', investigationId],
    queryFn: () => api.get<Person[]>(`/investigations/${investigationId}/persons`),
    enabled: Boolean(investigationId),
  })

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">{preset.title}</h1>
        <p className="text-sm text-slate-500">{preset.hint}</p>
      </header>

      {preset.warning && <Warning>{preset.warning}</Warning>}

      <Card title="Target">
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            setSubmitted(value.trim())
          }}
        >
          <div className="flex flex-wrap gap-3 items-end">
            {kind === 'advanced' && (
              <div className="w-48">
                <label className="label">Target type</label>
                <select
                  className="input"
                  value={type}
                  onChange={(event) => setType(event.target.value as IdentifierType)}
                >
                  {ADVANCED_TYPES.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
              </div>
            )}

            <div className="flex-1 min-w-[240px]">
              <label className="label">Value</label>
              <input
                className="input"
                required
                placeholder={preset.placeholder}
                value={value}
                onChange={(event) => setValue(event.target.value)}
              />
            </div>

            <button className="btn btn-primary" type="submit">Prepare</button>
          </div>

          <div className="flex flex-wrap gap-3">
            <div className="w-64">
              <label className="label">Attach to a case file (optional)</label>
              <select
                className="input"
                value={investigationId}
                onChange={(event) => {
                  setInvestigationId(event.target.value)
                  setPersonId('')
                }}
              >
                <option value="">"Quick searches" case file</option>
                {(investigations.data ?? []).map((investigation) => (
                  <option key={investigation.id} value={investigation.id}>
                    {investigation.title}
                  </option>
                ))}
              </select>
            </div>

            {investigationId && (
              <div className="w-64">
                <label className="label">Person (optional)</label>
                <select
                  className="input"
                  value={personId}
                  onChange={(event) => setPersonId(event.target.value)}
                >
                  <option value="">None — results not attached</option>
                  {(persons.data ?? []).map((person) => (
                    <option key={person.id} value={person.id}>{person.display_name}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <p className="text-[11px] text-slate-600">
            Attaching a search to a person enables correlation, scoring and automatic
            chaining. Without attachment, results are still stored with their sources in
            a technical case file.
          </p>
        </form>
      </Card>

      {submitted && (
        <>
          <SearchRunner
            targetType={kind === 'advanced' ? type : preset.type}
            targetValue={submitted}
            personId={personId || undefined}
            investigationId={personId ? undefined : investigationId || undefined}
          />
          <Results
            personId={personId || undefined}
            investigationId={investigationId || undefined}
          />
        </>
      )}
    </div>
  )
}

function Results({
  personId,
  investigationId,
}: {
  personId?: string
  investigationId?: string
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['search-findings', personId, investigationId],
    queryFn: () =>
      api.get<Finding[]>(
        `/findings${query({ person_id: personId, investigation_id: investigationId, limit: 100 })}`,
      ),
    refetchInterval: 5000,
  })

  const profiles = (data ?? []).filter((finding) =>
    ['social_profile', 'profile_metadata', 'account_exists'].includes(finding.type),
  )
  const queries = (data ?? []).filter((finding) => finding.type === 'search_query')
  const others = (data ?? []).filter(
    (finding) => !profiles.includes(finding) && !queries.includes(finding),
  )

  if (isLoading) return <Loading />

  return (
    <div className="space-y-4">
      <Card title={`Found (${profiles.length})`}>
        {profiles.length === 0 ? (
          <Empty message="No account detected yet." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Result</th>
                <th>Plugin</th>
                <th>Confidence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((finding) => {
                const url = (finding.content as { url?: string } | null)?.url
                return (
                  <tr key={finding.id}>
                    <td>
                      <span className="text-slate-200">{finding.title}</span>
                      {url && (
                        <a
                          className="block text-xs text-accent hover:underline"
                          href={url}
                          target="_blank"
                          rel="noreferrer noopener"
                        >
                          {url}
                        </a>
                      )}
                    </td>
                    <td className="text-slate-400">{finding.plugin}</td>
                    <td><ConfidenceBar value={finding.confidence} /></td>
                    <td><StatusBadge status={finding.status} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {profiles.length > 0 && personId && (
          <p className="text-xs text-slate-600 mt-3">
            Validate these results from the{' '}
            <Link className="text-accent hover:underline" to={`/persons/${personId}?tab=profiles`}>
              page de la personne
            </Link>
            .
          </p>
        )}
      </Card>

      {queries.length > 0 && (
        <Card title={`Suggested searches (${queries.length})`}>
          <p className="text-xs text-slate-500 mb-2">
            These queries were not executed: open the relevant ones yourself.
          </p>
          <ul className="space-y-1">
            {queries.slice(0, 30).map((finding) => {
              const content = finding.content as { url?: string; query?: string } | null
              return (
                <li key={finding.id} className="text-sm">
                  <a
                    className="text-accent hover:underline"
                    href={content?.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {content?.query ?? finding.title}
                  </a>
                </li>
              )
            })}
          </ul>
        </Card>
      )}

      {others.length > 0 && (
        <Card title={`Other items (${others.length})`}>
          <ul className="space-y-1 text-sm">
            {others.map((finding) => (
              <li key={finding.id} className="flex items-center justify-between gap-3">
                <span className="text-slate-300">{finding.title}</span>
                <span className="flex items-center gap-2">
                  <span className="text-xs text-slate-600">{finding.plugin}</span>
                  <StatusBadge status={finding.status} />
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
