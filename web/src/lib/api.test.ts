import { describe, expect, it, vi } from "vitest";

import {
  artifactHref,
  auditHref,
  backupHref,
  extractPermissionRequest,
  missionArtifactHref,
  resolvedPermissionIds,
  runEventStreamHref,
  runtimeApi,
  type RuntimeEvent,
} from "./api";

describe("api helpers", () => {
  it("extracts permission requests from direct and nested event shapes", () => {
    const direct = event("permission.requested", {
      permission_id: "perm_direct",
      prompt: "Approve write?",
      options: [{ option_id: "allow", label: "Allow" }],
    });
    const nested = event("permission.requested", {
      raw: {
        data: {
          requestId: "perm_nested",
          prompt: "Approve shell?",
          tool: "shell",
        },
      },
    });
    const fallbackOption = event("permission.requested", {
      permission_id: "perm_fallback",
      options: [{}],
    });

    expect(extractPermissionRequest(direct)).toMatchObject({
      permission_id: "perm_direct",
      options: [{ id: "allow", label: "Allow" }],
    });
    expect(extractPermissionRequest(nested)).toMatchObject({
      permission_id: "perm_nested",
      tool: "shell",
    });
    expect(extractPermissionRequest(fallbackOption)).toMatchObject({
      permission_id: "perm_fallback",
      options: [{ id: "approve" }],
    });
    expect(extractPermissionRequest(event("run.running", {}))).toBeNull();
    expect(
      extractPermissionRequest(event("permission.requested", {})),
    ).toBeNull();
  });

  it("collects resolved permission ids", () => {
    const ids = resolvedPermissionIds([
      event("permission.resolved", { permission_id: "perm_1" }),
      event("permission.resolved", { raw: { data: { requestId: "perm_2" } } }),
      event("permission.requested", { permission_id: "perm_3" }),
    ]);

    expect([...ids]).toEqual(["perm_1", "perm_2"]);
  });

  it("wraps runtime endpoints", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((path: string, init?: RequestInit) => {
        calls.push([path, init]);
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );

    await runtimeApi.session();
    await runtimeApi.login({ username: "cloudagents", password: "secret" });
    await runtimeApi.logout();
    await runtimeApi.workerControl("worker 1");
    await runtimeApi.drainWorker("worker 1");
    await runtimeApi.resumeWorker("worker 1");
    await runtimeApi.retryWorkerRuns("worker 1");
    await runtimeApi.queue();
    await runtimeApi.executors();
    await runtimeApi.costStatus();
    await runtimeApi.runAudit("run_1");
    await runtimeApi.cancelRun("run_1");
    await runtimeApi.mission("mission_1");
    await runtimeApi.missionEvents("mission_1");
    await runtimeApi.missionArtifacts("mission_1");
    await runtimeApi.cancelMission("mission_1");
    await runtimeApi.overrideReviewGate("mission_1", {
      decision: "approve",
      reason: "reviewed",
    });
    await runtimeApi.profile("planner");
    await runtimeApi.createProfile({ id: "planner-copy" });
    await runtimeApi.accessPolicy();
    await runtimeApi.accessProjects();
    await runtimeApi.createAccessProject({ project_id: "default" });
    await runtimeApi.apiTokens();
    await runtimeApi.createApiToken({ name: "operator" });
    await runtimeApi.revokeApiToken("token_1");
    await runtimeApi.createMission({ goal: "ship", strategy: "sequential" });

    expect(calls.map(([path]) => path)).toEqual([
      "/auth/session",
      "/auth/login",
      "/auth/logout",
      "/workers/worker%201/control",
      "/workers/worker%201/drain",
      "/workers/worker%201/resume",
      "/workers/worker%201/retry",
      "/queue",
      "/executors",
      "/cost/status",
      "/runs/run_1/audit.json",
      "/runs/run_1/cancel",
      "/missions/mission_1",
      "/missions/mission_1/events.json",
      "/missions/mission_1/artifacts",
      "/missions/mission_1/cancel",
      "/missions/mission_1/review-gate/override",
      "/profiles/planner",
      "/profiles",
      "/access/policy",
      "/access/projects",
      "/access/projects",
      "/access/tokens",
      "/access/tokens",
      "/access/tokens/token_1/revoke",
      "/missions",
    ]);
    expect(calls[1][1]?.method).toBe("POST");
    expect(calls[2][1]?.method).toBe("POST");
    expect(calls[4][1]?.method).toBe("POST");
    expect(calls[5][1]?.method).toBe("POST");
    expect(calls[6][1]?.method).toBe("POST");
    expect(calls[11][1]?.method).toBe("POST");
    expect(calls[15][1]?.method).toBe("POST");
    expect(calls[16][1]?.method).toBe("POST");
    expect(calls[18][1]?.method).toBe("POST");
    expect(calls[21][1]?.method).toBe("POST");
    expect(calls[23][1]?.method).toBe("POST");
    expect(calls[24][1]?.method).toBe("POST");
    expect(calls[25][1]?.method).toBe("POST");
  });

  it("surfaces API errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response("not allowed", {
            status: 403,
            statusText: "Forbidden",
          }),
        ),
      ),
    );

    await expect(runtimeApi.health()).rejects.toThrow("not allowed");
  });

  it("builds hrefs from the current app base", async () => {
    window.history.pushState({}, "", "/cloud-agents/");
    vi.resetModules();
    const fresh = await import("./api");

    expect(fresh.artifactHref("run_1", "a b.json")).toBe(
      "/cloud-agents/runs/run_1/artifacts/a%20b.json",
    );
    expect(fresh.auditHref("run_1")).toBe(
      "/cloud-agents/runs/run_1/audit.json",
    );
    expect(fresh.runEventStreamHref("run_1")).toBe(
      "/cloud-agents/runs/run_1/events",
    );
    expect(fresh.backupHref("backup.tgz")).toBe(
      "/cloud-agents/ops/backups/backup.tgz",
    );
    expect(fresh.missionArtifactHref("mission_1", "final report.md")).toBe(
      "/cloud-agents/missions/mission_1/artifacts/final%20report.md",
    );

    window.history.pushState({}, "", "/agentflow/");
    vi.resetModules();
    const agentflowBase = await import("./api");
    expect(agentflowBase.artifactHref("run_1", "a b.json")).toBe(
      "/agentflow/runs/run_1/artifacts/a%20b.json",
    );

    window.history.pushState({}, "", "/");
    expect(artifactHref("run_1", "events.jsonl")).toBe(
      "/runs/run_1/artifacts/events.jsonl",
    );
    expect(auditHref("run_1")).toBe("/runs/run_1/audit.json");
    expect(runEventStreamHref("run_1")).toBe("/runs/run_1/events");
    expect(backupHref("backup.tgz")).toBe("/ops/backups/backup.tgz");
    expect(missionArtifactHref("mission_1", "manifest.json")).toBe(
      "/missions/mission_1/artifacts/manifest.json",
    );
  });
});

function event(type: string, data: Record<string, unknown>): RuntimeEvent {
  return {
    id: `evt_${type}`,
    run_id: "run_1",
    sequence: 1,
    type,
    created_at: new Date().toISOString(),
    data,
  };
}
