export type UsageSummary = {
  provider: string
  model: string
  requests: number
  prompt_tokens: number | null
  completion_tokens: number | null
  reasoning_tokens: number | null
  cached_tokens: number | null
  cache_creation_tokens: number | null
  total_tokens: number | null
  avg_latency_ms: number | null
  latency_sum_ms: number | null
  avg_throughput: number | null
  input_cost_usd: number | null
  output_cost_usd: number | null
  total_cost_usd: number | null
  avg_effective_price_usd: number | null
  avg_effective_price_per_million_usd: number | null
  successful_requests: number
  failed_requests: number
  status_429: number | null
  status_4xx: number | null
  status_5xx: number | null
  status_unknown: number | null
}

export type PricingDetail = {
  source: string | null
  input: number
  output: number
  cache_read: number
  cache_write: number | null
  tier: { min_tokens: number; max_tokens: number | null } | null
  snapshot_id: number | null
  estimated: boolean
}

export type UsageRow = {
  id: string
  ts: number
  provider: string
  model: string
  client_source: string | null
  session_id: string | null
  endpoint: string
  prompt_tokens: number | null
  prompt_length: number | null
  completion_tokens: number | null
  reasoning_tokens: number | null
  cached_tokens: number | null
  cache_creation_tokens: number | null
  total_tokens: number | null
  latency_ms: number | null
  ttft_ms: number | null
  tool_tokens: number | null
  tool_names: string | null
  normal_input_cost_usd: number | null
  cache_read_cost_usd: number | null
  cache_write_cost_usd: number | null
  input_cost_usd: number | null
  output_cost_usd: number | null
  total_cost_usd: number | null
  status: number | null
  device_id: string | null
  device_name: string | null
  price_snapshot_id: number | null
  cost_estimated: boolean | null
  pricing: PricingDetail | null
}

export type DailyUsage = {
  period: string
  requests: number
  prompt_tokens: number | null
  completion_tokens: number | null
  cached_tokens: number | null
  cache_creation_tokens: number | null
  total_tokens: number | null
  input_cost_usd: number | null
  output_cost_usd: number | null
  total_cost_usd: number | null
  avg_latency_ms: number | null
  latency_sum_ms: number | null
  avg_throughput: number | null
  successful_requests: number | null
  failed_requests: number | null
  status_429: number | null
  status_4xx: number | null
  status_5xx: number | null
  status_unknown: number | null
}

export type ActiveFilter = {
  models: string[];
} | null
export type DateRangeOption = '24h' | '5h' | '7d' | '30d' | 'all'
/** The bucket the time axis is drawn in. */
export type PeriodGranularity = 'hour' | '15m' | 'day'

export type SessionOutcome = 'solved' | 'partial' | 'failed' | 'stuck' | 'no_op' | 'unknown'
export type SessionEvaluationSource = 'manual' | 'heuristic' | 'llm'
export type EvaluatorType = 'claude' | 'codex' | 'opencode'

export type SessionEvaluation = {
  session_id: string
  outcome: SessionOutcome
  source: SessionEvaluationSource
  confidence: number | null
  task_title: string | null
  task_title_zh: string | null
  summary: string | null
  evidence: string[]
  failure_reason: string | null
  evaluated_at: string | null
}

export type EvaluationJob = {
  job_id: string
  kind: string
  session_id: string
  client_source: string
  user_id: string | null
  device_id: string | null
  device_name?: string | null
  status: 'running' | 'succeeded' | 'failed'
  trigger: 'auto'
  evaluator_type: EvaluatorType
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export type SessionSummary = {
  session_id: string
  client_source: string
  model: string
  started: number
  ended: number
  duration_s: number
  request_count: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  cache_creation_tokens: number
  total_cost_usd: number
  avg_latency_ms: number
  latency_sum_ms: number
  avg_ttft_ms: number
  successful_requests: number
  failed_requests: number
  status_429: number | null
  status_4xx: number | null
  status_5xx: number | null
  status_unknown: number | null
  tool_calls_json: Record<string, number> | null
  evaluation: SessionEvaluation | null
}

export type SessionSelectorRow = {
  session_id: string
  client_source: string
  request_count: number
  started: number
}

export type SessionsSummary = {
  session_count: number
  avg_duration_s: number
  total_tokens: number
  total_cost_usd: number
  avg_latency_ms: number
}

export type ModelEffectivenessGroup = {
  key: string
  session_count: number
  evaluated_count: number
  solved_count: number
  partial_count: number
  failed_count: number
  stuck_count: number
  unknown_count: number
  no_op_count: number
  solve_rate: number | null
  total_cost_usd: number
  cost_per_solved: number | null
  avg_duration_s: number
}

export type ModelEffectivenessResponse = {
  groups: ModelEffectivenessGroup[]
}

export type DailyEffectivenessGroup = {
  model: string
  client_source: string
  session_count: number
  evaluated_count: number
  solved_count: number
  failed_count: number
  stuck_count: number
  total_cost_usd: number
  cost_per_solved: number | null
  solve_rate: number | null
}

export type DailyEffectivenessReport = {
  date: string
  summary: string
  session_count: number
  evaluated_count: number
  classified_count: number
  solved_count: number
  partial_count: number
  failed_count: number
  stuck_count: number
  no_op_count: number
  unknown_count: number
  total_cost_usd: number
  highlights: string[]
  needs_attention: string[]
  model_takeaways: string[]
  groups: DailyEffectivenessGroup[]
}

// The client and the server release separately, so `status` is an open string:
// a client may report a status this server has never heard of.
export type SetupAgentHealth = {
  configured: boolean
  endpoint_matches: boolean | null
  configured_endpoint: string | null
  expected_endpoint: string | null
  status: 'ready' | 'missing_config' | 'wrong_endpoint' | 'configured' | (string & {})
}

export type SetupDiagnostics = {
  agents: Record<string, SetupAgentHealth>
}

export type OnboardingCopiedCommand = {
  source: string
  command: string
}

export type PricingSource = 'user' | 'server' | 'litellm' | 'openrouter'

export type PricingTier = {
  min_tokens: number
  max_tokens: number | null
  input: number
  output: number
  cache_read: number
  cache_write: number | null
}

export type PricingTimeRate = {
  days: number[] | null
  start_minute: number
  end_minute: number
  input: number
  output: number
  cache_read: number
  cache_write: number | null
}

export type PricingEntry = {
  input: number
  output: number
  cache_read: number
  cache_write: number | null
  source: PricingSource
  /** The stored override that priced this row (Custom rows only). */
  override?: string | null
  tiers?: PricingTier[]
  time_rates?: PricingTimeRate[]
}

export type AuthUser = {
  id: string
  email: string
  name: string | null
  created_at: number
}

export type DeviceRow = {
  id: string
  kind: string
  device_name: string | null
  created_at: number
  last_used_at: number | null
  client_version: string | null
  client_commit: string | null
  current: boolean
  revoked_at?: number | null
}

export type DeviceStatus = {
  device_id: string | null
  device_name: string | null
  client_version: string | null
  client_commit: string | null
  reported_at: number
  status: Record<string, any> | null
}
