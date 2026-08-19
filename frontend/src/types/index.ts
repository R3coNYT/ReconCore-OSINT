/** Types shared with the API (mirrors the Pydantic schemas). */

export type Role = 'ADMIN' | 'ANALYST' | 'READ_ONLY'

export type VerificationStatus =
  | 'UNKNOWN' | 'HYPOTHESIS' | 'PROBABLE' | 'CONFIRMED' | 'REJECTED'

export type FindingStatus =
  | 'NEW' | 'CONFIRMED' | 'PROBABLE' | 'UNVERIFIED' | 'REJECTED' | 'OUTDATED' | 'CONTRADICTED'

export type IdentifierType =
  | 'NAME' | 'FIRST_NAME' | 'LAST_NAME' | 'ALIAS' | 'USERNAME' | 'EMAIL' | 'PHONE'
  | 'ADDRESS' | 'CITY' | 'DEPARTMENT' | 'REGION' | 'COUNTRY' | 'DOMAIN' | 'WEBSITE'
  | 'SOCIAL_PROFILE' | 'COMPANY' | 'ORGANIZATION' | 'PUBLIC_ID' | 'DATE_OF_BIRTH'
  | 'PROFESSION' | 'NOTE'

export interface User {
  id: string
  email: string
  full_name: string | null
  role: Role
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_at: string
}

export interface ScoreContribution {
  code: string
  label: string
  points: number
  detail: string | null
}

export interface Score {
  score: number
  ratio: number
  verdict: string
  breakdown: ScoreContribution[]
  disclaimer: string
}

export interface Investigation {
  id: string
  title: string
  entity_type: string
  description: string | null
  legal_basis: string | null
  status: string
  automation_enabled: boolean
  default_depth: number
  last_activity_at: string | null
  created_at: string
  updated_at: string
  stats?: InvestigationStats
}

export interface InvestigationStats {
  persons: number
  identifiers: number
  usernames: number
  social_profiles: number
  findings: number
  sources: number
  relationships: number
  searches: number
  open_contradictions: number
  last_search_at: string | null
}

export interface Tag { id: string; name: string; slug: string; color: string | null }

export interface Person {
  id: string
  investigation_id: string
  display_name: string
  first_name: string | null
  last_name: string | null
  full_name: string | null
  date_of_birth: string | null
  profession: string | null
  summary: string | null
  confidence_score: number
  is_archived: boolean
  last_search_at: string | null
  created_at: string
  updated_at: string
  tags: Tag[]
  counters?: PersonCounters
  score?: Score
}

export interface PersonCounters {
  identifiers: number
  usernames: number
  social_profiles: number
  findings: number
  sources: number
  relationships: number
  searches: number
  open_contradictions: number
  new_findings: number
}

export interface Identifier {
  id: string
  person_id: string | null
  type: IdentifierType
  value: string
  normalized_value: string
  platform_id: string | null
  confidence: number
  status: VerificationStatus
  is_former: boolean
  source_id: string | null
  note: string | null
  created_at: string
}

export interface Username {
  id: string
  person_id: string
  value: string
  normalized_value: string
  platform_id: string | null
  url: string | null
  status: VerificationStatus
  confidence: number
  is_variant: boolean
  variant_rule: string | null
  discovered_at: string | null
  note: string | null
}

export interface SocialProfile {
  id: string
  person_id: string
  platform_id: string | null
  username: string
  url: string | null
  status: VerificationStatus
  confidence: number
  display_name: string | null
  bio: string | null
  avatar_url: string | null
  external_url: string | null
  location: string | null
  public_email: string | null
  public_phone: string | null
  followers: number | null
  following: number | null
  posts_count: number | null
  is_verified: boolean | null
  is_private: boolean | null
  is_business: boolean | null
  platform_user_id: string | null
  discovered_by_plugin: string | null
  last_checked_at: string | null
  score?: Score
}

export interface Finding {
  id: string
  person_id: string | null
  type: string
  title: string
  content: Record<string, unknown> | null
  status: FindingStatus
  confidence: number
  plugin: string | null
  source_id: string | null
  discovered_at: string | null
  verified_at: string | null
  score_explanation: Score | null
}

export interface Source {
  id: string
  kind: string
  url: string | null
  title: string | null
  description: string | null
  plugin: string | null
  raw_reference: string | null
  reliability: number
  date_discovered: string | null
  date_checked: string | null
}

export interface Contradiction {
  id: string
  person_id: string | null
  field: string
  value_a: string
  value_b: string
  resolved: boolean
  resolution: string | null
  resolved_value: string | null
  created_at: string
}

export interface TimelineEvent {
  id: string
  at: string
  kind: string
  message: string
  actor: string | null
  payload: Record<string, unknown> | null
}

export interface PluginRun {
  id: string
  search_id: string | null
  plugin: string
  plugin_version: string | null
  target_type: string
  target_value: string
  depth: number
  status: string
  progress: number
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  items_found: number
  error: string | null
  logs: string[] | null
}

export interface Search {
  id: string
  investigation_id: string | null
  person_id: string | null
  label: string | null
  target_type: string
  target_value: string
  depth: number
  differential: boolean
  status: string
  started_at: string | null
  finished_at: string | null
  stats: Record<string, unknown> | null
  created_at: string
  runs?: PluginRun[]
}

export interface SearchProgress {
  search_id: string
  status: string
  total_runs: number
  finished_runs: number
  progress: number
  runs: PluginRun[]
}

export interface Plugin {
  name: string
  version: string
  description: string | null
  repository: string | null
  license: string | null
  enabled: boolean
  supported_identifiers: string[]
  requires_secrets: string[]
  secrets_configured: Record<string, boolean>
  risk_level: string
  risk_notes: string[]
  last_audit_at: string | null
  health_status: string | null
  health_message: string | null
  health_checked_at: string | null
  limits: {
    requests_per_minute: number
    concurrency: number
    timeout_seconds: number
    retry_count: number
  }
  queue: string
}

export interface PluginAudit {
  plugin: string
  repository: string | null
  license: string | null
  version: string | null
  last_upstream_update: string | null
  last_reviewed: string | null
  risk_level: string
  network_access: string
  filesystem_access: string
  subprocess: string
  dynamic_downloads: string
  privileged_operations: string
  docker_socket: string
  hardcoded_secrets: string
  suspicious_behavior: string
  files_scanned: number
  dependencies: string[]
  signals: Array<{ code: string; severity: string; file: string; line: number; explanation: string }>
  errors: string[]
  generated_at: string
  disclaimer: string
}

export interface Platform {
  id: string
  name: string
  slug: string
  category: string
  base_url: string | null
  profile_url_template: string | null
  icon: string | null
  enabled: boolean
}

export interface GraphData {
  nodes: Array<{
    id: string; type: string; label: string; ref: string
    confidence?: number; status?: string; subtype?: string; url?: string
    is_variant?: boolean; category?: string
  }>
  edges: Array<{
    id: string; source: string; target: string; type: string
    confidence: number; status?: string; note?: string | null
  }>
  stats: { nodes: number; edges: number; by_type: Record<string, number> }
}

export interface VariantSuggestion {
  value: string
  rule: string
  confidence: number
  status: string
}

export interface DuplicateCandidate extends Score {
  person_id: string
  display_name: string
}

export interface Dashboard {
  counts: Record<string, number>
  plugin_activity: Array<{ plugin: string; runs: number; items: number }>
  recent_searches: Array<{ id: string; label: string | null; status: string; created_at: string }>
  recent_findings: Array<{
    id: string; title: string; type: string; plugin: string | null
    confidence: number; person_id: string | null
  }>
}
