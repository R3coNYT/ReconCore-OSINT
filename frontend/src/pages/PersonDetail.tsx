/** Person page: the core of the analyst's work (tabs plus human validation). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import GraphView from '@/components/GraphView'
import SearchRunner from '@/components/SearchRunner'
import {
  Card,
  ConfidenceBar,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  ScorePanel,
  STATUS_OPTIONS,
  Stat,
  StatusBadge,
  Warning,
} from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import { api, query } from '@/lib/api'
import type {
  Contradiction,
  DuplicateCandidate,
  Finding,
  IdentifierType,
  Identifier,
  Person,
  Platform,
  Search,
  SocialProfile,
  Source,
  TimelineEvent,
  Username,
  VariantSuggestion,
} from '@/types'

const TABS = [
  ['overview', 'Overview'],
  ['identifiers', 'Identifiers'],
  ['usernames', 'Usernames'],
  ['profiles', 'Social profiles'],
  ['findings', 'Findings'],
  ['sources', 'Sources'],
  ['graph', 'Graph'],
  ['timeline', 'Timeline'],
  ['searches', 'History'],
  ['duplicates', 'Duplicates'],
] as const

/**
 * One decision now updates the finding, the profile, the username and the
 * identifier that describe the same account (the backend joins them on their
 * source). Refreshing only the tab the analyst clicked in would leave the
 * other three showing a stale status from the cache.
 */
function invalidateDecisionViews(client: ReturnType<typeof useQueryClient>, personId: string) {
  for (const key of ['person', 'identifiers', 'usernames', 'profiles', 'findings', 'timeline']) {
    client.invalidateQueries({ queryKey: [key, personId] })
  }
}

const IDENTIFIER_TYPES: IdentifierType[] = [
  'FIRST_NAME', 'LAST_NAME', 'NAME', 'ALIAS', 'USERNAME', 'EMAIL', 'PHONE',
  'ADDRESS', 'CITY', 'DEPARTMENT', 'REGION', 'COUNTRY', 'DOMAIN', 'WEBSITE',
  'COMPANY', 'ORGANIZATION', 'PROFESSION', 'DATE_OF_BIRTH', 'PUBLIC_ID',
]

export default function PersonDetail() {
  const { id = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'overview'
  const [adding, setAdding] = useState(false)
  const { can } = useAuth()

  const person = useQuery({
    queryKey: ['person', id],
    queryFn: () => api.get<Person>(`/persons/${id}`),
  })

  if (person.isLoading) return <Loading />
  if (person.error) return <ErrorBox error={person.error} />
  if (!person.data) return null

  const data = person.data
  const counters = data.counters

  return (
    <div className="space-y-5">
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <Link
            to={`/investigations/${data.investigation_id}`}
            className="text-xs text-slate-500 hover:text-accent"
          >
            ← Back to case file
          </Link>
          <h1 className="text-xl font-semibold text-slate-100">{data.display_name}</h1>
          <p className="text-sm text-slate-500">
            Case score {Math.round(data.confidence_score * 100)}% ·{' '}
            {data.last_search_at
              ? `last search ${new Date(data.last_search_at).toLocaleString()}`
              : 'no search yet'}
          </p>
          <div className="flex gap-1 mt-1">
            {data.tags.map((tag) => (
              <span key={tag.id} className="text-[11px] px-2 py-0.5 rounded bg-base-700 text-slate-300">
                {tag.name}
              </span>
            ))}
          </div>
        </div>
        <div className="flex gap-2">
          {can('ANALYST') && (
            <button className="btn btn-primary" onClick={() => setAdding(true)}>
              + Add information
            </button>
          )}
          <ExportMenu personId={id} name={data.display_name} />
        </div>
      </header>

      {counters && (
        <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
          <Stat label="Identifiants" value={counters.identifiers} />
          <Stat label="Pseudos" value={counters.usernames} />
          <Stat label="Profils" value={counters.social_profiles} />
          <Stat label="Findings" value={counters.findings} hint={`${counters.new_findings} to triage`} />
          <Stat label="Sources" value={counters.sources} />
          <Stat label="Contradictions" value={counters.open_contradictions} />
        </div>
      )}

      <nav className="flex gap-1 border-b border-line overflow-x-auto">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            className={`tab ${tab === key ? 'tab-active' : ''}`}
            onClick={() => setParams({ tab: key })}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === 'overview' && <Overview person={data} />}
      {tab === 'identifiers' && <Identifiers personId={id} />}
      {tab === 'usernames' && <Usernames personId={id} />}
      {tab === 'profiles' && <Profiles personId={id} />}
      {tab === 'findings' && <Findings personId={id} />}
      {tab === 'sources' && <Sources investigationId={data.investigation_id} />}
      {tab === 'graph' && (
        <Card title="Identity graph">
          <GraphView endpoint={`/persons/${id}/graph`} />
        </Card>
      )}
      {tab === 'timeline' && <Timeline personId={id} />}
      {tab === 'searches' && <SearchHistory personId={id} />}
      {tab === 'duplicates' && <Duplicates personId={id} />}

      {adding && <AddIdentifierModal personId={id} onClose={() => setAdding(false)} />}
    </div>
  )
}

/* --------------------------------------------------------------- overview */

function Overview({ person }: { person: Person }) {
  const contradictions = useQuery({
    queryKey: ['contradictions', person.id],
    queryFn: () =>
      api.get<Contradiction[]>(`/contradictions${query({ person_id: person.id, resolved: false })}`),
  })
  const identifiers = useQuery({
    queryKey: ['identifiers', person.id],
    queryFn: () => api.get<Identifier[]>(`/persons/${person.id}/identifiers`),
  })
  const profiles = useQuery({
    queryKey: ['profiles', person.id],
    queryFn: () => api.get<SocialProfile[]>(`/persons/${person.id}/social-profiles`),
  })

  const grouped = (identifiers.data ?? []).reduce<Record<string, Identifier[]>>((acc, item) => {
    ;(acc[item.type] ??= []).push(item)
    return acc
  }, {})

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 space-y-4">
        {contradictions.data && contradictions.data.length > 0 && (
          <Card title="Alerts">
            {contradictions.data.map((item) => (
              <ContradictionRow key={item.id} contradiction={item} />
            ))}
          </Card>
        )}

        <Card title="Identity">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <Field label="First name" value={person.first_name} />
            <Field label="Last name" value={person.last_name} />
            <Field label="Full name" value={person.full_name} />
            <Field label="Date of birth" value={person.date_of_birth} />
            <Field label="Profession" value={person.profession} />
          </dl>
          {person.summary && <p className="mt-3 text-sm text-slate-400">{person.summary}</p>}
        </Card>

        <Card title="Collected information">
          {Object.keys(grouped).length === 0 ? (
            <Empty message="No identifiers yet." />
          ) : (
            <div className="space-y-3">
              {Object.entries(grouped).map(([type, items]) => (
                <div key={type}>
                  <p className="text-xs uppercase tracking-wide text-slate-500 mb-1">{type}</p>
                  <div className="flex flex-wrap gap-2">
                    {items.map((item) => (
                      <span
                        key={item.id}
                        className="px-2 py-1 rounded bg-base-700 border border-line text-sm flex items-center gap-2"
                      >
                        <span className={item.is_former ? 'line-through text-slate-500' : ''}>
                          {item.value}
                        </span>
                        <StatusBadge status={item.status} />
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Social networks">
          {profiles.data?.length === 0 ? (
            <Empty message="No profile identified." />
          ) : (
            <div className="space-y-2">
              {(profiles.data ?? []).map((profile) => (
                <div key={profile.id} className="flex items-center justify-between gap-3 text-sm">
                  <span>
                    <span className="text-slate-200">{profile.username}</span>
                    {profile.url && (
                      <a
                        className="ml-2 text-xs text-accent hover:underline"
                        href={profile.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {profile.url}
                      </a>
                    )}
                  </span>
                  <span className="flex items-center gap-3">
                    <ConfidenceBar value={profile.confidence} />
                    <StatusBadge status={profile.status} />
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="space-y-4">
        {person.score && (
          <Card title="Consolidation score">
            <ScorePanel score={person.score} />
          </Card>
        )}
        <Card title="Run another search">
          <p className="text-sm text-slate-500 mb-3">
            Pick a value from the case file as a starting point in the matching tab,
            or use the dedicated search pages.
          </p>
          <div className="flex flex-col gap-2">
            <Link className="btn justify-center" to="/search/username">Search by username</Link>
            <Link className="btn justify-center" to="/search/email">Search by email</Link>
            <Link className="btn justify-center" to="/search/phone">Search by phone</Link>
          </div>
        </Card>
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-slate-200">{value || '—'}</dd>
    </>
  )
}

function ContradictionRow({ contradiction }: { contradiction: Contradiction }) {
  const client = useQueryClient()
  const [choice, setChoice] = useState('')
  const resolve = useMutation({
    mutationFn: () =>
      api.post(`/contradictions/${contradiction.id}/resolve`, {
        resolved_value: choice,
        resolution: 'Tranche manuellement par l analyste',
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['contradictions'] }),
  })

  return (
    <div className="border border-warn/40 bg-warn/5 rounded-md p-3 mb-2">
      <p className="text-warn text-sm">⚠ Contradictory information — {contradiction.field}</p>
      <div className="flex flex-wrap gap-2 mt-2">
        {[contradiction.value_a, contradiction.value_b].map((value) => (
          <button
            key={value}
            className={`btn ${choice === value ? 'btn-primary' : ''}`}
            onClick={() => setChoice(value)}
          >
            {value}
          </button>
        ))}
        <button
          className="btn"
          disabled={!choice || resolve.isPending}
          onClick={() => resolve.mutate()}
        >
          Keep this value
        </button>
      </div>
      <p className="text-[11px] text-slate-500 mt-2">
        The system never picks a side: the analyst decides, and the decision is logged.
      </p>
    </div>
  )
}

/* ------------------------------------------------------------ identifiers */

function Identifiers({ personId }: { personId: string }) {
  const client = useQueryClient()
  const { can } = useAuth()
  const { data, isLoading, error } = useQuery({
    queryKey: ['identifiers', personId],
    queryFn: () => api.get<Identifier[]>(`/persons/${personId}/identifiers`),
  })

  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patch(`/persons/${personId}/identifiers/${id}`, { status }),
    onSuccess: () => invalidateDecisionViews(client, personId),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/persons/${personId}/identifiers/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['identifiers', personId] }),
  })

  if (isLoading) return <Loading />
  if (error) return <ErrorBox error={error} />

  return (
    <Card title={`Identifiers (${data?.length ?? 0})`}>
      {data?.length === 0 ? (
        <Empty message="No identifiers yet. Use the 'Add information' button." />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Value</th>
              <th>Normalise</th>
              <th>Confidence</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((item) => (
              <tr key={item.id}>
                <td className="text-slate-400 text-xs">{item.type}</td>
                <td className={item.is_former ? 'line-through text-slate-500' : 'text-slate-200'}>
                  {item.value}
                  {item.is_former && <span className="text-xs text-slate-600"> (ancien)</span>}
                </td>
                <td className="font-mono text-xs text-slate-500">{item.normalized_value}</td>
                <td><ConfidenceBar value={item.confidence} /></td>
                <td>
                  {can('ANALYST') ? (
                    <select
                      className="input py-1 text-xs"
                      value={item.status}
                      onChange={(event) =>
                        update.mutate({ id: item.id, status: event.target.value })
                      }
                    >
                      {STATUS_OPTIONS.map((status) => (
                        <option key={status} value={status}>{status}</option>
                      ))}
                    </select>
                  ) : (
                    <StatusBadge status={item.status} />
                  )}
                </td>
                <td className="text-right">
                  {can('ANALYST') && (
                    <button
                      className="btn btn-danger px-2 py-1"
                      onClick={() => remove.mutate(item.id)}
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------- usernames */

function Usernames({ personId }: { personId: string }) {
  const client = useQueryClient()
  const { can } = useAuth()
  const [showVariants, setShowVariants] = useState(false)
  const [runTarget, setRunTarget] = useState<string | null>(null)

  const usernames = useQuery({
    queryKey: ['usernames', personId],
    queryFn: () => api.get<Username[]>(`/persons/${personId}/usernames`),
  })
  const suggestions = useQuery({
    queryKey: ['variants', personId],
    queryFn: () =>
      api.get<{ suggestions: VariantSuggestion[]; warning: string }>(
        `/persons/${personId}/username-variants`,
      ),
    enabled: showVariants,
  })

  const save = useMutation({
    mutationFn: (values: string[]) =>
      api.post(`/persons/${personId}/username-variants`, { values }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['usernames', personId] })
      setShowVariants(false)
    },
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patch(`/persons/${personId}/usernames/${id}`, { status }),
    onSuccess: () => invalidateDecisionViews(client, personId),
  })

  const [selected, setSelected] = useState<Set<string>>(new Set())

  return (
    <div className="space-y-4">
      <Card
        title={`Usernames (${usernames.data?.length ?? 0})`}
        actions={
          can('ANALYST') && (
            <button className="btn" onClick={() => setShowVariants(true)}>
              Suggest variants
            </button>
          )
        }
      >
        {usernames.isLoading && <Loading />}
        {usernames.data?.length === 0 && <Empty message="No username stored." />}
        {usernames.data && usernames.data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Username</th>
                <th>Platform</th>
                <th>Origin</th>
                <th>Confidence</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {usernames.data.map((item) => (
                <tr key={item.id}>
                  <td className="text-slate-200 font-mono">{item.value}</td>
                  <td className="text-slate-400">
                    {item.url ? (
                      <a
                        className="text-accent hover:underline"
                        href={item.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {item.url}
                      </a>
                    ) : (
                      <span className="text-slate-600">platform unknown</span>
                    )}
                  </td>
                  <td className="text-xs text-slate-500">
                    {item.is_variant ? `hypothese (${item.variant_rule ?? 'variante'})` : 'observe'}
                  </td>
                  <td><ConfidenceBar value={item.confidence} /></td>
                  <td>
                    {can('ANALYST') ? (
                      <select
                        className="input py-1 text-xs"
                        value={item.status}
                        onChange={(event) =>
                          updateStatus.mutate({ id: item.id, status: event.target.value })
                        }
                      >
                        {STATUS_OPTIONS.map((status) => (
                          <option key={status} value={status}>{status}</option>
                        ))}
                      </select>
                    ) : (
                      <StatusBadge status={item.status} />
                    )}
                  </td>
                  <td className="text-right">
                    <button className="btn px-2 py-1" onClick={() => setRunTarget(item.value)}>
                      Search
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {runTarget && (
        <SearchRunner
          targetType="USERNAME"
          targetValue={runTarget}
          personId={personId}
          onFinished={() => client.invalidateQueries({ queryKey: ['person', personId] })}
        />
      )}

      {showVariants && (
        <Modal title="Suggested variants" onClose={() => setShowVariants(false)}>
          {suggestions.isLoading && <Loading />}
          {suggestions.data && (
            <div className="space-y-3">
              <Warning>{suggestions.data.warning}</Warning>
              <div className="max-h-80 overflow-y-auto space-y-1">
                {suggestions.data.suggestions.map((item) => (
                  <label key={item.value} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={selected.has(item.value)}
                      onChange={(event) => {
                        const next = new Set(selected)
                        event.target.checked ? next.add(item.value) : next.delete(item.value)
                        setSelected(next)
                      }}
                    />
                    <span className="font-mono text-slate-200">{item.value}</span>
                    <span className="text-xs text-slate-600">
                      {item.rule} · {Math.round(item.confidence * 100)}%
                    </span>
                  </label>
                ))}
              </div>
              <div className="flex justify-end gap-2">
                <button className="btn" onClick={() => setShowVariants(false)}>Cancel</button>
                <button
                  className="btn btn-primary"
                  disabled={selected.size === 0 || save.isPending}
                  onClick={() => save.mutate([...selected])}
                >
                  Save as hypotheses ({selected.size})
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}
    </div>
  )
}

/* --------------------------------------------------------------- profiles */

function Profiles({ personId }: { personId: string }) {
  const client = useQueryClient()
  const { can } = useAuth()
  const [detail, setDetail] = useState<string | null>(null)

  const profiles = useQuery({
    queryKey: ['profiles', personId],
    queryFn: () => api.get<SocialProfile[]>(`/persons/${personId}/social-profiles`),
  })
  const platforms = useQuery({
    queryKey: ['platforms'],
    queryFn: () => api.get<Platform[]>('/platforms'),
  })

  const decide = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.post(`/persons/${personId}/social-profiles/${id}/status`, { status }),
    onSuccess: () => invalidateDecisionViews(client, personId),
  })

  const platformName = (id: string | null) =>
    platforms.data?.find((platform) => platform.id === id)?.name ?? 'Inconnue'

  return (
    <div className="space-y-4">
      <Warning>
        The same username on several platforms does not prove it is the same person.
        Confirm only on the basis of converging evidence.
      </Warning>

      <Card title={`Social profiles (${profiles.data?.length ?? 0})`}>
        {profiles.isLoading && <Loading />}
        {profiles.data?.length === 0 && <Empty message="No profile discovered." />}
        {profiles.data && profiles.data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Platform</th>
                <th>Account</th>
                <th>Signals</th>
                <th>Score</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {profiles.data.map((profile) => (
                <tr key={profile.id}>
                  <td className="text-slate-300">{platformName(profile.platform_id)}</td>
                  <td>
                    <button
                      className="text-accent hover:underline font-mono"
                      onClick={() => setDetail(profile.id)}
                    >
                      {profile.username}
                    </button>
                    {profile.url && (
                      <a
                        className="block text-xs text-slate-600 hover:text-accent"
                        href={profile.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {profile.url}
                      </a>
                    )}
                  </td>
                  <td className="text-xs text-slate-500">
                    {[
                      profile.display_name && `nom: ${profile.display_name}`,
                      profile.location && `lieu: ${profile.location}`,
                      profile.public_email && 'email public',
                      profile.followers !== null && `${profile.followers} abonnes`,
                      profile.discovered_by_plugin && `via ${profile.discovered_by_plugin}`,
                    ]
                      .filter(Boolean)
                      .join(' · ') || '—'}
                  </td>
                  <td><ConfidenceBar value={profile.confidence} /></td>
                  <td>
                    {can('ANALYST') ? (
                      <div className="flex gap-1">
                        <button
                          className="btn px-2 py-1 text-ok border-ok/40"
                          onClick={() => decide.mutate({ id: profile.id, status: 'CONFIRMED' })}
                        >
                          ✓
                        </button>
                        <button
                          className="btn px-2 py-1"
                          onClick={() => decide.mutate({ id: profile.id, status: 'PROBABLE' })}
                        >
                          ?
                        </button>
                        <button
                          className="btn px-2 py-1 btn-danger"
                          onClick={() => decide.mutate({ id: profile.id, status: 'REJECTED' })}
                        >
                          ✕
                        </button>
                      </div>
                    ) : (
                      <StatusBadge status={profile.status} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {detail && (
        <ProfileDetail personId={personId} profileId={detail} onClose={() => setDetail(null)} />
      )}
    </div>
  )
}

function ProfileDetail({
  personId,
  profileId,
  onClose,
}: {
  personId: string
  profileId: string
  onClose: () => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['profile', profileId],
    queryFn: () => api.get<SocialProfile>(`/persons/${personId}/social-profiles/${profileId}`),
  })

  return (
    <Modal title="Profile details" onClose={onClose}>
      {isLoading && <Loading />}
      {data && (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
            <Field label="Username" value={data.username} />
            <Field label="Display name" value={data.display_name} />
            <Field label="Location" value={data.location} />
            <Field label="Public email" value={data.public_email} />
            <Field label="Public phone" value={data.public_phone} />
            <Field label="External link" value={data.external_url} />
            <Field label="Followers" value={data.followers?.toString() ?? null} />
            <Field label="Posts" value={data.posts_count?.toString() ?? null} />
            <Field label="Discovered by" value={data.discovered_by_plugin} />
          </dl>
          {data.bio && <p className="text-sm text-slate-400 border-t border-line pt-3">{data.bio}</p>}
          {data.score && (
            <div className="border-t border-line pt-3">
              <ScorePanel score={data.score} />
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

/* --------------------------------------------------------------- findings */

function Findings({ personId }: { personId: string }) {
  const client = useQueryClient()
  const { can } = useAuth()
  const [status, setStatus] = useState('')

  const findings = useQuery({
    queryKey: ['findings', personId, status],
    queryFn: () => api.get<Finding[]>(`/findings${query({ person_id: personId, status })}`),
  })

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: string }) =>
      api.post(`/findings/${id}/decision`, { decision }),
    onSuccess: () => invalidateDecisionViews(client, personId),
  })

  return (
    <Card
      title={`Findings (${findings.data?.length ?? 0})`}
      actions={
        <select
          className="input py-1 text-xs w-40"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="">All statuses</option>
          {['NEW', 'CONFIRMED', 'PROBABLE', 'UNVERIFIED', 'REJECTED', 'OUTDATED'].map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      }
    >
      {findings.isLoading && <Loading />}
      {findings.data?.length === 0 && <Empty message="No results." />}
      <div className="space-y-2">
        {(findings.data ?? []).map((finding) => (
          <div key={finding.id} className="border border-line rounded-md p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-slate-200 text-sm">{finding.title}</p>
                <p className="text-xs text-slate-600">
                  {finding.type} · {finding.plugin ?? 'manuel'} ·{' '}
                  {finding.discovered_at
                    ? new Date(finding.discovered_at).toLocaleString()
                    : '—'}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <ConfidenceBar value={finding.confidence} />
                <StatusBadge status={finding.status} />
              </div>
            </div>

            {finding.content && (
              <details className="mt-2">
                <summary className="text-xs text-slate-600 cursor-pointer">Donnees brutes</summary>
                <pre className="mt-1 p-2 bg-base-900 border border-line rounded text-xs overflow-x-auto text-slate-400">
                  {JSON.stringify(finding.content, null, 2)}
                </pre>
              </details>
            )}

            {finding.score_explanation && (
              <details className="mt-1">
                <summary className="text-xs text-slate-600 cursor-pointer">
                  Pourquoi ce score ?
                </summary>
                <div className="mt-2">
                  <ScorePanel score={finding.score_explanation} />
                </div>
              </details>
            )}

            {can('ANALYST') && (
              <div className="flex gap-2 mt-2">
                <button
                  className="btn px-2 py-1 text-ok border-ok/40"
                  onClick={() => decide.mutate({ id: finding.id, decision: 'confirm' })}
                >
                  ✓ Confirm
                </button>
                <button
                  className="btn px-2 py-1"
                  onClick={() => decide.mutate({ id: finding.id, decision: 'investigate' })}
                >
                  ? To verify
                </button>
                <button
                  className="btn px-2 py-1 btn-danger"
                  onClick={() => decide.mutate({ id: finding.id, decision: 'reject' })}
                >
                  ✕ Reject
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- sources */

function Sources({ investigationId }: { investigationId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['sources', investigationId],
    queryFn: () => api.get<Source[]>(`/sources${query({ investigation_id: investigationId })}`),
  })

  return (
    <Card title={`Sources (${data?.length ?? 0})`}>
      {isLoading && <Loading />}
      {data?.length === 0 && <Empty message="No source stored." />}
      {data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Title / URL</th>
              <th>Plugin</th>
              <th>Reliability</th>
              <th>Checked on</th>
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
                  {source.date_checked
                    ? new Date(source.date_checked).toLocaleString()
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

/* --------------------------------------------------------------- timeline */

function Timeline({ personId }: { personId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['timeline', personId],
    queryFn: () => api.get<TimelineEvent[]>(`/persons/${personId}/timeline`),
  })

  return (
    <Card title="Timeline">
      {isLoading && <Loading />}
      {data?.length === 0 && <Empty message="No events." />}
      <ol className="relative border-l border-line ml-2">
        {(data ?? []).map((event) => (
          <li key={event.id} className="ml-4 pb-4">
            <span className="absolute -left-1.5 w-3 h-3 rounded-full bg-accent border-2 border-base-800" />
            <p className="text-xs text-slate-500">
              {new Date(event.at).toLocaleString()}
              {event.actor && ` · ${event.actor}`}
            </p>
            <p className="text-sm text-slate-200">{event.message}</p>
            <p className="text-[11px] text-slate-600">{event.kind}</p>
          </li>
        ))}
      </ol>
    </Card>
  )
}

/* -------------------------------------------------------- search history */

function SearchHistory({ personId }: { personId: string }) {
  const client = useQueryClient()
  const { can } = useAuth()
  const { data, isLoading } = useQuery({
    queryKey: ['searches', personId],
    queryFn: () => api.get<Search[]>(`/searches${query({ person_id: personId })}`),
    refetchInterval: 10_000,
  })

  // Quick searches run without a person, so their results sit in a technical
  // case file until they are imported here.
  const orphans = useQuery({
    queryKey: ['searches', 'unattached'],
    queryFn: () => api.get<Search[]>('/searches?unattached=true'),
  })

  const importSearch = useMutation({
    mutationFn: (searchId: string) =>
      api.post(`/persons/${personId}/import-search`, { search_id: searchId }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['searches'] })
      client.invalidateQueries({ queryKey: ['person', personId] })
      client.invalidateQueries({ queryKey: ['findings', personId] })
      client.invalidateQueries({ queryKey: ['profiles', personId] })
    },
  })

  return (
    <div className="space-y-4">
      {orphans.data && orphans.data.length > 0 && can('ANALYST') && (
        <Card title={`Quick searches not attached to anyone (${orphans.data.length})`}>
          <p className="text-xs text-slate-500 mb-3">
            Importing moves the stored results onto this person and re-runs the
            correlation. Nothing is queried again.
          </p>
          {importSearch.error != null && <ErrorBox error={importSearch.error} />}
          <table className="table">
            <thead>
              <tr>
                <th>Target</th>
                <th>Status</th>
                <th>Date</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {orphans.data.map((search) => (
                <tr key={search.id}>
                  <td className="font-mono text-xs">
                    {search.target_type}={search.target_value}
                  </td>
                  <td><StatusBadge status={search.status} /></td>
                  <td className="text-xs text-slate-500">
                    {new Date(search.created_at).toLocaleString()}
                  </td>
                  <td className="text-right">
                    <button
                      className="btn btn-primary px-2 py-1"
                      disabled={importSearch.isPending}
                      onClick={() => importSearch.mutate(search.id)}
                    >
                      Import into this case file
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

    <Card title="Search history">
      {isLoading && <Loading />}
      {data?.length === 0 && <Empty message="No search launched for this person." />}
      {data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Target</th>
              <th>Depth</th>
              <th>Mode</th>
              <th>Status</th>
              <th>Results</th>
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

/* -------------------------------------------------------------- duplicates */

function Duplicates({ personId }: { personId: string }) {
  const client = useQueryClient()
  const { can } = useAuth()
  const { data, isLoading } = useQuery({
    queryKey: ['duplicates', personId],
    queryFn: () => api.get<DuplicateCandidate[]>(`/persons/${personId}/duplicates`),
  })

  const merge = useMutation({
    mutationFn: (sourceId: string) =>
      api.post(`/persons/${personId}/merge`, {
        source_person_id: sourceId,
        confirm: true,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['duplicates', personId] })
      client.invalidateQueries({ queryKey: ['person', personId] })
    },
  })

  return (
    <Card title="Possible duplicates">
      {isLoading && <Loading />}
      {data?.length === 0 && <Empty message="No duplicate detected in this case file." />}
      {merge.error != null && <ErrorBox error={merge.error} />}
      <div className="space-y-3">
        {(data ?? []).map((candidate) => (
          <div key={candidate.person_id} className="border border-line rounded-md p-3">
            <div className="flex items-center justify-between gap-3">
              <Link
                className="text-accent hover:underline"
                to={`/persons/${candidate.person_id}`}
              >
                {candidate.display_name}
              </Link>
              <span className="text-lg font-semibold text-slate-200">{candidate.score}%</span>
            </div>
            <div className="mt-2">
              <ScorePanel score={candidate} />
            </div>
            {can('ANALYST') && (
              <div className="flex gap-2 mt-2">
                <button
                  className="btn"
                  onClick={() => {
                    if (
                      window.confirm(
                        `Merge "${candidate.display_name}" into this case file? ` +
                          'This operation is irreversible.',
                      )
                    ) {
                      merge.mutate(candidate.person_id)
                    }
                  }}
                >
                  Merge
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------ add + export */

function AddIdentifierModal({ personId, onClose }: { personId: string; onClose: () => void }) {
  const client = useQueryClient()
  const [form, setForm] = useState({
    type: 'EMAIL' as IdentifierType,
    value: '',
    platform: '',
    confidence: 0.6,
    status: 'UNKNOWN',
    is_former: false,
    note: '',
    source_url: '',
  })
  const [created, setCreated] = useState<{ compatible_plugins: Array<{ name: string }> } | null>(
    null,
  )

  const mutation = useMutation({
    mutationFn: () =>
      api.post<{ compatible_plugins: Array<{ name: string }> }>(
        `/persons/${personId}/identifiers`,
        Object.fromEntries(
          Object.entries(form).filter(([, value]) => value !== '' && value !== false),
        ),
      ),
    onSuccess: (response) => {
      setCreated(response)
      client.invalidateQueries({ queryKey: ['identifiers', personId] })
      client.invalidateQueries({ queryKey: ['person', personId] })
    },
  })

  return (
    <Modal title="Add information" onClose={onClose}>
      {created ? (
        <div className="space-y-4">
          <p className="text-sm text-ok">New identifier added to the case file.</p>
          <p className="text-sm text-slate-400">
            Compatible plugins:{' '}
            {created.compatible_plugins.map((plugin) => plugin.name).join(', ') || 'none'}
          </p>
          <SearchRunner
            targetType={form.type}
            targetValue={form.value}
            personId={personId}
            onFinished={() => client.invalidateQueries({ queryKey: ['person', personId] })}
          />
          <div className="flex justify-end">
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            mutation.mutate()
          }}
        >
          {mutation.error != null && <ErrorBox error={mutation.error} />}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Type</label>
              <select
                className="input"
                value={form.type}
                onChange={(event) =>
                  setForm({ ...form, type: event.target.value as IdentifierType })
                }
              >
                {IDENTIFIER_TYPES.map((type) => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Platform (optional)</label>
              <input
                className="input"
                placeholder="Instagram, GitHub..."
                value={form.platform}
                onChange={(event) => setForm({ ...form, platform: event.target.value })}
              />
            </div>
          </div>

          <div>
            <label className="label">Value</label>
            <input
              className="input"
              required
              value={form.value}
              onChange={(event) => setForm({ ...form, value: event.target.value })}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Status</label>
              <select
                className="input"
                value={form.status}
                onChange={(event) => setForm({ ...form, status: event.target.value })}
              >
                {STATUS_OPTIONS.map((status) => (
                  <option key={status} value={status}>{status}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Confidence: {Math.round(form.confidence * 100)}%</label>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={form.confidence * 100}
                onChange={(event) =>
                  setForm({ ...form, confidence: Number(event.target.value) / 100 })
                }
                className="w-full"
              />
            </div>
          </div>

          <div>
            <label className="label">Source (URL) — recommended</label>
            <input
              className="input"
              placeholder="https://..."
              value={form.source_url}
              onChange={(event) => setForm({ ...form, source_url: event.target.value })}
            />
          </div>

          <div>
            <label className="label">Note</label>
            <textarea
              className="input h-16"
              value={form.note}
              onChange={(event) => setForm({ ...form, note: event.target.value })}
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={form.is_former}
              onChange={(event) => setForm({ ...form, is_former: event.target.checked })}
            />
            Former / historical contact detail
          </label>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={mutation.isPending}>Add</button>
          </div>
        </form>
      )}
    </Modal>
  )
}

function ExportMenu({ personId, name }: { personId: string; name: string }) {
  const [busy, setBusy] = useState<string | null>(null)

  async function download(format: 'json' | 'csv' | 'pdf') {
    setBusy(format)
    try {
      await api.download(`/persons/${personId}/export?format=${format}`, `${name}.${format}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex gap-1">
      {(['json', 'csv', 'pdf'] as const).map((format) => (
        <button
          key={format}
          className="btn"
          disabled={busy !== null}
          onClick={() => download(format)}
        >
          {busy === format ? '...' : format.toUpperCase()}
        </button>
      ))}
    </div>
  )
}
