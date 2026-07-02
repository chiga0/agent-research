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
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { LanguageToggle, Shell } from "./components/shell";
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
  type WorkerInfo,
  type WorkerRegistration,
} from "./lib/api";
import { LanguageProvider, useI18n } from "./lib/i18n";
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
const unitsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/units",
  component: UnitsPage,
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
  unitsRoute,
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
      <LanguageProvider>
        <AuthGate />
      </LanguageProvider>
    </QueryClientProvider>
  );
}

function AuthGate() {
  const session = useQuery({
    queryKey: ["auth", "session"],
    queryFn: runtimeApi.session,
    refetchInterval: false,
    retry: false,
  });

  if (session.isPending) {
    return (
      <div className="grid min-h-screen place-items-center bg-background px-4">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-border border-t-primary" />
      </div>
    );
  }

  if (!session.data?.authenticated) {
    return <LoginPage />;
  }

  return <RouterProvider router={router} />;
}

function LoginPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const [username, setUsername] = useState("cloudagents");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: runtimeApi.login,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["auth", "session"] });
    },
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    login.mutate({ username, password });
  };

  return (
    <div className="min-h-screen bg-background px-4 py-6 text-foreground sm:px-6 lg:px-8">
      <div className="mx-auto flex max-w-5xl justify-end">
        <LanguageToggle />
      </div>
      <div className="mx-auto grid min-h-[calc(100vh-3rem)] w-full max-w-5xl items-center gap-6 lg:grid-cols-[minmax(0,1fr)_420px]">
        <section className="grid gap-6">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-md bg-primary text-primary-foreground">
              <ShieldCheck className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h1 className="text-2xl font-semibold tracking-normal sm:text-3xl">
                {t("nav.title")}
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {t("nav.subtitle")}
              </p>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <Metric
              label={t("login.ingress")}
              value={t("login.ingressValue")}
              detail={t("login.ingressDetail")}
            />
            <Metric
              label={t("login.scope")}
              value={t("login.scopeValue")}
              detail={t("login.scopeDetail")}
            />
            <Metric
              label={t("login.workers")}
              value={t("login.workersValue")}
              detail={t("login.workersDetail")}
            />
          </div>
        </section>

        <Card className="w-full">
          <CardHeader className="grid gap-1">
            <div className="flex items-center gap-2">
              <KeyRound className="h-4 w-4 text-primary" />
              <CardTitle>{t("login.title")}</CardTitle>
            </div>
            <p className="text-sm text-muted-foreground">
              {t("login.subtitle")}
            </p>
          </CardHeader>
          <CardBody>
            <form className="grid gap-4" onSubmit={submit}>
              <Field label={t("login.username")}>
                <Input
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                />
              </Field>
              <Field label={t("login.password")}>
                <Input
                  autoComplete="current-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </Field>
              {login.isError ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                  {t("login.error")}
                </div>
              ) : null}
              <Button
                className="h-11 w-full"
                disabled={login.isPending || !username || !password}
                type="submit"
                variant="primary"
              >
                <KeyRound className="h-4 w-4" />
                {login.isPending ? t("login.signingIn") : t("login.signIn")}
              </Button>
            </form>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function OverviewPage() {
  const { t } = useI18n();
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
    <Page title={t("overview.title")} subtitle={t("overview.subtitle")}>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric
          label={t("overview.runtime")}
          value={health.data?.ok ? t("common.healthy") : t("common.checking")}
          detail={health.data?.version}
        />
        <Metric
          label={t("overview.runs")}
          value={metrics.data?.runs.total ?? "-"}
          detail={statusLine(metrics.data?.runs.by_status)}
        />
        <Metric
          label={t("overview.missions")}
          value={metrics.data?.missions.total ?? "-"}
          detail={statusLine(metrics.data?.missions.by_status)}
        />
        <Metric
          label={t("overview.permissions")}
          value={metrics.data?.permissions.pending ?? "-"}
          detail={`${metrics.data?.permissions.stalled ?? 0} ${t("overview.stalledSuffix")}`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <Card>
          <CardHeader>
            <CardTitle>{t("overview.queue")}</CardTitle>
            <Badge tone={metrics.data?.queue.stale_workers ? "warn" : "ok"}>
              {metrics.data?.queue.active_workers ?? 0}{" "}
              {t("overview.activeSuffix")}
            </Badge>
          </CardHeader>
          <CardBody className="grid gap-3 md:grid-cols-3">
            <Metric
              label={t("overview.queued")}
              value={metrics.data?.queue.counts.queued ?? 0}
            />
            <Metric
              label={t("overview.running")}
              value={metrics.data?.queue.counts.running ?? 0}
            />
            <Metric
              label={t("overview.staleWorkers")}
              value={metrics.data?.queue.stale_workers ?? 0}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{t("overview.adapters")}</CardTitle>
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
  const { t } = useI18n();
  const runs = useQuery({ queryKey: ["runs"], queryFn: runtimeApi.runs });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: runtimeApi.capabilities,
  });
  return (
    <Page title={t("runs.title")} subtitle={t("runs.subtitle")}>
      <div className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
        <CreateRunForm
          adapters={Object.keys(capabilities.data?.adapters ?? { fake: {} })}
        />
        <Card>
          <CardHeader>
            <CardTitle>{t("runs.history")}</CardTitle>
            <Button size="sm" variant="ghost" onClick={() => runs.refetch()}>
              <RefreshCw className="h-4 w-4" />
              {t("common.refresh")}
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

function UnitsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const workers = useQuery({
    queryKey: ["workers"],
    queryFn: runtimeApi.workers,
  });
  const [registration, setRegistration] = useState<WorkerRegistration | null>(
    null,
  );
  const drain = useMutation({
    mutationFn: (workerId: string) => runtimeApi.drainWorker(workerId),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: ["workers"] }),
  });
  const resume = useMutation({
    mutationFn: runtimeApi.resumeWorker,
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: ["workers"] }),
  });
  const retry = useMutation({
    mutationFn: runtimeApi.retryWorkerRuns,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["workers"] });
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const workerList = workers.data?.workers ?? [];
  const active = workerList.filter(
    (worker) => worker.status === "active",
  ).length;
  const draining = workerList.filter(
    (worker) => worker.status === "draining",
  ).length;
  const stale = workerList.filter((worker) => worker.status === "stale").length;
  return (
    <Page title={t("units.title")} subtitle={t("units.subtitle")}>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric
          label={t("units.title")}
          value={workerList.length}
          detail={t("units.localRemote")}
        />
        <Metric
          label={t("common.active")}
          value={active}
          detail={t("units.activeDetail")}
        />
        <Metric
          label={t("units.draining")}
          value={draining}
          detail={t("units.drainingDetail")}
        />
        <Metric
          label={t("common.stale")}
          value={stale}
          detail={t("units.staleDetail")}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
        <WorkerRegistrationForm onCreated={setRegistration} />
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-primary" />
              <CardTitle>{t("units.executionUnits")}</CardTitle>
            </div>
            <Button size="sm" variant="ghost" onClick={() => workers.refetch()}>
              <RefreshCw className="h-4 w-4" />
              {t("common.refresh")}
            </Button>
          </CardHeader>
          <CardBody>
            <WorkerList
              workers={workerList}
              onDrain={(workerId) => drain.mutate(workerId)}
              onResume={(workerId) => resume.mutate(workerId)}
              onRetry={(workerId) => retry.mutate(workerId)}
            />
          </CardBody>
        </Card>
      </div>
      {registration ? (
        <WorkerRegistrationResult registration={registration} />
      ) : null}
    </Page>
  );
}

function WorkerRegistrationForm({
  onCreated,
}: {
  onCreated: (registration: WorkerRegistration) => void;
}) {
  const { t } = useI18n();
  const [error, setError] = useState<string | null>(null);
  const createRegistration = useMutation({
    mutationFn: runtimeApi.createWorkerRegistration,
    onSuccess: (result) => {
      setError(null);
      onCreated(result);
    },
    onError: (err) => setError(String(err)),
  });
  const form = useForm({
    defaultValues: {
      worker_id: "hk-2c2g-a",
      control_url: defaultWorkerControlUrl(),
      capacity: 1,
      region: "hk",
      cpus: 2,
      memory_gb: 2,
    },
    onSubmit: async ({ value }) => {
      await createRegistration.mutateAsync({
        worker_id: value.worker_id,
        control_url: value.control_url,
        capacity: Number(value.capacity) || 1,
        labels: { region: value.region },
        resources: {
          cpus: Number(value.cpus) || 2,
          memory_gb: Number(value.memory_gb) || 2,
        },
      });
    },
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("units.register")}</CardTitle>
        <Badge tone="info">{t("units.oneTimeToken")}</Badge>
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
          <form.Field name="worker_id">
            {(field) => (
              <Field label={t("units.unitId")}>
                <Input
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.value)}
                />
              </Field>
            )}
          </form.Field>
          <form.Field name="control_url">
            {(field) => (
              <Field label={t("units.workerControlUrl")}>
                <Input
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.value)}
                />
              </Field>
            )}
          </form.Field>
          <div className="grid gap-3 md:grid-cols-3">
            <form.Field name="capacity">
              {(field) => (
                <Field label={t("common.capacity")}>
                  <Input
                    min={1}
                    type="number"
                    value={field.state.value}
                    onChange={(event) =>
                      field.handleChange(Number(event.target.value))
                    }
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="cpus">
              {(field) => (
                <Field label={t("units.cpUs")}>
                  <Input
                    min={1}
                    type="number"
                    value={field.state.value}
                    onChange={(event) =>
                      field.handleChange(Number(event.target.value))
                    }
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="memory_gb">
              {(field) => (
                <Field label={t("units.memoryGb")}>
                  <Input
                    min={1}
                    type="number"
                    value={field.state.value}
                    onChange={(event) =>
                      field.handleChange(Number(event.target.value))
                    }
                  />
                </Field>
              )}
            </form.Field>
          </div>
          <form.Field name="region">
            {(field) => (
              <Field label={t("units.region")}>
                <Input
                  value={field.state.value}
                  onChange={(event) => field.handleChange(event.target.value)}
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
            disabled={createRegistration.isPending}
            type="submit"
            variant="primary"
          >
            <KeyRound className="h-4 w-4" />
            {t("common.generate")}
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function WorkerList({
  workers,
  onDrain,
  onResume,
  onRetry,
}: {
  workers: WorkerInfo[];
  onDrain: (workerId: string) => void;
  onResume: (workerId: string) => void;
  onRetry: (workerId: string) => void;
}) {
  const { t } = useI18n();
  if (!workers.length) {
    return (
      <EmptyState
        title={t("units.noUnits")}
        detail={t("units.noUnitsDetail")}
      />
    );
  }
  return (
    <div className="grid gap-2">
      {workers.map((worker) => (
        <div
          key={worker.worker_id}
          className="grid gap-3 rounded-md border border-border p-3 xl:grid-cols-[minmax(0,1fr)_160px_220px]"
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate font-mono text-sm">
                {worker.worker_id}
              </span>
              <StatusBadge status={worker.status} />
              <Badge tone="neutral">
                {stringValue(worker.metadata?.kind ?? "local")}
              </Badge>
            </div>
            <div className="mt-2 grid gap-1 text-xs text-muted-foreground md:grid-cols-2">
              <span>
                {t("units.heartbeat")} {timeAgo(worker.heartbeat_at)}
              </span>
              <span>
                {t("units.leaseTtl")} {worker.lease_ttl_seconds}s
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {workerBadges(worker).map((badge) => (
                <Badge key={badge} tone="neutral">
                  {badge}
                </Badge>
              ))}
            </div>
          </div>
          <div className="grid content-start gap-2">
            <Metric
              label={t("common.capacity")}
              value={`${worker.active_count}/${worker.capacity}`}
            />
          </div>
          <div className="flex flex-wrap content-start justify-start gap-2 xl:justify-end">
            <Button
              disabled={worker.status === "draining"}
              size="sm"
              onClick={() => onDrain(worker.worker_id)}
            >
              <PauseCircle className="h-4 w-4" />
              {t("units.drain")}
            </Button>
            <Button
              disabled={worker.status === "active"}
              size="sm"
              onClick={() => onResume(worker.worker_id)}
            >
              <Play className="h-4 w-4" />
              {t("units.resume")}
            </Button>
            <Button
              disabled={worker.active_count === 0}
              size="sm"
              variant="danger"
              onClick={() => onRetry(worker.worker_id)}
            >
              <RefreshCw className="h-4 w-4" />
              {t("units.retry")}
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

function WorkerRegistrationResult({
  registration,
}: {
  registration: WorkerRegistration;
}) {
  const { t } = useI18n();
  return (
    <Card className="border-warning/40">
      <CardHeader>
        <div>
          <CardTitle>{t("units.deploymentCommand")}</CardTitle>
          <div className="mt-1 text-xs text-muted-foreground">
            {t("units.tokenDetail")}
          </div>
        </div>
        <Button size="sm" onClick={() => copyText(registration.deploy_command)}>
          <Copy className="h-4 w-4" />
          {t("common.copy")}
        </Button>
      </CardHeader>
      <CardBody className="grid gap-3">
        <div className="grid gap-3 md:grid-cols-3">
          <Metric label={t("units.unit")} value={registration.worker_id} />
          <Metric label={t("common.capacity")} value={registration.capacity} />
          <Metric
            label={t("common.token")}
            value={registration.token.token_prefix}
          />
        </div>
        <pre className="max-h-[320px] overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
          {registration.deploy_command}
        </pre>
      </CardBody>
    </Card>
  );
}

function ExecutorsPage() {
  const { t } = useI18n();
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
    <Page title={t("executors.title")} subtitle={t("executors.subtitle")}>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric
          label={t("common.strategy")}
          value={stringValue(config.strategy ?? "shared")}
          detail={
            config.enabled
              ? t("executors.registryEnabled")
              : t("executors.sharedEndpoint")
          }
        />
        <Metric
          label={t("common.active")}
          value={activeCount}
          detail={t("executors.activeDetail")}
        />
        <Metric
          label={t("common.failed")}
          value={failedCount}
          detail={t("executors.failedDetail")}
        />
        <Metric
          label={t("executors.container")}
          value={stringValue(config.container_image ?? "-")}
          detail={stringValue(config.container_network ?? "bridge")}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-primary" />
              <CardTitle>{t("executors.leases")}</CardTitle>
            </div>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => executors.refetch()}
            >
              <RefreshCw className="h-4 w-4" />
              {t("common.refresh")}
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
              <CardTitle>{t("executors.registry")}</CardTitle>
            </div>
            <Badge
              tone={
                capabilities.data?.features.includes("executor_registry")
                  ? "ok"
                  : "neutral"
              }
            >
              {stringValue(config.strategy ?? "shared")}
            </Badge>
          </CardHeader>
          <CardBody className="grid gap-3">
            <ProfileJson label={t("common.config")} value={config} />
            <ProfileJson label={t("common.counts")} value={counts} />
          </CardBody>
        </Card>
      </div>
    </Page>
  );
}

function ExecutorLeaseList({ leases }: { leases: ExecutorLease[] }) {
  const { t } = useI18n();
  if (!leases.length) {
    return <EmptyState title={t("executors.noLeases")} />;
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
  const { t } = useI18n();
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
        <CardTitle>{t("runs.create")}</CardTitle>
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
              <Field label={t("common.adapter")}>
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
              <Field label={t("common.prompt")}>
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
                <Field label={t("common.repo")}>
                  <Input
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="workspace">
              {(field) => (
                <Field label={t("common.workspace")}>
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
              <Field label={t("runs.timeout")}>
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
            {t("common.start")}
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function RunDetailPage() {
  const { t } = useI18n();
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
    <Page title={t("runs.detail")} subtitle={runId}>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid gap-4">
          <Card>
            <CardHeader>
              <CardTitle>{t("runs.state")}</CardTitle>
              <div className="flex gap-2">
                {run.data ? <StatusBadge status={run.data.status} /> : null}
                <Button
                  disabled={cancel.isPending || isTerminal(run.data?.status)}
                  size="sm"
                  onClick={() => cancel.mutate()}
                >
                  <PauseCircle className="h-4 w-4" />
                  {t("common.cancel")}
                </Button>
              </div>
            </CardHeader>
            <CardBody className="grid gap-3 md:grid-cols-4">
              <Metric
                label={t("common.adapter")}
                value={run.data?.spec.adapter ?? "-"}
              />
              <Metric
                label={t("common.events")}
                value={run.data?.event_count ?? "-"}
              />
              <Metric
                label={t("runs.inputs")}
                value={run.data?.prompt_count ?? "-"}
              />
              <Metric
                label={t("common.updated")}
                value={timeAgo(run.data?.updated_at)}
              />
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
              <CardTitle>{t("common.downloads")}</CardTitle>
            </CardHeader>
            <CardBody className="grid gap-2">
              <LinkButton href={artifactHref(runId, "events.jsonl")}>
                <Download className="h-4 w-4" />
                {t("common.eventsJsonl")}
              </LinkButton>
              <LinkButton href={artifactHref(runId, "diagnostics.json")}>
                <Download className="h-4 w-4" />
                {t("common.diagnostics")}
              </LinkButton>
              <LinkButton href={auditHref(runId)}>
                <Download className="h-4 w-4" />
                {t("common.auditBundle")}
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
  const { t } = useI18n();
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
          <CardTitle>{t("live.title")}</CardTitle>
        </div>
        <Badge tone={connectionTone(connectionStatus)}>
          <Radio className="h-4 w-4" />
          {connectionLabel(connectionStatus)}
        </Badge>
      </CardHeader>
      <CardBody className="grid gap-4">
        <div className="grid gap-3 md:grid-cols-3">
          <Metric
            label={t("live.runStatus")}
            value={runStatus ?? t("common.loading")}
          />
          <Metric label={t("live.lastEvent")} value={latest?.type ?? "-"} />
          <Metric
            label={t("live.runnerSignal")}
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
                  {filterLabel(item, t)}
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
            {t("live.noRecentEvent")}
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
            <EmptyState title={t("live.waiting")} detail={t("live.subtitle")} />
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
  const { t } = useI18n();
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
        <CardTitle>{t("runs.permissionRequests")}</CardTitle>
        <Badge tone="warn">
          {pending.length} {t("runs.permissionPending")}
        </Badge>
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
  const { t } = useI18n();
  const missions = useQuery({
    queryKey: ["missions"],
    queryFn: runtimeApi.missions,
  });
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: runtimeApi.capabilities,
  });
  return (
    <Page title={t("missions.title")} subtitle={t("missions.subtitle")}>
      <div className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
        <CreateMissionForm
          adapters={Object.keys(capabilities.data?.adapters ?? { fake: {} })}
        />
        <Card>
          <CardHeader>
            <CardTitle>{t("missions.history")}</CardTitle>
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
  const { t } = useI18n();
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
    <Page title={t("missions.detail")} subtitle={missionId}>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid gap-4">
          <Card>
            <CardHeader>
              <div className="min-w-0">
                <CardTitle>{t("missions.state")}</CardTitle>
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                  {state?.spec.goal ?? t("missions.loadingGoal")}
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
                  {t("common.cancel")}
                </Button>
              </div>
            </CardHeader>
            <CardBody className="grid gap-3 md:grid-cols-4">
              <Metric
                label={t("common.strategy")}
                value={state?.spec.strategy ?? "-"}
              />
              <Metric
                label={t("common.adapter")}
                value={state?.spec.adapter ?? "-"}
              />
              <Metric
                label={t("common.progress")}
                value={`${state?.completed_task_count ?? 0}/${state?.task_count ?? 0}`}
              />
              <Metric
                label={t("common.events")}
                value={state?.event_count ?? "-"}
              />
            </CardBody>
          </Card>
          {state?.status === "blocked" ? (
            <Card className="border-warning/40">
              <CardHeader>
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-warning" />
                  <CardTitle>{t("missions.reviewBlocked")}</CardTitle>
                </div>
                <Badge tone="warn">{t("missions.reviewDecision")}</Badge>
              </CardHeader>
              <CardBody className="flex flex-wrap gap-2">
                <Button
                  disabled={override.isPending}
                  size="sm"
                  variant="primary"
                  onClick={() => override.mutate("approve")}
                >
                  {t("missions.approveGate")}
                </Button>
                <Button
                  disabled={override.isPending}
                  size="sm"
                  variant="danger"
                  onClick={() => override.mutate("deny")}
                >
                  {t("missions.denyGate")}
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
              <CardTitle>{t("common.downloads")}</CardTitle>
            </CardHeader>
            <CardBody className="grid gap-2">
              <LinkButton
                href={missionArtifactHref(missionId, "manifest.json")}
              >
                <Download className="h-4 w-4" />
                {t("common.manifest")}
              </LinkButton>
              <LinkButton href={missionArtifactHref(missionId, "events.jsonl")}>
                <Download className="h-4 w-4" />
                {t("common.eventsJsonl")}
              </LinkButton>
              <LinkButton
                href={missionArtifactHref(missionId, "final-report.md")}
              >
                <Download className="h-4 w-4" />
                {t("common.finalReport")}
              </LinkButton>
            </CardBody>
          </Card>
        </div>
      </div>
    </Page>
  );
}

function CreateMissionForm({ adapters }: { adapters: string[] }) {
  const { t } = useI18n();
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
        <CardTitle>{t("missions.create")}</CardTitle>
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
              <Field label={t("common.goal")}>
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
                <Field label={t("common.strategy")}>
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
                <Field label={t("common.adapter")}>
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
            {t("common.start")}
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function ProfilesPage() {
  const { t } = useI18n();
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: runtimeApi.profiles,
  });
  const [draft, setDraft] = useState<AgentProfile | null>(null);
  return (
    <Page title={t("profiles.title")} subtitle={t("profiles.subtitle")}>
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
                    {t("common.copy")}
                  </Button>
                  <Button size="sm" onClick={() => setDraft(profile)}>
                    <UserCog className="h-4 w-4" />
                    {t("common.edit")}
                  </Button>
                </div>
                <ProfileJson
                  label={t("profiles.runtime")}
                  value={profile.runtime}
                />
                <ProfileJson
                  label={t("profiles.tools")}
                  value={profile.tools}
                />
                <ProfileJson
                  label={t("profiles.approval")}
                  value={profile.approval}
                />
                <ProfileJson
                  label={t("profiles.limits")}
                  value={profile.limits}
                />
                <ProfileJson
                  label={t("profiles.workspace")}
                  value={profile.workspace}
                />
                <ProfileJson
                  label={t("profiles.artifacts")}
                  value={profile.artifacts}
                />
              </CardBody>
            </Card>
          ))}
        </div>
      </div>
    </Page>
  );
}

function AccessPage() {
  const { t } = useI18n();
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
    <Page title={t("access.title")} subtitle={t("access.subtitle")}>
      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4 text-primary" />
              <CardTitle>{t("access.currentPrincipal")}</CardTitle>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge tone="info">{policy.data?.mode ?? "loading"}</Badge>
              <Button
                disabled={!policy.data}
                size="sm"
                onClick={() => downloadJson("access-policy.json", policy.data)}
              >
                <Download className="h-4 w-4" />
                {t("access.export")}
              </Button>
            </div>
          </CardHeader>
          <CardBody className="grid gap-3">
            <Metric
              label={t("access.identity")}
              value={principal?.display_name ?? "-"}
            />
            <Metric
              label={t("access.roles")}
              value={principal?.roles.join(", ") || "-"}
            />
            <ProfileJson
              label={t("access.auditPosture")}
              value={policy.data?.audit ?? {}}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{t("access.roleMatrix")}</CardTitle>
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
          <CardTitle>{t("access.scopes")}</CardTitle>
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
              <CardTitle>{t("access.projects")}</CardTitle>
            </div>
            <Badge tone="neutral">{projects.data?.projects.length ?? 0}</Badge>
          </CardHeader>
          <CardBody className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
              <Field label={t("access.projectId")}>
                <Input
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                />
              </Field>
              <Field label={t("profiles.displayName")}>
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
                {t("common.create")}
              </Button>
            </div>
            <AccessProjectList
              projects={projects.data?.projects ?? policy.data?.projects ?? []}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <KeyRound className="h-4 w-4 text-primary" />
              <CardTitle>{t("access.apiTokens")}</CardTitle>
            </div>
            <Badge tone="neutral">{tokens.data?.tokens.length ?? 0}</Badge>
          </CardHeader>
          <CardBody className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
              <Field label={t("access.tokenName")}>
                <Input
                  value={tokenName}
                  onChange={(event) => setTokenName(event.target.value)}
                />
              </Field>
              <Field label={t("access.projectId")}>
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
                {t("common.create")}
              </Button>
            </div>
            {createdToken ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-3">
                <div className="text-sm font-medium">
                  {t("access.newToken")}
                </div>
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
  const { t } = useI18n();
  if (!projects.length) {
    return <EmptyState title={t("access.noProjects")} />;
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
  const { t } = useI18n();
  if (!tokens.length) {
    return <EmptyState title={t("access.noApiTokens")} />;
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
            {t("access.revoke")}
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
  const { t } = useI18n();
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
        <CardTitle>{t("profiles.editor")}</CardTitle>
        <Badge tone="info">{t("profiles.versioned")}</Badge>
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
                <Field label={t("profiles.id")}>
                  <Input
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                  />
                </Field>
              )}
            </form.Field>
            <form.Field name="display_name">
              {(field) => (
                <Field label={t("profiles.displayName")}>
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
              <Field label={t("profiles.description")}>
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
            {t("profiles.save")}
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

function OperationsPage() {
  const { t } = useI18n();
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
    <Page title={t("operations.title")} subtitle={t("operations.subtitle")}>
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <Card>
          <CardHeader>
            <CardTitle>{t("operations.drills")}</CardTitle>
            <Button
              size="sm"
              variant="primary"
              onClick={() => runDrills.mutate()}
            >
              <ShieldCheck className="h-4 w-4" />
              {t("operations.runDrills")}
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
            <CardTitle>{t("operations.backups")}</CardTitle>
            <Button
              disabled={createBackup.isPending}
              size="sm"
              onClick={() => createBackup.mutate()}
            >
              <Download className="h-4 w-4" />
              {t("operations.createBackup")}
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
              <EmptyState title={t("operations.noBackups")} />
            ) : null}
          </CardBody>
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <CostBudgetPanel cost={cost.data} />
        <Card>
          <CardHeader>
            <CardTitle>{t("operations.p5Evaluations")}</CardTitle>
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
            <CardTitle>{t("operations.runtimeStatus")}</CardTitle>
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
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <WalletCards className="h-4 w-4 text-primary" />
          <CardTitle>{t("operations.costBudget")}</CardTitle>
        </div>
        <StatusBadge status={cost?.status ?? "loading"} />
      </CardHeader>
      <CardBody className="grid gap-3 md:grid-cols-3">
        <Metric
          label={t("common.month")}
          value={cost?.month ?? "-"}
          detail="UTC"
        />
        <Metric
          label={t("common.estimated")}
          value={money(cost?.monthly_estimated_cost_usd)}
          detail={`${cost?.runs.length ?? 0} runs`}
        />
        <Metric
          label={t("common.budget")}
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
  const { t } = useI18n();
  if (!runs.length) {
    return (
      <EmptyState title={t("runs.noRuns")} detail={t("runs.noRunsDetail")} />
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
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("overview.recentRuns")}</CardTitle>
        <Link className="text-sm text-primary" to="/runs">
          {t("overview.viewAll")}
        </Link>
      </CardHeader>
      <CardBody>
        <RunList runs={runs.slice(0, 5)} />
      </CardBody>
    </Card>
  );
}

function MissionList({ missions }: { missions: MissionState[] }) {
  const { t } = useI18n();
  if (!missions.length) {
    return (
      <EmptyState
        title={t("missions.noMissions")}
        detail={t("missions.noMissionsDetail")}
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
              {t("missions.openDetail")}
            </Link>
            <LinkButton
              href={missionArtifactHref(mission.mission_id, "manifest.json")}
              size="sm"
            >
              <Download className="h-4 w-4" />
              {t("common.manifest")}
            </LinkButton>
            <LinkButton
              href={missionArtifactHref(mission.mission_id, "final-report.md")}
              size="sm"
            >
              <Download className="h-4 w-4" />
              {t("missions.report")}
            </LinkButton>
          </div>
        </div>
      ))}
    </div>
  );
}

function MissionDagPanel({ mission }: { mission?: MissionState }) {
  const { t } = useI18n();
  const tasks = mission?.tasks ?? [];
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-primary" />
          <CardTitle>{t("missions.taskDag")}</CardTitle>
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
                <span className="text-muted-foreground">
                  {t("common.profile")}{" "}
                </span>
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
                  {t("missions.openRun")}
                </Link>
              ) : (
                <span className="text-sm text-muted-foreground">
                  Waiting for dependencies
                </span>
              )}
              {task.result ? (
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    {t("common.result")}
                  </summary>
                  <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-slate-950 p-2 text-slate-100">
                    {JSON.stringify(task.result, null, 2)}
                  </pre>
                </details>
              ) : null}
            </div>
          </div>
        ))}
        {!tasks.length ? <EmptyState title={t("missions.noTasks")} /> : null}
      </CardBody>
    </Card>
  );
}

function MissionEventList({ events }: { events: MissionEvent[] }) {
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("missions.events")}</CardTitle>
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
        {!events.length ? <EmptyState title={t("missions.noEvents")} /> : null}
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
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("common.artifacts")}</CardTitle>
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
          <EmptyState title={t("missions.noArtifacts")} />
        ) : null}
      </CardBody>
    </Card>
  );
}

function RecentMissions({ missions }: { missions: MissionState[] }) {
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("overview.recentMissions")}</CardTitle>
        <Link className="text-sm text-primary" to="/missions">
          {t("overview.viewAll")}
        </Link>
      </CardHeader>
      <CardBody>
        <MissionList missions={missions.slice(0, 3)} />
      </CardBody>
    </Card>
  );
}

function EventList({ events }: { events: RuntimeEvent[] }) {
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("common.eventStream")}</CardTitle>
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
        {!events.length ? <EmptyState title={t("runs.noEvents")} /> : null}
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
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("common.artifacts")}</CardTitle>
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
        {!artifacts.length ? (
          <EmptyState title={t("runs.noArtifacts")} />
        ) : null}
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

function filterLabel(
  filter: RunnerFilter,
  t?: (key: Parameters<ReturnType<typeof useI18n>["t"]>[0]) => string,
) {
  const labels: Record<RunnerFilter, string> = {
    agent: t?.("live.agent") ?? "Agent",
    all: t?.("live.all") ?? "All",
    error: t?.("live.errors") ?? "Errors",
    permission: t?.("live.permissions") ?? "Permissions",
    warning: t?.("live.warnings") ?? "Warnings",
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

function defaultWorkerControlUrl() {
  if (window.location.pathname.startsWith("/agentflow")) {
    return `${window.location.origin}/agentflow-worker`;
  }
  if (window.location.pathname.startsWith("/cloud-agents")) {
    return `${window.location.origin}/cloud-agents-worker`;
  }
  return `${window.location.origin}/cloud-agents-worker`;
}

function workerBadges(worker: WorkerInfo) {
  const metadata = worker.metadata ?? {};
  const labels = objectValue(metadata.labels);
  const resources = objectValue(metadata.resources);
  const capabilities = objectValue(metadata.capabilities);
  const adapters = Array.isArray(capabilities.adapters)
    ? capabilities.adapters
        .map((adapter) => stringValue(adapter))
        .filter((adapter): adapter is string => Boolean(adapter))
    : [];
  return [
    ...Object.entries(labels).map(([key, value]) => `${key}:${String(value)}`),
    ...Object.entries(resources).map(
      ([key, value]) => `${key}:${String(value)}`,
    ),
    ...adapters.map((adapter) => `adapter:${adapter}`),
  ].slice(0, 8);
}

function objectValue(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, unknown>;
}

function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(value);
    return;
  }
  const element = document.createElement("textarea");
  element.value = value;
  document.body.appendChild(element);
  element.select();
  document.execCommand("copy");
  element.remove();
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
  copyText,
  copyProfile,
  defaultWorkerControlUrl,
  downloadText,
  emptyProfile,
  emptyToNull,
  filterLabel,
  filterTranscript,
  formatBytes,
  isTerminalEvent,
  mergeEvents,
  money,
  objectValue,
  parseJsonObject,
  prettyJson,
  registryValue,
  runnerReadableReport,
  runnerSignal,
  runnerTranscript,
  stringValue,
  toolEventBody,
  toolEventRole,
  workerBadges,
  isTerminal,
  statusLine,
  timeAgo,
};
