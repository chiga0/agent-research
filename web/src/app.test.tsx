import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App, __testUtils, queryClient, router } from "./app";

const run = {
  run_id: "run_1",
  status: "running",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  event_count: 2,
  prompt_count: 1,
  spec: {
    adapter: "fake",
    prompt: "Inspect runtime",
  },
};

const mission = {
  mission_id: "mission_1",
  status: "running",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  event_count: 1,
  task_count: 2,
  completed_task_count: 1,
  failed_task_count: 0,
  spec: { goal: "Ship beta", strategy: "sequential", adapter: "fake" },
  tasks: [
    {
      task_id: "plan",
      title: "Plan mission",
      profile_id: "planner",
      status: "completed",
      run_id: "run_1",
      depends_on: [],
      result: { artifacts: [{ name: "plan.md" }] },
    },
    {
      task_id: "review",
      title: "Review mission",
      profile_id: "reviewer",
      status: "pending",
      run_id: null,
      depends_on: ["plan"],
    },
  ],
};

const missionEvents = [
  {
    id: "mevt_1",
    mission_id: "mission_1",
    sequence: 1,
    type: "task.created",
    created_at: new Date().toISOString(),
    data: { task_id: "plan" },
  },
  {
    id: "mevt_2",
    mission_id: "mission_1",
    sequence: 2,
    type: "mission.started",
    created_at: new Date().toISOString(),
    data: { strategy: "sequential" },
  },
];

const events = [
  {
    id: "evt_0",
    run_id: "run_1",
    sequence: 1,
    type: "run.created",
    created_at: new Date().toISOString(),
    data: { spec: run.spec },
  },
  {
    id: "evt_1",
    run_id: "run_1",
    sequence: 2,
    type: "permission.requested",
    created_at: new Date().toISOString(),
    data: {
      permission_id: "perm_1",
      prompt: "Allow shell command?",
      options: [
        { id: "approve", label: "Approve" },
        { id: "deny", label: "Deny" },
      ],
    },
  },
  {
    id: "evt_2",
    run_id: "run_1",
    sequence: 3,
    type: "step.started",
    created_at: new Date().toISOString(),
    data: { prompt_number: 1 },
  },
  {
    id: "evt_3",
    run_id: "run_1",
    sequence: 4,
    type: "message.delta",
    created_at: new Date().toISOString(),
    data: { prompt_number: 1, text: "Inspecting live runner state." },
  },
];

const fixtures: Record<string, unknown> = {
  health: { ok: true, version: "0.1-test" },
  capabilities: {
    mode: "saeu-runtime",
    features: ["metrics", "backup", "executor_registry", "cost_budget"],
    adapters: {
      fake: { name: "Fake", status: "available" },
      qwen: { name: "Qwen", status: "available" },
    },
    queue: { counts: {}, jobs: [], workers: [] },
    executor_registry: {
      config: {
        strategy: "per_run_process",
        enabled: true,
        container_image: "qwen-code:latest",
        container_network: "bridge",
      },
      counts: { running: 1 },
    },
    profiles: [],
  },
  "metrics.json": {
    generated_at: new Date().toISOString(),
    runs: { total: 1, by_status: { running: 1 } },
    missions: { total: 1, by_status: { running: 1 } },
    queue: {
      counts: { queued: 0, running: 1 },
      worker_count: 1,
      active_workers: 1,
      stale_workers: 0,
    },
    permissions: { pending: 1, stalled: 0 },
    latency_seconds: { count: 0, avg: null, p95: null },
  },
  executors: {
    executor_registry: {
      config: {
        strategy: "per_run_process",
        enabled: true,
        container_image: "qwen-code:latest",
        container_network: "bridge",
      },
      counts: { running: 1 },
    },
    executors: [
      {
        executor_id: "exec_1",
        run_id: "run_1",
        adapter: "qwen",
        strategy: "per_run_process",
        status: "running",
        base_url: "http://127.0.0.1:4210",
        workspace: "/tmp/workspace/run_1",
        port: 4210,
        pid: 1234,
        started_at: new Date().toISOString(),
        heartbeat_at: new Date().toISOString(),
        released_at: null,
        exit_code: null,
        last_error: null,
        metadata: {},
      },
    ],
  },
  "cost/status": {
    generated_at: new Date().toISOString(),
    status: "ok",
    config: {
      monthly_budget_usd: 10,
      per_run_budget_usd: 1,
      estimated_cost_per_run_usd: 0.05,
    },
    month: "2026-07",
    monthly_estimated_cost_usd: 0.1,
    monthly_budget_usd: 10,
    warning_threshold_usd: 8,
    runs: [{ run_id: "run_1", estimated_cost_usd: 0.1 }],
  },
  runs: { runs: [run] },
  "runs/run_1": run,
  "runs/run_1/events.json": { events },
  "runs/run_1/artifacts": {
    artifacts: [
      {
        name: "final-report.md",
        size_bytes: 42,
        updated_at: new Date().toISOString(),
      },
    ],
  },
  missions: { missions: [mission] },
  "missions/mission_1": mission,
  "missions/mission_1/events.json": { events: missionEvents },
  "missions/mission_1/artifacts": {
    artifacts: [
      {
        name: "final_report.md",
        size_bytes: 88,
        updated_at: new Date().toISOString(),
      },
    ],
  },
  profiles: {
    profiles: [
      {
        id: "planner",
        display_name: "Planner",
        description: "Plan work",
        version: 1,
        source: "system",
        runtime: { preferred_adapter: "qwen" },
        tools: { allow: ["read_file"] },
        approval: { mode: "ask" },
        limits: {},
        workspace: {},
        artifacts: {},
      },
    ],
  },
  "ops/status": {
    database: { exists: true },
    security: { docker_socket: false },
  },
  "ops/drills": {
    status: "pass",
    checks: [
      {
        id: "runtime-db",
        status: "pass",
        summary: "runtime.db is present",
        details: {},
      },
    ],
  },
  "ops/backups": {
    backups: [
      {
        name: "cloud-agents-backup-test.tar.gz",
        size_bytes: 128,
        created_at: new Date().toISOString(),
      },
    ],
  },
  "p5/evaluations": {
    components: [
      {
        id: "acp-streamable-http",
        status: "implemented",
        mode: "json-rpc",
        decision: "keep",
      },
    ],
  },
  "access/policy": {
    mode: "single-tenant-rbac-foundation",
    current_principal: {
      id: "operator",
      display_name: "operator",
      roles: ["owner"],
    },
    roles: [
      {
        id: "owner",
        description: "Can administer runtime",
        permissions: ["runs:*", "missions:*", "profiles:*"],
      },
    ],
    scopes: ["runs:*", "missions:*", "profiles:*"],
    projects: [
      {
        project_id: "default",
        display_name: "Default",
        description: "Default project",
        status: "active",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        metadata: {},
      },
    ],
    tokens: [
      {
        token_id: "token_1",
        name: "operator-token",
        principal_id: "operator",
        project_id: "default",
        scopes: ["runs:*"],
        status: "active",
        token_prefix: "cat_test",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        revoked_at: null,
        last_used_at: null,
        metadata: {},
      },
    ],
    audit: { auth_boundary: "basic auth plus bearer" },
  },
  "access/projects": {
    projects: [
      {
        project_id: "default",
        display_name: "Default",
        description: "Default project",
        status: "active",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        metadata: {},
      },
    ],
  },
  "access/tokens": {
    tokens: [
      {
        token_id: "token_1",
        name: "operator-token",
        principal_id: "operator",
        project_id: "default",
        scopes: ["runs:*"],
        status: "active",
        token_prefix: "cat_test",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        revoked_at: null,
        last_used_at: null,
        metadata: {},
      },
    ],
  },
};

describe("Cloud Agents console", () => {
  beforeEach(async () => {
    queryClient.clear();
    window.location.hash = "";
    document.documentElement.classList.remove("dark");
    vi.stubGlobal("fetch", vi.fn(fetchMock));
    await act(async () => {
      await router.navigate({ to: "/" });
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the runtime overview", async () => {
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Overview" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("Recent Runs")).toBeInTheDocument();
    expect(screen.getByText("Recent Missions")).toBeInTheDocument();
  });

  it("creates a run from the Runs page", async () => {
    const user = userEvent.setup();
    await act(async () => {
      await router.navigate({ to: "/runs" });
    });
    render(<App />);

    await user.click(screen.getByRole("button", { name: /refresh/i }));
    await user.clear(await screen.findByLabelText("Prompt"));
    await user.type(screen.getByLabelText("Prompt"), "Run a smoke validation");
    await user.type(screen.getByLabelText("Repo"), "/tmp/repo");
    await user.type(screen.getByLabelText("Workspace"), "/tmp/workspace");
    await user.clear(screen.getByLabelText("Timeout seconds"));
    await user.type(screen.getByLabelText("Timeout seconds"), "900");
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/runs",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("Run a smoke validation"),
        }),
      ),
    );
  });

  it("resolves a run permission and exposes artifact downloads", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:runner-report");
    const revokeObjectURL = vi.fn();
    const click = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.spyOn(document, "createElement").mockImplementation((tagName) => {
      const element = document.createElementNS(
        "http://www.w3.org/1999/xhtml",
        tagName,
      ) as HTMLAnchorElement;
      if (tagName === "a") {
        element.click = click;
      }
      return element;
    });
    await act(async () => {
      await router.navigate({ to: "/runs/$runId", params: { runId: "run_1" } });
    });
    render(<App />);

    expect(await screen.findByText("Permission Requests")).toBeInTheDocument();
    expect(await screen.findByText("Live Runner Chat")).toBeInTheDocument();
    expect(
      screen.getByText("Inspecting live runner state."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Agent" }));
    await user.click(screen.getByRole("button", { name: "Permissions" }));
    await user.click(screen.getByRole("button", { name: "Warnings" }));
    await user.click(screen.getByRole("button", { name: "Errors" }));
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.click(screen.getByRole("button", { name: "Download Report" }));
    expect(click).toHaveBeenCalled();
    expect(screen.getByText("final-report.md")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/runs/run_1/permissions/perm_1",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("approve"),
        }),
      ),
    );
  });

  it("shows mission detail and profile policy editor", async () => {
    const user = userEvent.setup();
    await act(async () => {
      await router.navigate({ to: "/missions" });
    });
    render(<App />);

    expect(await screen.findByText("Ship beta")).toBeInTheDocument();
    expect(screen.getByText("Plan mission")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /open detail/i }));
    expect(await screen.findByText("Task DAG")).toBeInTheDocument();
    expect(screen.getByText("Mission Events")).toBeInTheDocument();
    expect(screen.getByText("final_report.md")).toBeInTheDocument();

    await act(async () => {
      await router.navigate({ to: "/missions" });
    });
    await user.clear(screen.getByLabelText("Goal"));
    await user.type(
      screen.getByLabelText("Goal"),
      "Create a beta validation report",
    );
    await user.selectOptions(screen.getByLabelText("Strategy"), "fanout");
    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/missions",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("Create a beta validation report"),
        }),
      ),
    );

    await act(async () => {
      await router.navigate({ to: "/profiles" });
    });
    await screen.findByText("Planner");
    expect(screen.getByText("Runtime")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy" }));
    await user.clear(screen.getByLabelText("Display name"));
    await user.type(screen.getByLabelText("Display name"), "Planner Copy");
    await user.click(screen.getByRole("button", { name: "Save Profile" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/profiles",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("Planner Copy"),
        }),
      ),
    );
  });

  it("shows access policy foundations", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:access-policy");
    const revokeObjectURL = vi.fn();
    const click = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.spyOn(document, "createElement").mockImplementation((tagName) => {
      const element = document.createElementNS(
        "http://www.w3.org/1999/xhtml",
        tagName,
      ) as HTMLAnchorElement;
      if (tagName === "a") {
        element.click = click;
      }
      return element;
    });
    await act(async () => {
      await router.navigate({ to: "/access" });
    });
    render(<App />);

    expect(await screen.findByText("Current Principal")).toBeInTheDocument();
    expect(screen.getByText("Role Matrix")).toBeInTheDocument();
    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.getByText("API Tokens")).toBeInTheDocument();
    expect((await screen.findAllByText("runs:*")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Export" }));
    expect(click).toHaveBeenCalled();
    await user.clear(screen.getAllByLabelText("Project ID")[0]);
    await user.type(screen.getAllByLabelText("Project ID")[0], "team1");
    await user.clear(screen.getByLabelText("Token name"));
    await user.type(screen.getByLabelText("Token name"), "team-token");
    await user.click(screen.getAllByRole("button", { name: "Create" })[0]);
    await user.click(screen.getAllByRole("button", { name: "Create" })[1]);
    expect(await screen.findByText("cat_created_secret")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/access/tokens",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("shows executor isolation registry", async () => {
    const user = userEvent.setup();
    await act(async () => {
      await router.navigate({ to: "/executors" });
    });
    render(<App />);

    expect(await screen.findByText("Executor Leases")).toBeInTheDocument();
    expect(screen.getByText("Registry")).toBeInTheDocument();
    expect(await screen.findByText("exec_1")).toBeInTheDocument();
    expect(screen.getAllByText("per_run_process").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
  });

  it("runs operations drills and creates backups", async () => {
    const user = userEvent.setup();
    await act(async () => {
      await router.navigate({ to: "/operations" });
    });
    render(<App />);

    expect(await screen.findByText("Failure Drills")).toBeInTheDocument();
    expect(await screen.findByText("Cost Budget")).toBeInTheDocument();
    expect(await screen.findByText("acp-streamable-http")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/ops/backups",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("opens mobile navigation and toggles theme", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByLabelText("Open navigation"));
    expect(screen.getByText("Navigation")).toBeInTheDocument();
    await user.click(screen.getAllByRole("link", { name: /Missions/ }).at(-1)!);
    expect(
      await screen.findByRole("heading", { name: "Missions" }),
    ).toBeInTheDocument();
    await user.click(screen.getByLabelText("Open navigation"));
    await user.click(screen.getByLabelText("Close navigation"));
    await waitFor(() =>
      expect(screen.queryByText("Navigation")).not.toBeInTheDocument(),
    );

    await user.click(screen.getByLabelText("Toggle theme"));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("summarizes runner events for the live chat timeline", () => {
    const now = new Date().toISOString();
    const liveEvents = [
      event("run.created", 1, { spec: run.spec }, now),
      event(
        "workspace.prepared",
        2,
        {
          strategy: "qwen_serve_shared",
          path: "/workspace",
        },
        now,
      ),
      event("resources.resolved", 3, { cpus: 1 }, now),
      event("run.queued", 4, {}, now),
      event("lease.claimed", 5, { worker_id: "worker_1" }, now),
      event(
        "run.started",
        6,
        { adapter: "qwen", workspace: "/workspace" },
        now,
      ),
      event(
        "input.accepted",
        7,
        { prompt_number: 1, prompt_preview: "Hello" },
        now,
      ),
      event("step.started", 8, { prompt_number: 1 }, now),
      event("step.submitted", 9, { prompt_number: 1 }, now),
      event("message.delta", 10, { prompt_number: 1, text: "Hel" }, now),
      event("message.delta", 11, { prompt_number: 1, text: "lo" }, now),
      event(
        "permission.requested",
        12,
        { permission_id: "perm_2", prompt: "Approve?" },
        now,
      ),
      event("permission.resolved", 13, { decision: "approve" }, now),
      event("permission.stalled", 14, { permission_id: "perm_3" }, now),
      event("stream.warning", 15, { reason: "reconnect" }, now),
      event("step.completed", 16, { prompt_number: 1 }, now),
      event("run.completed", 17, { final_artifact: "final_1.json" }, now),
      event("run.failed", 18, { reason: "boom" }, now),
      event("run.cancelled", 19, { reason: "user" }, now),
      event("turn_error", 20, { raw: true }, now),
      event(
        "adapter.event",
        21,
        { command: "npm test", cwd: "/workspace", exit_code: 0 },
        now,
      ),
      event(
        "adapter.event",
        22,
        { command: "npm lint", exit_code: 1, stderr: "lint failed" },
        now,
      ),
    ];

    const transcript = __testUtils.runnerTranscript(liveEvents);
    const plannerProfile = (
      fixtures.profiles as {
        profiles: Array<Parameters<typeof __testUtils.copyProfile>[0]>;
      }
    ).profiles[0];

    expect(transcript.map((item) => item.title)).toContain("Agent output #1");
    expect(
      transcript.find((item) => item.title === "Agent output #1")?.body,
    ).toBe("Hello");
    expect(transcript.map((item) => item.title)).toContain(
      "Permission required",
    );
    expect(transcript.map((item) => item.title)).toContain("Run failed");
    expect(transcript.map((item) => item.title)).toContain("turn_error");
    expect(__testUtils.mergeEvents(liveEvents, [])).toBe(liveEvents);
    expect(__testUtils.mergeEvents(liveEvents, [liveEvents[0]])).toBe(
      liveEvents,
    );
    expect(__testUtils.isTerminalEvent("run.completed")).toBe(true);
    expect(__testUtils.isTerminalEvent("step.completed")).toBe(false);
    expect(__testUtils.connectionLabel("fallback")).toBe("polling");
    expect(__testUtils.connectionTone("live")).toBe("ok");
    expect(__testUtils.connectionTone("reconnecting")).toBe("warn");
    expect(__testUtils.connectionTone("closed")).toBe("neutral");
    expect(__testUtils.bubbleClass("error")).toContain("destructive");
    expect(__testUtils.filterLabel("warning")).toBe("Warnings");
    expect(__testUtils.filterTranscript(transcript, "all")).toBe(transcript);
    expect(__testUtils.filterTranscript(transcript, "agent")).toHaveLength(1);
    expect(
      __testUtils.filterTranscript(transcript, "permission").length,
    ).toBeGreaterThan(1);
    expect(
      __testUtils.filterTranscript(transcript, "warning").length,
    ).toBeGreaterThan(1);
    expect(
      __testUtils.filterTranscript(transcript, "error").length,
    ).toBeGreaterThan(1);
    expect(__testUtils.runnerSignal(liveEvents.at(-1), "running").label).toBe(
      "active",
    );
    expect(__testUtils.runnerSignal(undefined, "running").label).toBe(
      "waiting",
    );
    expect(__testUtils.runnerSignal(liveEvents[0], "completed").label).toBe(
      "terminal",
    );
    expect(
      __testUtils.runnerSignal(
        event(
          "run.started",
          30,
          {},
          new Date(Date.now() - 180_000).toISOString(),
        ),
        "running",
      ).label,
    ).toBe("stalled");
    expect(__testUtils.runnerReadableReport(transcript, liveEvents)).toContain(
      "Runner Execution Report",
    );
    expect(__testUtils.copyProfile(plannerProfile).id).toBe("planner-copy");
    expect(__testUtils.compactJson(null)).toBe("");
    expect(__testUtils.compactJson({ ok: true })).toContain("ok");
    expect(__testUtils.emptyProfile().id).toBe("custom-profile");
    expect(__testUtils.emptyToNull("  ")).toBeNull();
    expect(__testUtils.formatBytes(1024)).toBe("1.0 KB");
    expect(__testUtils.prettyJson({ ok: true })).toContain("ok");
    expect(__testUtils.parseJsonObject("{}", "test")).toEqual({});
    expect(() => __testUtils.parseJsonObject("[]", "test")).toThrow(
      "test must be a JSON object",
    );
    expect(
      __testUtils.toolEventBody(
        event("adapter.event", 31, { tool: "shell", stdout: "ok" }, now),
      ),
    ).toContain("shell");
    expect(
      __testUtils.toolEventRole(
        event("adapter.event", 32, { status: "failed" }, now),
      ),
    ).toBe("error");
    expect(
      __testUtils.runnerTranscript([event("run.completed", 33, {}, now)])[0]
        .body,
    ).toBe("The runner reached a terminal success state.");
    expect(
      __testUtils.runnerTranscript([event("unmapped.event", 34, {}, now)]),
    ).toHaveLength(0);
    expect(
      __testUtils.toolEventBody(
        event("adapter.event", 35, { name: "named-tool" }, now),
      ),
    ).toContain("named-tool");
    expect(
      __testUtils.toolEventBody(event("adapter.event", 36, {}, now)),
    ).toBe("adapter event");
    expect(__testUtils.statusLine({ running: 2 })).toBe("running 2");
    expect(__testUtils.stringValue(123)).toBe("123");
    expect(__testUtils.timeAgo(undefined)).toBe("-");
  });

  it("downloads a readable runner report", () => {
    const createObjectURL = vi.fn(() => "blob:report");
    const revokeObjectURL = vi.fn();
    const click = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.spyOn(document, "createElement").mockImplementation((tagName) => {
      const element = document.createElementNS(
        "http://www.w3.org/1999/xhtml",
        tagName,
      ) as HTMLAnchorElement;
      if (tagName === "a") {
        element.click = click;
      }
      return element;
    });

    __testUtils.downloadText("report.md", "# report");

    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:report");
  });
});

function event(
  type: string,
  sequence: number,
  data: Record<string, unknown>,
  createdAt: string,
) {
  return {
    id: `evt_${sequence}`,
    run_id: "run_1",
    sequence,
    type,
    created_at: createdAt,
    data,
  };
}

async function fetchMock(input: RequestInfo | URL, init?: RequestInit) {
  const url = typeof input === "string" ? input : input.toString();
  const path = url.replace(/^https?:\/\/[^/]+\//, "").replace(/^\//, "");
  if (init?.method === "POST" && path === "runs") {
    return jsonResponse({ ...run, run_id: "run_created", status: "queued" });
  }
  if (init?.method === "POST" && path === "missions") {
    return jsonResponse({ ...mission, mission_id: "mission_created" });
  }
  if (init?.method === "POST" && path === "profiles") {
    return jsonResponse({
      ...(fixtures.profiles as { profiles: Array<Record<string, unknown>> })
        .profiles[0],
      display_name: "Planner Copy",
      source: "user",
      version: 2,
    });
  }
  if (init?.method === "POST" && path === "access/projects") {
    return jsonResponse({
      project_id: "created",
      display_name: "Created",
      description: "",
      status: "active",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      metadata: {},
    });
  }
  if (init?.method === "POST" && path === "access/tokens") {
    return jsonResponse({
      token_id: "token_created",
      name: "operator-token",
      principal_id: "operator",
      project_id: "default",
      scopes: ["runs:*"],
      status: "active",
      token_prefix: "cat_created",
      token: "cat_created_secret",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      metadata: {},
    });
  }
  if (init?.method === "POST" && path === "access/tokens/token_1/revoke") {
    return jsonResponse({
      token_id: "token_1",
      name: "operator-token",
      principal_id: "operator",
      scopes: ["runs:*"],
      status: "revoked",
      token_prefix: "cat_test",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      revoked_at: new Date().toISOString(),
      metadata: {},
    });
  }
  if (init?.method === "POST" && path.includes("/permissions/")) {
    return jsonResponse({ accepted: true });
  }
  if (init?.method === "POST" && path === "ops/backups") {
    return jsonResponse({
      backup: {
        name: "cloud-agents-backup-new.tar.gz",
        size_bytes: 256,
        created_at: new Date().toISOString(),
      },
    });
  }
  if (init?.method === "POST" && path === "ops/drills") {
    return jsonResponse(fixtures["ops/drills"]);
  }
  return jsonResponse(fixtures[path] ?? {});
}

function jsonResponse(payload: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
}
