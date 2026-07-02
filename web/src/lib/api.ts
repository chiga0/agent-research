export type RunStatus =
  "created" | "queued" | "running" | "completed" | "failed" | "cancelled";

const API_BASE = getApiBase();

export interface RunSpec {
  prompt?: string | null;
  adapter: string;
  repo?: string | null;
  workspace?: string | null;
  model?: string | null;
  sandbox?: Record<string, unknown>;
  timeout_seconds?: number | null;
  metadata?: Record<string, unknown>;
}

export interface RunState {
  run_id: string;
  status: RunStatus;
  adapter_run_id?: string | null;
  created_at: string;
  updated_at: string;
  event_count: number;
  prompt_count: number;
  spec: RunSpec;
}

export interface RuntimeEvent {
  id: string;
  run_id: string;
  sequence: number;
  type: string;
  created_at: string;
  data: Record<string, unknown>;
}

export interface ArtifactInfo {
  name: string;
  size_bytes: number;
  updated_at: string;
}

export interface PermissionRequest {
  permission_id: string;
  prompt?: string;
  options?: Array<{ id: string; label?: string; description?: string }>;
  tool?: string;
  raw?: Record<string, unknown>;
}

export interface WorkerInfo {
  worker_id: string;
  status: string;
  capacity: number;
  active_count: number;
  heartbeat_at: string;
  lease_ttl_seconds: number;
  metadata?: Record<string, unknown>;
}

export interface QueueStatus {
  counts: Record<string, number>;
  jobs: Array<Record<string, unknown>>;
  workers: WorkerInfo[];
}

export interface WorkerControl {
  worker_id: string;
  draining: boolean;
  desired_state: string;
  runs: Array<Record<string, unknown>>;
  generated_at: string;
}

export interface WorkerRegistration {
  worker_id: string;
  capacity: number;
  control_url: string;
  token: ApiToken;
  metadata: Record<string, unknown>;
  deploy_command: string;
}

export interface ExecutorLease {
  executor_id: string;
  run_id: string;
  adapter: string;
  strategy: string;
  status: string;
  base_url?: string | null;
  workspace?: string | null;
  port?: number | null;
  pid?: number | null;
  started_at: string;
  heartbeat_at?: string | null;
  released_at?: string | null;
  exit_code?: number | null;
  last_error?: string | null;
  metadata: Record<string, unknown>;
}

export interface CostStatus {
  generated_at: string;
  status: string;
  config: Record<string, unknown>;
  month: string;
  monthly_estimated_cost_usd: number;
  monthly_budget_usd: number;
  warning_threshold_usd?: number | null;
  runs: Array<Record<string, unknown>>;
}

export interface DrillCheck {
  id: string;
  status: "pass" | "warn" | "fail" | string;
  summary: string;
  details: Record<string, unknown>;
}

export interface Capabilities {
  mode: string;
  features: string[];
  adapters: Record<
    string,
    { name: string; status?: string; features?: string[] }
  >;
  queue: QueueStatus;
  executor_registry?: Record<string, unknown>;
  profiles: AgentProfile[];
  permission_stall_policy?: { seconds: number; action: string };
  cleanup_policy?: Record<string, unknown>;
  ops_policy?: Record<string, unknown>;
}

export interface Metrics {
  generated_at: string;
  runs: { total: number; by_status: Record<string, number> };
  missions: { total: number; by_status: Record<string, number> };
  queue: {
    counts: Record<string, number>;
    worker_count: number;
    active_workers: number;
    stale_workers: number;
  };
  permissions: { pending: number; stalled: number };
  latency_seconds: { count: number; avg: number | null; p95: number | null };
}

export interface MissionTask {
  task_id: string;
  title: string;
  profile_id: string;
  status: string;
  run_id?: string | null;
  depends_on: string[];
  result?: Record<string, unknown>;
  profile_snapshot?: Record<string, unknown>;
}

export interface MissionState {
  mission_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  event_count: number;
  task_count: number;
  completed_task_count: number;
  failed_task_count: number;
  spec: { goal: string; strategy: string; adapter: string };
  tasks: MissionTask[];
}

export interface MissionEvent {
  id: string;
  mission_id: string;
  sequence: number;
  type: string;
  created_at: string;
  data: Record<string, unknown>;
}

export interface AgentProfile {
  id: string;
  display_name: string;
  description: string;
  version: number;
  source: string;
  runtime: Record<string, unknown>;
  tools: Record<string, unknown>;
  approval: Record<string, unknown>;
  limits: Record<string, unknown>;
  workspace: Record<string, unknown>;
  artifacts: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface AccessPolicy {
  mode: string;
  current_principal: {
    id: string;
    display_name: string;
    roles: string[];
  };
  roles: Array<{
    id: string;
    description: string;
    permissions: string[];
  }>;
  scopes: string[];
  projects?: AccessProject[];
  tokens?: ApiToken[];
  audit: Record<string, unknown>;
}

export interface AccessProject {
  project_id: string;
  display_name: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ApiToken {
  token_id: string;
  name: string;
  principal_id: string;
  project_id?: string | null;
  scopes: string[];
  status: string;
  token_prefix: string;
  created_at: string;
  updated_at: string;
  revoked_at?: string | null;
  last_used_at?: string | null;
  metadata: Record<string, unknown>;
  token?: string;
}

export interface BackupInfo {
  name: string;
  size_bytes: number;
  created_at: string;
}

export interface P5Evaluation {
  id: string;
  status: string;
  mode: string;
  decision: string;
  entrypoints?: string[];
  required_env?: string;
}

export interface AuthSession {
  authenticated: boolean;
  login_required: boolean;
  principal?: {
    id: string;
    display_name: string;
    roles: string[];
  } | null;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json() as Promise<T>;
}

export const runtimeApi = {
  session: () => api<AuthSession>("auth/session"),
  login: (payload: { username: string; password: string }) =>
    api<AuthSession>("auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  logout: () =>
    api<{ authenticated: boolean }>("auth/logout", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  health: () => api<{ ok: boolean; version: string }>("health"),
  capabilities: () => api<Capabilities>("capabilities"),
  metrics: () => api<Metrics>("metrics.json"),
  costStatus: () => api<CostStatus>("cost/status"),
  queue: () => api<QueueStatus>("queue"),
  workers: () => api<{ workers: WorkerInfo[] }>("workers"),
  createWorkerRegistration: (payload: Record<string, unknown>) =>
    api<WorkerRegistration>("workers/registrations", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  workerControl: (workerId: string) =>
    api<WorkerControl>(`workers/${encodeURIComponent(workerId)}/control`),
  drainWorker: (workerId: string, reason = "drain from console") =>
    api<{ worker: WorkerInfo; control: WorkerControl }>(
      `workers/${encodeURIComponent(workerId)}/drain`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    ),
  resumeWorker: (workerId: string) =>
    api<{ worker: WorkerInfo; control: WorkerControl }>(
      `workers/${encodeURIComponent(workerId)}/resume`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    ),
  retryWorkerRuns: (workerId: string) =>
    api<{ worker_id: string; requeued_run_ids: string[]; control: WorkerControl }>(
      `workers/${encodeURIComponent(workerId)}/retry`,
      {
        method: "POST",
        body: JSON.stringify({ reason: "retry from console" }),
      },
    ),
  executors: () =>
    api<{
      executor_registry: Record<string, unknown>;
      executors: ExecutorLease[];
    }>("executors"),
  runs: () => api<{ runs: RunState[] }>("runs"),
  run: (runId: string) => api<RunState>(`runs/${runId}`),
  runEvents: (runId: string) =>
    api<{ events: RuntimeEvent[] }>(`runs/${runId}/events.json`),
  runArtifacts: (runId: string) =>
    api<{ artifacts: ArtifactInfo[] }>(`runs/${runId}/artifacts`),
  runAudit: (runId: string) =>
    api<Record<string, unknown>>(`runs/${runId}/audit.json`),
  createRun: (payload: Partial<RunSpec>) =>
    api<RunState>("runs", { method: "POST", body: JSON.stringify(payload) }),
  cancelRun: (runId: string) =>
    api<{ cancelled: boolean }>(`runs/${runId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason: "cancelled from console" }),
    }),
  resolvePermission: (
    runId: string,
    permissionId: string,
    payload: { decision: string; option_id?: string; reason?: string },
  ) =>
    api(`runs/${runId}/permissions/${permissionId}`, {
      method: "POST",
      body: JSON.stringify({ decided_by: "web-console", ...payload }),
    }),
  missions: () => api<{ missions: MissionState[] }>("missions"),
  mission: (missionId: string) => api<MissionState>(`missions/${missionId}`),
  missionEvents: (missionId: string) =>
    api<{ events: MissionEvent[] }>(`missions/${missionId}/events.json`),
  missionArtifacts: (missionId: string) =>
    api<{ artifacts: ArtifactInfo[] }>(`missions/${missionId}/artifacts`),
  createMission: (payload: Record<string, unknown>) =>
    api<MissionState>("missions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancelMission: (missionId: string, reason = "cancelled from console") =>
    api<MissionState>(`missions/${missionId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  overrideReviewGate: (
    missionId: string,
    payload: {
      decision: "approve" | "deny";
      reason: string;
      decided_by?: string;
    },
  ) =>
    api<MissionState>(`missions/${missionId}/review-gate/override`, {
      method: "POST",
      body: JSON.stringify({ decided_by: "web-console", ...payload }),
    }),
  profiles: () => api<{ profiles: AgentProfile[] }>("profiles"),
  profile: (profileId: string) => api<AgentProfile>(`profiles/${profileId}`),
  createProfile: (payload: Partial<AgentProfile>) =>
    api<AgentProfile>("profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  accessPolicy: () => api<AccessPolicy>("access/policy"),
  accessProjects: () => api<{ projects: AccessProject[] }>("access/projects"),
  createAccessProject: (payload: Partial<AccessProject>) =>
    api<AccessProject>("access/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  apiTokens: () => api<{ tokens: ApiToken[] }>("access/tokens"),
  createApiToken: (payload: Partial<ApiToken>) =>
    api<ApiToken>("access/tokens", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  revokeApiToken: (tokenId: string) =>
    api<ApiToken>(`access/tokens/${tokenId}/revoke`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  opsStatus: () => api<Record<string, unknown>>("ops/status"),
  drills: () => api<Record<string, unknown>>("ops/drills"),
  runDrills: () =>
    api<Record<string, unknown>>("ops/drills", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  backups: () => api<{ backups: BackupInfo[] }>("ops/backups"),
  createBackup: () =>
    api<{ backup: BackupInfo }>("ops/backups", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  p5Evaluations: () => api<{ components: P5Evaluation[] }>("p5/evaluations"),
};

export function artifactHref(runId: string, artifactName: string) {
  return `${API_BASE}runs/${runId}/artifacts/${encodeURIComponent(artifactName)}`;
}

export function auditHref(runId: string) {
  return `${API_BASE}runs/${runId}/audit.json`;
}

export function runEventStreamHref(runId: string) {
  return `${API_BASE}runs/${runId}/events`;
}

export function backupHref(name: string) {
  return `${API_BASE}ops/backups/${encodeURIComponent(name)}`;
}

export function missionArtifactHref(missionId: string, artifactName: string) {
  return `${API_BASE}missions/${missionId}/artifacts/${encodeURIComponent(artifactName)}`;
}

export function extractPermissionRequest(
  event: RuntimeEvent,
): PermissionRequest | null {
  if (event.type !== "permission.requested") {
    return null;
  }
  const rawId =
    event.data.permission_id ??
    nestedValue(event.data.raw, "data", "requestId");
  if (typeof rawId !== "string" || !rawId.trim()) {
    return null;
  }
  const options =
    event.data.options ?? nestedValue(event.data.raw, "data", "options");
  return {
    permission_id: rawId,
    prompt: stringValue(
      event.data.prompt ?? nestedValue(event.data.raw, "data", "prompt"),
    ),
    tool: stringValue(
      event.data.tool ?? nestedValue(event.data.raw, "data", "tool"),
    ),
    options: Array.isArray(options)
      ? options
          .filter(
            (option): option is Record<string, unknown> =>
              typeof option === "object",
          )
          .map((option) => ({
            id:
              stringValue(option.id) ||
              stringValue(option.option_id) ||
              "approve",
            label: stringValue(option.label),
            description: stringValue(option.description),
          }))
      : undefined,
    raw: event.data,
  };
}

export function resolvedPermissionIds(events: RuntimeEvent[]) {
  const ids = new Set<string>();
  for (const event of events) {
    if (event.type !== "permission.resolved") {
      continue;
    }
    const id =
      event.data.permission_id ??
      nestedValue(event.data.raw, "data", "requestId");
    if (typeof id === "string") {
      ids.add(id);
    }
  }
  return ids;
}

function getApiBase() {
  const path = window.location.pathname;
  if (path === "/cloud-agents" || path.startsWith("/cloud-agents/")) {
    return "/cloud-agents/";
  }
  return "/";
}

function nestedValue(value: unknown, ...keys: string[]) {
  let current = value;
  for (const key of keys) {
    if (!current || typeof current !== "object") {
      return undefined;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : undefined;
}
