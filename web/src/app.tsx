import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  createHashHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  RouterProvider,
  useParams,
} from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import {
  AlertTriangle,
  Copy,
  Cpu,
  Download,
  FileText,
  Filter,
  GitBranch,
  KeyRound,
  MessageSquare,
  PauseCircle,
  Play,
  Radio,
  RefreshCw,
  Save,
  Server,
  ShieldCheck,
  UserCog,
  Users,
  WalletCards,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Shell } from "./components/shell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  Field,
  Input,
  LinkButton,
  Metric,
  Select,
  StatusBadge,
  Textarea,
} from "./components/ui";
import {
  artifactHref,
  auditHref,
  backupHref,
  extractPermissionRequest,
  missionArtifactHref,
  resolvedPermissionIds,
  runEventStreamHref,
  runtimeApi,
  type AccessProject,
  type ArtifactInfo,
  type ApiToken,
  type CostStatus,
  type DrillCheck,
  type AgentProfile,
  type ExecutorLease,
  type MissionEvent,
  type MissionState,
  type RuntimeEvent,
  type RunState,
} from "./lib/api";
import { downloadJson } from "./lib/utils";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 5000,
      retry: 1,
    },
  },
});

const rootRoute = createRootRoute({ component: Shell });
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: OverviewPage,
});
const runsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs",
  component: RunsPage,
});
const runDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs/$runId",
  component: RunDetailPage,
});
const executorsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/executors",
  component: ExecutorsPage,
});
const missionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/missions",
  component: MissionsPage,
});
const missionDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/missions/$missionId",
  component: MissionDetailPage,
});
const profilesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/profiles",
  component: ProfilesPage,
});
const accessRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/access",
  component: AccessPage,
});
const operationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/operations",
  component: OperationsPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  runsRoute,
  runDetailRoute,
  executorsRoute,
  missionsRoute,
  missionDetailRoute,
  profilesRoute,
  accessRoute,
  operationsRoute,
]);

export const router = createRouter({ routeTree, history: createHashHistory() });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

function OverviewPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: runtimeApi.health });
  const metrics = useQuery({
    queryKey: ["metrics"],
    queryFn: runtimeApi.metrics,
  });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: runtimeApi.capabilities,
  });
  const runs = useQuery({ queryKey: ["runs"], queryFn: runtimeApi.runs });
  const missions = useQuery({
    queryKey: ["missions"],
    queryFn: runtimeApi.missions,
  });

  return (
    <Page
      title="Overview"
      subtitle="Runtime health, queue pressure, and latest work."
    >
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Runtime"
          value={health.data?.ok ? "Healthy" : "Checking"}
          detail={health.data?.version}
        />
        <Metric
          label="Runs"
          value={metrics.data?.runs.total ?? "-"}
          detail={statusLine(metrics.data?.runs.by_status)}
        />
        <Metric
          label="Missions"
          value={metrics.data?.missions.total ?? "-"}
          detail={statusLine(metrics.data?.missions.by_status)}
        />
        <Metric
          label="Permissions"
          value={metrics.data?.permissions.pending ?? "-"}
          detail={`${metrics.data?.permissions.stalled ?? 0} stalled`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <Card>
          <CardHeader>
            <CardTitle>Queue</CardTitle>
            <Badge tone={metrics.data?.queue.stale_workers ? "warn" : "ok"}>
              {metrics.data?.queue.active_workers ?? 0} active
            </Badge>
          </CardHeader>
          <CardBody className="grid gap-3 md:grid-cols-3">
            <Metric
              label="Queued"
              value={metrics.data?.queue.counts.queued ?? 0}
            />
            <Metric
              label="Running"
              value={metrics.data?.queue.counts.running ?? 0}
            />
            <Metric
              label="Stale workers"
              value={metrics.data?.queue.stale_workers ?? 0}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Adapters</CardTitle>
            <Badge tone="info">
              {Object.keys(capabilities.data?.adapters ?? {}).length}
            </Badge>
          </CardHeader>
          <CardBody className="grid gap-2">
            {Object.entries(capabilities.data?.adapters ?? {}).map(
              ([id, adapter]) => (
                <div
                  key={id}
                  className="flex items-center justify-between gap-3 rounded-md border border-border p-2"
                >
                  <span className="font-medium">{adapter.name || id}</span>
                  <StatusBadge status={adapter.status ?? "available"} />
                </div>
              ),
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <RecentRuns runs={runs.data?.runs ?? []} />
        <RecentMissions missions={missions.data?.missions ?? []} />
      </div>
    </Page>
  );
}

function RunsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: runtimeApi.runs });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: runtimeApi.capabilities,
  });
  return (
    <Page
      title="Runs"
      subtitle="Create and inspect isolated Agent execution units."
    >
      <div className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
        <CreateRunForm
          adapters={Object.keys(capabilities.data?.adapters ?? { fake: {} })}
        />
        <Card>
          <CardHeader>
            <CardTitle>Run History</CardTitle>
            <Button size="sm" variant="ghost" onClick={() => runs.refetch()}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </CardHeader>
          <CardBody>
            <RunList runs={runs.data?.runs ?? []} />
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}

function ExecutorsPage() {
  const executors = useQuery({
    queryKey: ["executors"],
    queryFn: runtimeApi.executors,
  });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: runtimeApi.capabilities,
  });
  const registry = executors.data?.executor_registry ?? {};
  const config = registryValue(registry, "config");
  const counts = registryValue(registry, "counts");
  const leases = executors.data?.executors ?? [];
  const activeCount = leases.filter((lease) =>
    ["starting", "running"].includes(lease.status),
  ).length;
  const failedCount = leases.filter((lease) =>
    ["failed", "orphaned"].includes(lease.status),
  ).length;

  return (
    <Page
      title="Executors"
      subtitle="Per-run qwen executor leases, isolation state, and worker registry."
    >
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Strategy"
          value={stringValue(config.strategy ?? "shared")}
          detail={config.enabled ? "registry enabled" : "shared endpoint"}
        />
        <Metric label="Active" value={activeCount} detail="starting/running" />
        <Metric label="Failed" value={failedCount} detail="failed/orphaned" />
        <Metric
          label="Container"
          value={stringValue(config.container_image ?? "-")}
          detail={stringValue(config.container_network ?? "bridge")}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-primary" />
              <CardTitle>Executor Leases</CardTitle>
            </div>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => executors.refetch()}
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </CardHeader>
          <CardBody>
            <ExecutorLeaseList leases={leases} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-primary" />
              <CardTitle>Registry</CardTitle>
            </div>
            <Badge tone={capabilities.data?.features.includes("executor_registry") ? "ok" : "neutral"}>
              {stringValue(config.strategy ?? "shared")}
            </Badge>
          </CardHeader>
          <CardBody className="grid gap-3">
            <ProfileJson label="Config" value={config} />
            <ProfileJson label="Counts" value={counts} />
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}

function ExecutorLeaseList({ leases }: { leases: ExecutorLease[] }) {
  if (!leases.length) {
    return <EmptyState title="No executor leases" />;
  }
  return (
    <div className="grid gap-2">
      {leases.map((lease) => (
        <div
          key={lease.executor_id}
          className="grid gap-3 rounded-md border border-border p-3 lg:grid-cols-[220px_120px_minmax(0,1fr)_160px]"
        >
          <div className="min-w-0">
            <div className="truncate font-mono text-xs">
              {lease.executor_id}
            </div>
            <Link
              className="mt-1 block truncate text-sm text-primary"
              to="/runs/$runId"
              params={{ runId: lease.run_id }}
            >
              {lease.run_id}
            </Link>
          </div>
          <div className="grid content-start gap-1">
            <StatusBadge status={lease.status} />
            <Badge tone="neutral">{lease.strategy}</Badge>
          </div>
          <div className="min-w-0 text-sm text-muted-foreground">
            <div className="truncate">{lease.base_url ?? "-"}</div>
            <div className="mt-1 truncate">{lease.workspace ?? "-"}</div>
            {lease.last_error ? (
              <div className="mt-1 text-destructive">{lease.last_error}</div>
            ) : null}
          </div>
          <div className="grid content-start gap-1 text-xs text-muted-foreground">
            <div>pid {lease.pid ?? "-"}</div>
            <div>port {lease.port ?? "-"}</div>
            <div>{timeAgo(lease.heartbeat_at ?? lease.started_at)}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function CreateRunForm({ adapters }: { adapters: string[] }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const createRun = useMutation({
    mutationFn: runtimeApi.createRun,
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["metrics"] });
    },
    onError: (err) => setError(String(err)),
  });
  const form = useForm({
    defaultValues: {
      adapter: adapters.includes("qwen") ? "qwen" : adapters[0] || "fake",
      prompt:
        "Summarize the current runtime status and produce a short final report.",
      repo: "",
      workspace: "",
      timeout_seconds: 1800,
    },
    onSubmit: async ({ value }) => {
      await createRun.mutateAsync({
        adapter: value.adapter,
        prompt: value.prompt,
        repo: emptyToNull(value.repo),
        workspace: emptyToNull(value.workspace),
        timeout_seconds: Number(value.timeout_seconds) || 1800,
      });
    },
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Create Run</CardTitle>
        <Badge tone="info">SAEU</Badge>
      </CardHeader>
      <CardBody>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void form.handleSubmit();
          }}
        >
          <form.Field name="adapter">
            {(field) => (
              <Field label="Adapter">
                <Select
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.value)}
                >
                  {adapters.map((adapter) => (
                    <option key={adapter} value={adapter}>
                      {adapter}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </form.Field>
          <form.Field name="prompt">
            {(field) => (
              <Field label="Prompt">
                <Textarea
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.value)}
                />
              </Field>
            )}
          </form.Field>
          <div className="grid gap-3 md:grid-cols-2">
            <form.Field name="repo">
              {(field) => (
                <Field label="Repo">
                  <Input
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="workspace">
              {(field) => (
                <Field label="Workspace">
                  <Input
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
          </div>
          <form.Field name="timeout_seconds">
            {(field) => (
              <Field label="Timeout seconds">
                <Input
                  min={60}
                  type="number"
                  value={field.state.value}
                  onChange={(event) =>
                    field.handleChange(Number(event.target.value))
                  }
                />
              </Field>
            )}
          </form.Field>
          {error ? (
            <div className="rounded-md border border-destructive/30 p-3 text-sm text-destructive">
              {error}
            </div>
          ) : null}
          <Button
            disabled={createRun.isPending}
            type="submit"
            variant="primary"
          >
            <Play className="h-4 w-4" />
            Start
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function RunDetailPage() {
  const { runId } = useParams({ from: "/runs/$runId" });
  const queryClient = useQueryClient();
  const run = useQuery({
    queryKey: ["runs", runId],
    queryFn: () => runtimeApi.run(runId),
  });
  const events = useQuery({
    queryKey: ["runs", runId, "events"],
    queryFn: () => runtimeApi.runEvents(runId),
  });
  const artifacts = useQuery({
    queryKey: ["runs", runId, "artifacts"],
    queryFn: () => runtimeApi.runArtifacts(runId),
  });
  const live = useRunLiveEvents(
    runId,
    events.data?.events ?? [],
    run.data?.status,
  );
  const cancel = useMutation({
    mutationFn: () => runtimeApi.cancelRun(runId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["runs", runId] });
    },
  });
  return (
    <Page title="Run Detail" subtitle={runId}>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid gap-4">
          <Card>
            <CardHeader>
              <CardTitle>State</CardTitle>
              <div className="flex gap-2">
                {run.data ? <StatusBadge status={run.data.status} /> : null}
                <Button
                  disabled={cancel.isPending || isTerminal(run.data?.status)}
                  size="sm"
                  onClick={() => cancel.mutate()}
                >
                  <PauseCircle className="h-4 w-4" />
                  Cancel
                </Button>
              </div>
            </CardHeader>
            <CardBody className="grid gap-3 md:grid-cols-4">
              <Metric label="Adapter" value={run.data?.spec.adapter ?? "-"} />
              <Metric label="Events" value={run.data?.event_count ?? "-"} />
              <Metric label="Inputs" value={run.data?.prompt_count ?? "-"} />
              <Metric label="Updated" value={timeAgo(run.data?.updated_at)} />
            </CardBody>
          </Card>
          <PermissionPanel runId={runId} events={live.events} />
          <LiveRunnerPanel
            connectionStatus={live.status}
            events={live.events}
            runStatus={run.data?.status}
          />
          <EventList events={live.events} />
        </div>
        <div className="grid content-start gap-4">
          <ArtifactPanel
            runId={runId}
            artifacts={artifacts.data?.artifacts ?? []}
          />
          <Card>
            <CardHeader>
              <CardTitle>Downloads</CardTitle>
            </CardHeader>
            <CardBody className="grid gap-2">
              <LinkButton href={artifactHref(runId, "events.jsonl")}>
                <Download className="h-4 w-4" />
                Events JSONL
              </LinkButton>
              <LinkButton href={artifactHref(runId, "diagnostics.json")}>
                <Download className="h-4 w-4" />
                Diagnostics
              </LinkButton>
              <LinkButton href={auditHref(runId)}>
                <Download className="h-4 w-4" />
                Audit Bundle
              </LinkButton>
            </CardBody>
          </Card>
        </div>
      </div>
    </Page>
  );
}

type LiveConnectionStatus =
  "connecting" | "live" | "reconnecting" | "closed" | "fallback";

const liveEventTypes = [
  "run.created",
  "workspace.prepared",
  "resources.resolved",
  "run.queued",
  "lease.claimed",
  "run.started",
  "input.accepted",
  "step.started",
  "step.submitted",
  "message.delta",
  "adapter.event",
  "stream.warning",
  "permission.requested",
  "permission.resolved",
  "permission.stalled",
  "step.completed",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "cancel.warning",
  "input.rejected",
  "adapter.not_configured",
];

function useRunLiveEvents(
  runId: string,
  initialEvents: RuntimeEvent[],
  runStatus?: string,
) {
  const [events, setEvents] = useState<RuntimeEvent[]>(initialEvents);
  const [status, setStatus] = useState<LiveConnectionStatus>("connecting");

  useEffect(() => {
    setEvents((current) => mergeEvents(current, initialEvents));
  }, [initialEvents]);

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      setStatus("fallback");
      return;
    }
    if (isTerminal(runStatus)) {
      setStatus("closed");
      return;
    }

    setStatus("connecting");
    const source = new EventSource(runEventStreamHref(runId));
    const handleEvent = (message: MessageEvent) => {
      try {
        const event = JSON.parse(message.data) as RuntimeEvent;
        setEvents((current) => mergeEvents(current, [event]));
        if (isTerminalEvent(event.type)) {
          setStatus("closed");
          source.close();
        }
      } catch {
        setStatus("reconnecting");
      }
    };
    for (const eventType of liveEventTypes) {
      source.addEventListener(eventType, handleEvent);
    }
    source.onopen = () => setStatus("live");
    source.onerror = () =>
      setStatus(
        source.readyState === EventSource.CLOSED ? "closed" : "reconnecting",
      );

    return () => {
      for (const eventType of liveEventTypes) {
        source.removeEventListener(eventType, handleEvent);
      }
      source.close();
    };
  }, [runId, runStatus]);

  return { events, status };
}

function LiveRunnerPanel({
  connectionStatus,
  events,
  runStatus,
}: {
  connectionStatus: LiveConnectionStatus;
  events: RuntimeEvent[];
  runStatus?: string;
}) {
  const transcript = useMemo(() => runnerTranscript(events), [events]);
  const [filter, setFilter] = useState<RunnerFilter>("all");
  const filteredTranscript = useMemo(
    () => filterTranscript(transcript, filter),
    [filter, transcript],
  );
  const latest = events.at(-1);
  const signal = runnerSignal(latest, runStatus);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!scrollRef.current) {
      return;
    }
    if (typeof scrollRef.current.scrollTo === "function") {
      scrollRef.current.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
      return;
    }
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [filteredTranscript.length, latest?.sequence]);

  return (
    <Card>
      <CardHeader>
        <div className="flex min-w-0 items-center gap-2">
          <MessageSquare className="h-4 w-4 text-primary" />
          <CardTitle>Live Runner Chat</CardTitle>
        </div>
        <Badge tone={connectionTone(connectionStatus)}>
          <Radio className="h-4 w-4" />
          {connectionLabel(connectionStatus)}
        </Badge>
      </CardHeader>
      <CardBody className="grid gap-4">
        <div className="grid gap-3 md:grid-cols-3">
          <Metric label="Run status" value={runStatus ?? "loading"} />
          <Metric label="Last event" value={latest?.type ?? "-"} />
          <Metric
            label="Runner signal"
            value={signal.label}
            detail={latest ? `seq ${latest.sequence}` : undefined}
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-2">
            {(["all", "agent", "permission", "warning", "error"] as const).map(
              (item) => (
                <Button
                  key={item}
                  size="sm"
                  variant={filter === item ? "primary" : "secondary"}
                  onClick={() => setFilter(item)}
                >
                  <Filter className="h-4 w-4" />
                  {filterLabel(item)}
                </Button>
              ),
            )}
          </div>
          <Button
            size="sm"
            onClick={() =>
              downloadText(
                `run-${latest?.run_id ?? "runner"}-report.md`,
                runnerReadableReport(transcript, events),
              )
            }
          >
            <FileText className="h-4 w-4" />
            Download Report
          </Button>
        </div>
        {signal.tone === "warn" ? (
          <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-amber-800 dark:text-warning">
            No runner event has arrived recently. You can inspect raw events,
            download the audit bundle, or cancel/retry the run.
          </div>
        ) : null}
        <div
          ref={scrollRef}
          className="grid max-h-[520px] gap-3 overflow-auto rounded-md border border-border bg-muted/40 p-3"
        >
          {filteredTranscript.map((item) => (
            <RunnerBubble key={item.id} item={item} />
          ))}
          {!filteredTranscript.length ? (
            <EmptyState
              title="Waiting for runner output"
              detail="The live stream will append steps, messages, permission requests, and terminal state here."
            />
          ) : null}
        </div>
      </CardBody>
    </Card>
  );
}

type RunnerTranscriptItem = {
  id: string;
  role: "system" | "agent" | "operator" | "warning" | "success" | "error";
  title: string;
  body: string;
  created_at: string;
  event_type: string;
  sequence: number;
};

type RunnerFilter = "all" | "agent" | "permission" | "warning" | "error";

function RunnerBubble({ item }: { item: RunnerTranscriptItem }) {
  return (
    <div
      className={`flex ${item.role === "agent" ? "justify-start" : "justify-end"}`}
    >
      <div
        className={`max-w-[860px] rounded-lg border p-3 text-sm ${bubbleClass(item.role)}`}
      >
        <div className="flex items-center justify-between gap-3">
          <span className="font-medium">{item.title}</span>
          <span className="shrink-0 text-xs opacity-70">
            {item.sequence} · {timeAgo(item.created_at)}
          </span>
        </div>
        <div className="mt-2 whitespace-pre-wrap break-words leading-6">
          {item.body}
        </div>
        <div className="mt-2 font-mono text-xs opacity-60">
          {item.event_type}
        </div>
      </div>
    </div>
  );
}

function PermissionPanel({
  runId,
  events,
}: {
  runId: string;
  events: RuntimeEvent[];
}) {
  const queryClient = useQueryClient();
  const resolved = resolvedPermissionIds(events);
  const pending = events
    .map(extractPermissionRequest)
    .filter((request): request is NonNullable<typeof request> =>
      Boolean(request),
    )
    .filter((request) => !resolved.has(request.permission_id));
  const resolve = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: string }) =>
      runtimeApi.resolvePermission(runId, id, {
        decision,
        option_id: decision,
        reason: "resolved from web console",
      }),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: ["runs", runId, "events"] }),
  });
  if (!pending.length) {
    return null;
  }
  return (
    <Card className="border-warning/40">
      <CardHeader>
        <CardTitle>Permission Requests</CardTitle>
        <Badge tone="warn">{pending.length} pending</Badge>
      </CardHeader>
      <CardBody className="grid gap-3">
        {pending.map((request) => (
          <div
            key={request.permission_id}
            className="rounded-md border border-border p-3"
          >
            <div className="font-medium">
              {request.prompt || request.tool || request.permission_id}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {(request.options?.length
                ? request.options
                : [{ id: "approve" }, { id: "deny" }]
              ).map((option) => (
                <Button
                  key={option.id}
                  size="sm"
                  variant={option.id === "deny" ? "danger" : "primary"}
                  onClick={() =>
                    resolve.mutate({
                      id: request.permission_id,
                      decision: option.id,
                    })
                  }
                >
                  {option.label || option.id}
                </Button>
              ))}
            </div>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function MissionsPage() {
  const missions = useQuery({
    queryKey: ["missions"],
    queryFn: runtimeApi.missions,
  });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: runtimeApi.capabilities,
  });
  return (
    <Page
      title="Missions"
      subtitle="Profile-based multi-agent task orchestration."
    >
      <div className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
        <CreateMissionForm
          adapters={Object.keys(capabilities.data?.adapters ?? { fake: {} })}
        />
        <Card>
          <CardHeader>
            <CardTitle>Mission History</CardTitle>
          </CardHeader>
          <CardBody>
            <MissionList missions={missions.data?.missions ?? []} />
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}

function MissionDetailPage() {
  const { missionId } = useParams({ from: "/missions/$missionId" });
  const queryClient = useQueryClient();
  const mission = useQuery({
    queryKey: ["missions", missionId],
    queryFn: () => runtimeApi.mission(missionId),
  });
  const events = useQuery({
    queryKey: ["missions", missionId, "events"],
    queryFn: () => runtimeApi.missionEvents(missionId),
  });
  const artifacts = useQuery({
    queryKey: ["missions", missionId, "artifacts"],
    queryFn: () => runtimeApi.missionArtifacts(missionId),
  });
  const cancel = useMutation({
    mutationFn: () => runtimeApi.cancelMission(missionId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["missions"] });
      await queryClient.invalidateQueries({
        queryKey: ["missions", missionId],
      });
    },
  });
  const override = useMutation({
    mutationFn: (decision: "approve" | "deny") =>
      runtimeApi.overrideReviewGate(missionId, {
        decision,
        reason: `review gate ${decision} from web console`,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["missions"] });
      await queryClient.invalidateQueries({
        queryKey: ["missions", missionId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["missions", missionId, "events"],
      });
    },
  });
  const state = mission.data;
  const missionEvents = events.data?.events ?? [];
  return (
    <Page title="Mission Detail" subtitle={missionId}>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid gap-4">
          <Card>
            <CardHeader>
              <div className="min-w-0">
                <CardTitle>Mission State</CardTitle>
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                  {state?.spec.goal ?? "Loading mission goal"}
                </p>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                {state ? <StatusBadge status={state.status} /> : null}
                <Button
                  disabled={cancel.isPending || isTerminal(state?.status)}
                  size="sm"
                  onClick={() => cancel.mutate()}
                >
                  <PauseCircle className="h-4 w-4" />
                  Cancel
                </Button>
              </div>
            </CardHeader>
            <CardBody className="grid gap-3 md:grid-cols-4">
              <Metric label="Strategy" value={state?.spec.strategy ?? "-"} />
              <Metric label="Adapter" value={state?.spec.adapter ?? "-"} />
              <Metric
                label="Progress"
                value={`${state?.completed_task_count ?? 0}/${state?.task_count ?? 0}`}
              />
              <Metric label="Events" value={state?.event_count ?? "-"} />
            </CardBody>
          </Card>
          {state?.status === "blocked" ? (
            <Card className="border-warning/40">
              <CardHeader>
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-warning" />
                  <CardTitle>Review Gate Blocked</CardTitle>
                </div>
                <Badge tone="warn">human decision</Badge>
              </CardHeader>
              <CardBody className="flex flex-wrap gap-2">
                <Button
                  disabled={override.isPending}
                  size="sm"
                  variant="primary"
                  onClick={() => override.mutate("approve")}
                >
                  Approve Gate
                </Button>
                <Button
                  disabled={override.isPending}
                  size="sm"
                  variant="danger"
                  onClick={() => override.mutate("deny")}
                >
                  Deny Gate
                </Button>
              </CardBody>
            </Card>
          ) : null}
          <MissionDagPanel mission={state} />
          <MissionEventList events={missionEvents} />
        </div>
        <div className="grid content-start gap-4">
          <MissionArtifactPanel
            missionId={missionId}
            artifacts={artifacts.data?.artifacts ?? []}
          />
          <Card>
            <CardHeader>
              <CardTitle>Downloads</CardTitle>
            </CardHeader>
            <CardBody className="grid gap-2">
              <LinkButton
                href={missionArtifactHref(missionId, "manifest.json")}
              >
                <Download className="h-4 w-4" />
                Manifest
              </LinkButton>
              <LinkButton href={missionArtifactHref(missionId, "events.jsonl")}>
                <Download className="h-4 w-4" />
                Events JSONL
              </LinkButton>
              <LinkButton
                href={missionArtifactHref(missionId, "final-report.md")}
              >
                <Download className="h-4 w-4" />
                Final Report
              </LinkButton>
            </CardBody>
          </Card>
        </div>
      </div>
    </Page>
  );
}

function CreateMissionForm({ adapters }: { adapters: string[] }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const createMission = useMutation({
    mutationFn: runtimeApi.createMission,
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["missions"] });
    },
    onError: (err) => setError(String(err)),
  });
  const form = useForm({
    defaultValues: {
      adapter: adapters.includes("qwen") ? "qwen" : adapters[0] || "fake",
      strategy: "sequential",
      goal: "Inspect the runtime, run validation, review risks, and produce a final report.",
    },
    onSubmit: async ({ value }) => createMission.mutateAsync(value),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Create Mission</CardTitle>
        <Badge tone="info">DAG</Badge>
      </CardHeader>
      <CardBody>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void form.handleSubmit();
          }}
        >
          <form.Field name="goal">
            {(field) => (
              <Field label="Goal">
                <Textarea
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.value)}
                />
              </Field>
            )}
          </form.Field>
          <div className="grid gap-3 md:grid-cols-2">
            <form.Field name="strategy">
              {(field) => (
                <Field label="Strategy">
                  <Select
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  >
                    <option value="sequential">sequential</option>
                    <option value="fanout">fanout</option>
                  </Select>
                </Field>
              )}
            </form.Field>
            <form.Field name="adapter">
              {(field) => (
                <Field label="Adapter">
                  <Select
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  >
                    {adapters.map((adapter) => (
                      <option key={adapter} value={adapter}>
                        {adapter}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
            </form.Field>
          </div>
          {error ? (
            <div className="rounded-md border border-destructive/30 p-3 text-sm text-destructive">
              {error}
            </div>
          ) : null}
          <Button
            disabled={createMission.isPending}
            type="submit"
            variant="primary"
          >
            <Play className="h-4 w-4" />
            Start
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function ProfilesPage() {
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: runtimeApi.profiles,
  });
  const [draft, setDraft] = useState<AgentProfile | null>(null);
  return (
    <Page
      title="Profiles"
      subtitle="Reusable Agent roles and execution policies."
    >
      <div className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
        <ProfileEditor
          key={
            draft ? `${draft.id}-${draft.version}-${draft.display_name}` : "new"
          }
          draft={draft}
          onSaved={() => setDraft(null)}
        />
        <div className="grid gap-4 md:grid-cols-2">
          {(profiles.data?.profiles ?? []).map((profile) => (
            <Card key={`${profile.id}-${profile.version}`}>
              <CardHeader>
                <div>
                  <CardTitle>{profile.display_name}</CardTitle>
                  <div className="mt-1 font-mono text-xs text-muted-foreground">
                    {profile.id}
                  </div>
                </div>
                <Badge tone={profile.source === "system" ? "info" : "neutral"}>
                  v{profile.version}
                </Badge>
              </CardHeader>
              <CardBody className="grid gap-3">
                <p className="text-sm text-muted-foreground">
                  {profile.description}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    onClick={() => setDraft(copyProfile(profile))}
                  >
                    <Copy className="h-4 w-4" />
                    Copy
                  </Button>
                  <Button size="sm" onClick={() => setDraft(profile)}>
                    <UserCog className="h-4 w-4" />
                    Edit
                  </Button>
                </div>
                <ProfileJson label="Runtime" value={profile.runtime} />
                <ProfileJson label="Tools" value={profile.tools} />
                <ProfileJson label="Approval" value={profile.approval} />
                <ProfileJson label="Limits" value={profile.limits} />
                <ProfileJson label="Workspace" value={profile.workspace} />
                <ProfileJson label="Artifacts" value={profile.artifacts} />
              </CardBody>
            </Card>
          ))}
        </div>
      </div>
    </Page>
  );
}

function AccessPage() {
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState("default");
  const [projectName, setProjectName] = useState("Default");
  const [tokenName, setTokenName] = useState("operator-token");
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const policy = useQuery({
    queryKey: ["access", "policy"],
    queryFn: runtimeApi.accessPolicy,
  });
  const projects = useQuery({
    queryKey: ["access", "projects"],
    queryFn: runtimeApi.accessProjects,
  });
  const tokens = useQuery({
    queryKey: ["access", "tokens"],
    queryFn: runtimeApi.apiTokens,
  });
  const createProject = useMutation({
    mutationFn: runtimeApi.createAccessProject,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["access"] });
    },
  });
  const createToken = useMutation({
    mutationFn: runtimeApi.createApiToken,
    onSuccess: async (token) => {
      setCreatedToken(token.token ?? null);
      await queryClient.invalidateQueries({ queryKey: ["access"] });
    },
  });
  const revokeToken = useMutation({
    mutationFn: runtimeApi.revokeApiToken,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["access"] });
    },
  });
  const principal = policy.data?.current_principal;
  return (
    <Page
      title="Access"
      subtitle="Single-tenant beta access posture and RBAC migration plan."
    >
      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4 text-primary" />
              <CardTitle>Current Principal</CardTitle>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge tone="info">{policy.data?.mode ?? "loading"}</Badge>
              <Button
                disabled={!policy.data}
                size="sm"
                onClick={() => downloadJson("access-policy.json", policy.data)}
              >
                <Download className="h-4 w-4" />
                Export
              </Button>
            </div>
          </CardHeader>
          <CardBody className="grid gap-3">
            <Metric label="Identity" value={principal?.display_name ?? "-"} />
            <Metric label="Roles" value={principal?.roles.join(", ") || "-"} />
            <ProfileJson
              label="Audit Posture"
              value={policy.data?.audit ?? {}}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Role Matrix</CardTitle>
            <Badge tone="neutral">{policy.data?.roles.length ?? 0}</Badge>
          </CardHeader>
          <CardBody className="grid gap-3">
            {(policy.data?.roles ?? []).map((role) => (
              <div
                key={role.id}
                className="rounded-md border border-border p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-medium">{role.id}</div>
                    <div className="mt-1 text-sm text-muted-foreground">
                      {role.description}
                    </div>
                  </div>
                  <Badge tone="neutral">{role.permissions.length}</Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-1">
                  {role.permissions.map((permission) => (
                    <Badge key={permission} tone="neutral">
                      {permission}
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Scopes</CardTitle>
          <Badge tone="info">P7 foundation</Badge>
        </CardHeader>
        <CardBody className="flex flex-wrap gap-2">
          {(policy.data?.scopes ?? []).map((scope) => (
            <Badge key={scope} tone="neutral">
              {scope}
            </Badge>
          ))}
        </CardBody>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4 text-primary" />
              <CardTitle>Projects</CardTitle>
            </div>
            <Badge tone="neutral">{projects.data?.projects.length ?? 0}</Badge>
          </CardHeader>
          <CardBody className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
              <Field label="Project ID">
                <Input
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                />
              </Field>
              <Field label="Display name">
                <Input
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                />
              </Field>
              <Button
                className="self-end"
                disabled={createProject.isPending}
                onClick={() =>
                  createProject.mutate({
                    project_id: projectId,
                    display_name: projectName,
                  })
                }
              >
                <Save className="h-4 w-4" />
                Create
              </Button>
            </div>
            <AccessProjectList projects={projects.data?.projects ?? policy.data?.projects ?? []} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <KeyRound className="h-4 w-4 text-primary" />
              <CardTitle>API Tokens</CardTitle>
            </div>
            <Badge tone="neutral">{tokens.data?.tokens.length ?? 0}</Badge>
          </CardHeader>
          <CardBody className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
              <Field label="Token name">
                <Input
                  value={tokenName}
                  onChange={(event) => setTokenName(event.target.value)}
                />
              </Field>
              <Field label="Project ID">
                <Input
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                />
              </Field>
              <Button
                className="self-end"
                disabled={createToken.isPending}
                onClick={() =>
                  createToken.mutate({
                    name: tokenName,
                    project_id: projectId || undefined,
                  })
                }
              >
                <KeyRound className="h-4 w-4" />
                Create
              </Button>
            </div>
            {createdToken ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-3">
                <div className="text-sm font-medium">New token</div>
                <div className="mt-2 break-words font-mono text-xs">
                  {createdToken}
                </div>
              </div>
            ) : null}
            <ApiTokenList
              tokens={tokens.data?.tokens ?? policy.data?.tokens ?? []}
              onRevoke={(tokenId) => revokeToken.mutate(tokenId)}
            />
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}

function AccessProjectList({ projects }: { projects: AccessProject[] }) {
  if (!projects.length) {
    return <EmptyState title="No projects" />;
  }
  return (
    <div className="grid gap-2">
      {projects.map((project) => (
        <div
          key={project.project_id}
          className="rounded-md border border-border p-3"
        >
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="font-medium">{project.display_name}</div>
              <div className="mt-1 font-mono text-xs text-muted-foreground">
                {project.project_id}
              </div>
            </div>
            <StatusBadge status={project.status} />
          </div>
        </div>
      ))}
    </div>
  );
}

function ApiTokenList({
  tokens,
  onRevoke,
}: {
  tokens: ApiToken[];
  onRevoke: (tokenId: string) => void;
}) {
  if (!tokens.length) {
    return <EmptyState title="No API tokens" />;
  }
  return (
    <div className="grid gap-2">
      {tokens.map((token) => (
        <div
          key={token.token_id}
          className="grid gap-3 rounded-md border border-border p-3 md:grid-cols-[minmax(0,1fr)_auto]"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate font-medium">{token.name}</span>
              <StatusBadge status={token.status} />
            </div>
            <div className="mt-1 font-mono text-xs text-muted-foreground">
              {token.token_id} / {token.token_prefix}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {token.scopes.map((scope) => (
                <Badge key={scope} tone="neutral">
                  {scope}
                </Badge>
              ))}
            </div>
          </div>
          <Button
            disabled={token.status !== "active"}
            size="sm"
            variant="danger"
            onClick={() => onRevoke(token.token_id)}
          >
            Revoke
          </Button>
        </div>
      ))}
    </div>
  );
}

function ProfileEditor({
  draft,
  onSaved,
}: {
  draft: AgentProfile | null;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const createProfile = useMutation({
    mutationFn: runtimeApi.createProfile,
    onSuccess: async () => {
      setError(null);
      onSaved();
      await queryClient.invalidateQueries({ queryKey: ["profiles"] });
      await queryClient.invalidateQueries({ queryKey: ["capabilities"] });
    },
    onError: (err) => setError(String(err)),
  });
  const defaultProfile = draft ?? emptyProfile();
  const form = useForm({
    defaultValues: {
      id: defaultProfile.id,
      display_name: defaultProfile.display_name,
      description: defaultProfile.description,
      runtime: prettyJson(defaultProfile.runtime),
      tools: prettyJson(defaultProfile.tools),
      approval: prettyJson(defaultProfile.approval),
      limits: prettyJson(defaultProfile.limits),
      workspace: prettyJson(defaultProfile.workspace),
      artifacts: prettyJson(defaultProfile.artifacts),
    },
    onSubmit: async ({ value }) => {
      try {
        await createProfile.mutateAsync({
          id: value.id,
          display_name: value.display_name,
          description: value.description,
          runtime: parseJsonObject(value.runtime, "runtime"),
          tools: parseJsonObject(value.tools, "tools"),
          approval: parseJsonObject(value.approval, "approval"),
          limits: parseJsonObject(value.limits, "limits"),
          workspace: parseJsonObject(value.workspace, "workspace"),
          artifacts: parseJsonObject(value.artifacts, "artifacts"),
        });
      } catch (err) {
        setError(String(err));
      }
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile Editor</CardTitle>
        <Badge tone="info">versioned</Badge>
      </CardHeader>
      <CardBody>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void form.handleSubmit();
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            <form.Field name="id">
              {(field) => (
                <Field label="Profile ID">
                  <Input
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="display_name">
              {(field) => (
                <Field label="Display name">
                  <Input
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
          </div>
          <form.Field name="description">
            {(field) => (
              <Field label="Description">
                <Textarea
                  className="min-h-20"
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.value)}
                />
              </Field>
            )}
          </form.Field>
          {(
            [
              "runtime",
              "tools",
              "approval",
              "limits",
              "workspace",
              "artifacts",
            ] as const
          ).map((name) => (
            <form.Field key={name} name={name}>
              {(field) => (
                <Field label={`${name} JSON`}>
                  <Textarea
                    className="min-h-24 font-mono text-xs"
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
          ))}
          {error ? (
            <div className="rounded-md border border-destructive/30 p-3 text-sm text-destructive">
              {error}
            </div>
          ) : null}
          <Button
            disabled={createProfile.isPending}
            type="submit"
            variant="primary"
          >
            <Save className="h-4 w-4" />
            Save Profile
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function OperationsPage() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["ops", "status"],
    queryFn: runtimeApi.opsStatus,
  });
  const drills = useQuery({
    queryKey: ["ops", "drills"],
    queryFn: runtimeApi.drills,
  });
  const backups = useQuery({
    queryKey: ["ops", "backups"],
    queryFn: runtimeApi.backups,
  });
  const p5 = useQuery({ queryKey: ["p5"], queryFn: runtimeApi.p5Evaluations });
  const cost = useQuery({
    queryKey: ["cost"],
    queryFn: runtimeApi.costStatus,
  });
  const createBackup = useMutation({
    mutationFn: runtimeApi.createBackup,
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: ["ops", "backups"] }),
  });
  const runDrills = useMutation({
    mutationFn: runtimeApi.runDrills,
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: ["ops", "drills"] }),
  });
  const checks = (drills.data?.checks ?? []) as DrillCheck[];
  return (
    <Page
      title="Operations"
      subtitle="P5 protocol decisions and P6 beta readiness controls."
    >
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <Card>
          <CardHeader>
            <CardTitle>Failure Drills</CardTitle>
            <Button
              size="sm"
              variant="primary"
              onClick={() => runDrills.mutate()}
            >
              <ShieldCheck className="h-4 w-4" />
              Run
            </Button>
          </CardHeader>
          <CardBody className="grid gap-2">
            {checks.map((check) => (
              <div
                key={check.id}
                className="grid gap-2 rounded-md border border-border p-3 md:grid-cols-[160px_100px_1fr]"
              >
                <span className="font-mono text-xs">{check.id}</span>
                <StatusBadge status={check.status} />
                <span className="text-sm text-muted-foreground">
                  {check.summary}
                </span>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Backups</CardTitle>
            <Button
              disabled={createBackup.isPending}
              size="sm"
              onClick={() => createBackup.mutate()}
            >
              <Download className="h-4 w-4" />
              Create
            </Button>
          </CardHeader>
          <CardBody className="grid gap-2">
            {(backups.data?.backups ?? []).map((backup) => (
              <a
                key={backup.name}
                className="rounded-md border border-border p-3 text-sm hover:bg-muted"
                href={backupHref(backup.name)}
              >
                <div className="font-medium">{backup.name}</div>
                <div className="text-xs text-muted-foreground">
                  {formatBytes(backup.size_bytes)}
                </div>
              </a>
            ))}
            {!backups.data?.backups.length ? (
              <EmptyState title="No backups yet" />
            ) : null}
          </CardBody>
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <CostBudgetPanel cost={cost.data} />
        <Card>
          <CardHeader>
            <CardTitle>P5 Evaluations</CardTitle>
          </CardHeader>
          <CardBody className="grid gap-2">
            {(p5.data?.components ?? []).map((component) => (
              <div
                key={component.id}
                className="rounded-md border border-border p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">{component.id}</span>
                  <StatusBadge status={component.status} />
                </div>
                <div className="mt-2 text-sm text-muted-foreground">
                  {component.decision}
                </div>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Runtime Status</CardTitle>
          </CardHeader>
          <CardBody>
            <pre className="max-h-[420px] overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
              {JSON.stringify(status.data ?? {}, null, 2)}
            </pre>
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}

function CostBudgetPanel({ cost }: { cost?: CostStatus }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <WalletCards className="h-4 w-4 text-primary" />
          <CardTitle>Cost Budget</CardTitle>
        </div>
        <StatusBadge status={cost?.status ?? "loading"} />
      </CardHeader>
      <CardBody className="grid gap-3 md:grid-cols-3">
        <Metric
          label="Month"
          value={cost?.month ?? "-"}
          detail="UTC"
        />
        <Metric
          label="Estimated"
          value={money(cost?.monthly_estimated_cost_usd)}
          detail={`${cost?.runs.length ?? 0} runs`}
        />
        <Metric
          label="Budget"
          value={money(cost?.monthly_budget_usd)}
          detail={
            cost?.warning_threshold_usd == null
              ? "unconfigured"
              : `warn at ${money(cost.warning_threshold_usd)}`
          }
        />
      </CardBody>
    </Card>
  );
}

function RunList({ runs }: { runs: RunState[] }) {
  if (!runs.length) {
    return (
      <EmptyState
        title="No runs"
        detail="Create the first SAEU run from the form."
      />
    );
  }
  return (
    <div className="grid gap-2">
      {runs.map((run) => (
        <Link
          key={run.run_id}
          className="grid gap-2 rounded-md border border-border p-3 hover:bg-muted"
          to="/runs/$runId"
          params={{ runId: run.run_id }}
        >
          <div className="flex items-center justify-between gap-3">
            <span className="truncate font-mono text-xs">{run.run_id}</span>
            <StatusBadge status={run.status} />
          </div>
          <div className="line-clamp-2 text-sm text-muted-foreground">
            {run.spec.prompt || run.spec.adapter}
          </div>
        </Link>
      ))}
    </div>
  );
}

function RecentRuns({ runs }: { runs: RunState[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Runs</CardTitle>
        <Link className="text-sm text-primary" to="/runs">
          View all
        </Link>
      </CardHeader>
      <CardBody>
        <RunList runs={runs.slice(0, 5)} />
      </CardBody>
    </Card>
  );
}

function MissionList({ missions }: { missions: MissionState[] }) {
  if (!missions.length) {
    return (
      <EmptyState
        title="No missions"
        detail="Create a mission to fan out work across profiles."
      />
    );
  }
  return (
    <div className="grid gap-3">
      {missions.map((mission) => (
        <div
          key={mission.mission_id}
          className="rounded-md border border-border p-3"
        >
          <div className="flex items-center justify-between gap-3">
            <span className="truncate font-mono text-xs">
              {mission.mission_id}
            </span>
            <StatusBadge status={mission.status} />
          </div>
          <div className="mt-2 text-sm">{mission.spec.goal}</div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {mission.tasks.map((task) => (
              <div
                key={task.task_id}
                className="rounded-md bg-muted p-2 text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{task.title}</span>
                  <StatusBadge status={task.status} />
                </div>
                <div className="mt-1 text-muted-foreground">
                  {task.profile_id}
                </div>
                {task.run_id ? (
                  <Link
                    className="mt-1 block text-primary"
                    to="/runs/$runId"
                    params={{ runId: task.run_id }}
                  >
                    {task.run_id}
                  </Link>
                ) : null}
              </div>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link
              className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-2 text-xs font-medium text-primary hover:bg-muted"
              to="/missions/$missionId"
              params={{ missionId: mission.mission_id }}
            >
              <GitBranch className="h-4 w-4" />
              Open detail
            </Link>
            <LinkButton
              href={missionArtifactHref(mission.mission_id, "manifest.json")}
              size="sm"
            >
              <Download className="h-4 w-4" />
              Manifest
            </LinkButton>
            <LinkButton
              href={missionArtifactHref(mission.mission_id, "final-report.md")}
              size="sm"
            >
              <Download className="h-4 w-4" />
              Report
            </LinkButton>
          </div>
        </div>
      ))}
    </div>
  );
}

function MissionDagPanel({ mission }: { mission?: MissionState }) {
  const tasks = mission?.tasks ?? [];
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-primary" />
          <CardTitle>Task DAG</CardTitle>
        </div>
        <Badge tone="neutral">{tasks.length}</Badge>
      </CardHeader>
      <CardBody className="grid gap-3">
        {tasks.map((task) => (
          <div
            key={task.task_id}
            className="grid gap-3 rounded-md border border-border p-3 lg:grid-cols-[220px_minmax(0,1fr)_180px]"
          >
            <div className="min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{task.title}</span>
                <StatusBadge status={task.status} />
              </div>
              <div className="mt-1 font-mono text-xs text-muted-foreground">
                {task.task_id}
              </div>
            </div>
            <div className="grid gap-2 text-sm">
              <div>
                <span className="text-muted-foreground">Profile </span>
                <span className="font-medium">{task.profile_id}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {(task.depends_on.length ? task.depends_on : ["root"]).map(
                  (dependency) => (
                    <Badge key={dependency} tone="neutral">
                      {dependency}
                    </Badge>
                  ),
                )}
              </div>
            </div>
            <div className="grid content-start gap-2">
              {task.run_id ? (
                <Link
                  className="inline-flex items-center gap-2 text-sm text-primary"
                  to="/runs/$runId"
                  params={{ runId: task.run_id }}
                >
                  <MessageSquare className="h-4 w-4" />
                  Open run
                </Link>
              ) : (
                <span className="text-sm text-muted-foreground">
                  Waiting for dependencies
                </span>
              )}
              {task.result ? (
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    Result
                  </summary>
                  <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-slate-950 p-2 text-slate-100">
                    {JSON.stringify(task.result, null, 2)}
                  </pre>
                </details>
              ) : null}
            </div>
          </div>
        ))}
        {!tasks.length ? <EmptyState title="No tasks" /> : null}
      </CardBody>
    </Card>
  );
}

function MissionEventList({ events }: { events: MissionEvent[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Mission Events</CardTitle>
        <Badge tone="neutral">{events.length}</Badge>
      </CardHeader>
      <CardBody className="grid max-h-[560px] gap-2 overflow-auto">
        {events.map((event) => (
          <div key={event.id} className="rounded-md border border-border p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs">
                {event.sequence}. {event.type}
              </span>
              <span className="text-xs text-muted-foreground">
                {timeAgo(event.created_at)}
              </span>
            </div>
            <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-slate-950 p-2 text-xs text-slate-100">
              {JSON.stringify(event.data, null, 2)}
            </pre>
          </div>
        ))}
        {!events.length ? <EmptyState title="No mission events" /> : null}
      </CardBody>
    </Card>
  );
}

function MissionArtifactPanel({
  missionId,
  artifacts,
}: {
  missionId: string;
  artifacts: ArtifactInfo[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Mission Artifacts</CardTitle>
        <Badge tone="neutral">{artifacts.length}</Badge>
      </CardHeader>
      <CardBody className="grid gap-2">
        {artifacts.map((artifact) => (
          <a
            key={artifact.name}
            className="rounded-md border border-border p-3 text-sm hover:bg-muted"
            href={missionArtifactHref(missionId, artifact.name)}
          >
            <div className="font-medium">{artifact.name}</div>
            <div className="text-xs text-muted-foreground">
              {formatBytes(artifact.size_bytes)}
            </div>
          </a>
        ))}
        {!artifacts.length ? (
          <EmptyState title="No mission artifacts yet" />
        ) : null}
      </CardBody>
    </Card>
  );
}

function RecentMissions({ missions }: { missions: MissionState[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Missions</CardTitle>
        <Link className="text-sm text-primary" to="/missions">
          View all
        </Link>
      </CardHeader>
      <CardBody>
        <MissionList missions={missions.slice(0, 3)} />
      </CardBody>
    </Card>
  );
}

function EventList({ events }: { events: RuntimeEvent[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Event Stream</CardTitle>
        <Badge tone="neutral">{events.length}</Badge>
      </CardHeader>
      <CardBody className="grid max-h-[560px] gap-2 overflow-auto">
        {events.map((event) => (
          <div key={event.id} className="rounded-md border border-border p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs">
                {event.sequence}. {event.type}
              </span>
              <span className="text-xs text-muted-foreground">
                {timeAgo(event.created_at)}
              </span>
            </div>
            <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-slate-950 p-2 text-xs text-slate-100">
              {JSON.stringify(event.data, null, 2)}
            </pre>
          </div>
        ))}
        {!events.length ? <EmptyState title="No events" /> : null}
      </CardBody>
    </Card>
  );
}

function ArtifactPanel({
  runId,
  artifacts,
}: {
  runId: string;
  artifacts: ArtifactInfo[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Artifacts</CardTitle>
        <Badge tone="neutral">{artifacts.length}</Badge>
      </CardHeader>
      <CardBody className="grid gap-2">
        {artifacts.map((artifact) => (
          <a
            key={artifact.name}
            className="rounded-md border border-border p-3 text-sm hover:bg-muted"
            href={artifactHref(runId, artifact.name)}
          >
            <div className="font-medium">{artifact.name}</div>
            <div className="text-xs text-muted-foreground">
              {formatBytes(artifact.size_bytes)}
            </div>
          </a>
        ))}
        {!artifacts.length ? <EmptyState title="No artifacts yet" /> : null}
      </CardBody>
    </Card>
  );
}

function ProfileJson({
  label,
  value,
}: {
  label: string;
  value: Record<string, unknown>;
}) {
  return (
    <details className="rounded-md border border-border p-2">
      <summary className="cursor-pointer text-sm font-medium">{label}</summary>
      <pre className="mt-2 max-h-44 overflow-auto rounded-md bg-slate-950 p-2 text-xs text-slate-100">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function emptyProfile(): AgentProfile {
  return {
    id: "custom-profile",
    display_name: "Custom Profile",
    description: "Describe when this Agent profile should be used.",
    version: 1,
    source: "user",
    runtime: { preferred_adapter: "qwen" },
    tools: { allow: [], deny: [] },
    approval: { mode: "ask" },
    limits: { max_turns: 40, timeout_seconds: 1800 },
    workspace: { strategy: "per_run" },
    artifacts: { required: ["final-report.md"] },
    metadata: {},
  };
}

function copyProfile(profile: AgentProfile): AgentProfile {
  return {
    ...profile,
    id: `${profile.id}-copy`,
    display_name: `${profile.display_name} Copy`,
    source: "user",
    version: 1,
  };
}

function prettyJson(value: Record<string, unknown> | undefined) {
  return JSON.stringify(value ?? {}, null, 2);
}

function parseJsonObject(value: string, label: string) {
  const parsed = JSON.parse(value || "{}") as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

function Page({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-normal">{title}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
      </div>
      {children}
    </div>
  );
}

function statusLine(statuses?: Record<string, number>) {
  if (!statuses) {
    return "-";
  }
  const text = Object.entries(statuses)
    .map(([status, count]) => `${status} ${count}`)
    .join(" / ");
  return text || "none";
}

function formatBytes(value: number) {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function timeAgo(value?: string) {
  if (!value) {
    return "-";
  }
  const delta = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(delta)) {
    return value;
  }
  const seconds = Math.max(0, Math.round(delta / 1000));
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  return `${Math.round(minutes / 60)}h ago`;
}

function emptyToNull(value: string) {
  return value.trim() ? value.trim() : null;
}

function mergeEvents(current: RuntimeEvent[], incoming: RuntimeEvent[]) {
  if (!incoming.length) {
    return current;
  }
  const bySequence = new Map<number, RuntimeEvent>();
  for (const event of [...current, ...incoming]) {
    bySequence.set(event.sequence, event);
  }
  const merged = [...bySequence.values()].sort(
    (left, right) => left.sequence - right.sequence,
  );
  if (
    merged.length === current.length &&
    merged.every((event, index) => event.id === current[index]?.id)
  ) {
    return current;
  }
  return merged;
}

function runnerTranscript(events: RuntimeEvent[]): RunnerTranscriptItem[] {
  const items: RunnerTranscriptItem[] = [];
  const agentMessages = new Map<string, RunnerTranscriptItem>();

  for (const event of events) {
    if (event.type === "message.delta") {
      const promptNumber = stringValue(event.data.prompt_number) ?? "current";
      const key = `agent-${promptNumber}`;
      const text = stringValue(event.data.text) ?? "";
      const existing = agentMessages.get(key);
      if (existing) {
        existing.body = `${existing.body}${text}`;
        existing.sequence = event.sequence;
        existing.created_at = event.created_at;
      } else {
        const item: RunnerTranscriptItem = {
          id: key,
          role: "agent",
          title:
            `Agent output ${promptNumber === "current" ? "" : `#${promptNumber}`}`.trim(),
          body: text,
          created_at: event.created_at,
          event_type: event.type,
          sequence: event.sequence,
        };
        agentMessages.set(key, item);
        items.push(item);
      }
      continue;
    }

    const item = transcriptItemForEvent(event);
    if (item) {
      items.push(item);
    }
  }

  return items.sort((left, right) => left.sequence - right.sequence);
}

function transcriptItemForEvent(
  event: RuntimeEvent,
): RunnerTranscriptItem | null {
  const base = {
    id: event.id,
    created_at: event.created_at,
    event_type: event.type,
    sequence: event.sequence,
  };
  switch (event.type) {
    case "run.created":
      return {
        ...base,
        role: "system",
        title: "Run accepted",
        body: "The control plane created the run and stored its request.",
      };
    case "workspace.prepared":
      return {
        ...base,
        role: "system",
        title: "Workspace ready",
        body: `${stringValue(event.data.strategy) ?? "workspace"} · ${stringValue(event.data.path) ?? "prepared"}`,
      };
    case "resources.resolved":
      return {
        ...base,
        role: "system",
        title: "Resources assigned",
        body: compactJson(event.data),
      };
    case "run.queued":
      return {
        ...base,
        role: "system",
        title: "Queued",
        body: "Waiting for an available runner.",
      };
    case "lease.claimed":
      return {
        ...base,
        role: "system",
        title: "Runner claimed",
        body: `Worker ${stringValue(event.data.worker_id) ?? "unknown"} started the lease.`,
      };
    case "run.started":
      return {
        ...base,
        role: "success",
        title: "Runner started",
        body:
          stringValue(event.data.workspace) ??
          stringValue(event.data.adapter) ??
          "Session is active.",
      };
    case "input.accepted":
      return {
        ...base,
        role: "operator",
        title: "Prompt submitted",
        body:
          stringValue(event.data.prompt_preview) ??
          `Prompt #${event.data.prompt_number ?? 1}`,
      };
    case "step.started":
      return {
        ...base,
        role: "system",
        title: "Step started",
        body: stepBody(event),
      };
    case "step.submitted":
      return {
        ...base,
        role: "system",
        title: "Prompt accepted by runner",
        body: stepBody(event),
      };
    case "step.completed":
      return {
        ...base,
        role: "success",
        title: "Step completed",
        body: stepBody(event),
      };
    case "permission.requested":
      return {
        ...base,
        role: "warning",
        title: "Permission required",
        body: permissionBody(event),
      };
    case "permission.resolved":
      return {
        ...base,
        role: "success",
        title: "Permission resolved",
        body: `Decision: ${stringValue(event.data.decision) ?? "recorded"}`,
      };
    case "permission.stalled":
      return {
        ...base,
        role: "warning",
        title: "Permission stalled",
        body: compactJson(event.data),
      };
    case "adapter.event":
      return {
        ...base,
        role: toolEventRole(event),
        title: "Tool event",
        body: toolEventBody(event),
      };
    case "stream.warning":
    case "cancel.warning":
      return {
        ...base,
        role: "warning",
        title: "Runner warning",
        body: compactJson(event.data),
      };
    case "run.completed":
      return {
        ...base,
        role: "success",
        title: "Run completed",
        body:
          stringValue(event.data.final_artifact) ??
          "The runner reached a terminal success state.",
      };
    case "run.failed":
      return {
        ...base,
        role: "error",
        title: "Run failed",
        body: failureBody(event),
      };
    case "run.cancelled":
      return {
        ...base,
        role: "warning",
        title: "Run cancelled",
        body: compactJson(event.data),
      };
    default:
      if (event.type.endsWith(".failed") || event.type.includes("error")) {
        return {
          ...base,
          role: "error",
          title: event.type,
          body: compactJson(event.data),
        };
      }
      return null;
  }
}

function stepBody(event: RuntimeEvent) {
  return `Prompt #${event.data.prompt_number ?? "current"}`;
}

function permissionBody(event: RuntimeEvent) {
  const request = extractPermissionRequest(event);
  return request?.prompt ?? request?.tool ?? compactJson(event.data);
}

function failureBody(event: RuntimeEvent) {
  return stringValue(event.data.reason) ?? compactJson(event.data);
}

function toolEventRole(event: RuntimeEvent): RunnerTranscriptItem["role"] {
  const status =
    stringValue(event.data.status) ?? stringValue(event.data.outcome);
  const exitCode = event.data.exit_code;
  if (status === "failed" || exitCode === 1) {
    return "error";
  }
  return "system";
}

function toolEventBody(event: RuntimeEvent) {
  const command =
    stringValue(event.data.command) ??
    stringValue(event.data.tool) ??
    stringValue(event.data.name) ??
    "adapter event";
  const cwd = stringValue(event.data.cwd);
  const exitCode =
    typeof event.data.exit_code === "number"
      ? `exit ${event.data.exit_code}`
      : undefined;
  const stdout = stringValue(event.data.stdout);
  const stderr = stringValue(event.data.stderr);
  return [
    command,
    cwd ? `cwd: ${cwd}` : undefined,
    exitCode,
    stdout ? `stdout: ${stdout.slice(0, 800)}` : undefined,
    stderr ? `stderr: ${stderr.slice(0, 800)}` : undefined,
  ]
    .filter(Boolean)
    .join("\n");
}

function filterTranscript(
  transcript: RunnerTranscriptItem[],
  filter: RunnerFilter,
) {
  if (filter === "all") {
    return transcript;
  }
  if (filter === "permission") {
    return transcript.filter((item) =>
      item.event_type.startsWith("permission."),
    );
  }
  if (filter === "warning") {
    return transcript.filter((item) => item.role === "warning");
  }
  if (filter === "error") {
    return transcript.filter((item) => item.role === "error");
  }
  return transcript.filter((item) => item.role === "agent");
}

function filterLabel(filter: RunnerFilter) {
  const labels: Record<RunnerFilter, string> = {
    agent: "Agent",
    all: "All",
    error: "Errors",
    permission: "Permissions",
    warning: "Warnings",
  };
  return labels[filter];
}

function runnerSignal(latest?: RuntimeEvent, runStatus?: string) {
  if (isTerminal(runStatus)) {
    return { label: "terminal", tone: "neutral" as const };
  }
  if (!latest) {
    return { label: "waiting", tone: "neutral" as const };
  }
  const ageMs = Date.now() - new Date(latest.created_at).getTime();
  if (Number.isFinite(ageMs) && ageMs > 120_000) {
    return { label: "stalled", tone: "warn" as const };
  }
  return { label: "active", tone: "ok" as const };
}

function runnerReadableReport(
  transcript: RunnerTranscriptItem[],
  events: RuntimeEvent[],
) {
  const lines = [
    "# Runner Execution Report",
    "",
    `Generated: ${new Date().toISOString()}`,
    `Events: ${events.length}`,
    "",
    "## Timeline",
    "",
  ];
  for (const item of transcript) {
    lines.push(
      `### ${item.sequence}. ${item.title}`,
      "",
      `Event: ${item.event_type}`,
      `Time: ${item.created_at}`,
      "",
      item.body || "-",
      "",
    );
  }
  return lines.join("\n");
}

function downloadText(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function compactJson(value: unknown) {
  if (!value || typeof value !== "object") {
    return String(value ?? "");
  }
  return JSON.stringify(value, null, 2);
}

function registryValue(source: Record<string, unknown>, key: string) {
  const value = source[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, unknown>;
}

function money(value?: number | null) {
  if (value == null || !Number.isFinite(value)) {
    return "$0.00";
  }
  return `$${value.toFixed(2)}`;
}

function stringValue(value: unknown) {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number") {
    return String(value);
  }
  return undefined;
}

function connectionTone(status: LiveConnectionStatus) {
  if (status === "live") {
    return "ok";
  }
  if (status === "reconnecting" || status === "fallback") {
    return "warn";
  }
  return "neutral";
}

function connectionLabel(status: LiveConnectionStatus) {
  const labels: Record<LiveConnectionStatus, string> = {
    closed: "closed",
    connecting: "connecting",
    fallback: "polling",
    live: "live",
    reconnecting: "reconnecting",
  };
  return labels[status];
}

function bubbleClass(role: RunnerTranscriptItem["role"]) {
  const classes: Record<RunnerTranscriptItem["role"], string> = {
    agent: "border-primary/30 bg-background",
    error: "border-destructive/30 bg-destructive/10 text-destructive",
    operator: "border-sky-500/30 bg-sky-500/10",
    success: "border-success/30 bg-success/10",
    system: "border-border bg-card",
    warning: "border-warning/30 bg-warning/10",
  };
  return classes[role];
}

function isTerminalEvent(eventType: string) {
  return ["run.completed", "run.failed", "run.cancelled"].includes(eventType);
}

function isTerminal(status?: string) {
  return Boolean(
    status && ["completed", "failed", "cancelled"].includes(status),
  );
}

export const __testUtils = {
  bubbleClass,
  connectionLabel,
  connectionTone,
  compactJson,
  copyProfile,
  downloadText,
  emptyProfile,
  emptyToNull,
  filterLabel,
  filterTranscript,
  formatBytes,
  isTerminalEvent,
  mergeEvents,
  parseJsonObject,
  prettyJson,
  runnerReadableReport,
  runnerSignal,
  runnerTranscript,
  stringValue,
  toolEventBody,
  toolEventRole,
  isTerminal,
  statusLine,
  timeAgo,
};
