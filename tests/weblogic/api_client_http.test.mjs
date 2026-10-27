// PIANO-UI-003 U3-R2: real HTTP/fetch request-lifetime regressions.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { performance } from "node:perf_hooks";
import {
  ApiClient,
  ApiError,
  LiveNoteCoordinator,
} from "../../src/piano_core/web/static/app.js";

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
let failures = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

async function waitFor(predicate, description, timeout = 1500) {
  const deadline = performance.now() + timeout;
  while (performance.now() < deadline) {
    if (predicate()) return;
    await sleep(5);
  }
  throw new Error(`timed out waiting for ${description}`);
}

function snapshot(sequence, note = 60) {
  return {
    schema_version: 1,
    runtime_epoch: "real-http-runtime",
    sequence,
    physical: { notes: [note], midi_connection: "CONNECTED" },
    playback: { notes: [] },
  };
}

class FakeEventSource {
  constructor() { this.onerror = null; }
  addEventListener() {}
  close() {}
}

const accounting = {
  started: 0,
  headersReceived: 0,
  bodiesCompleted: 0,
  aborted: 0,
  timedOut: 0,
  failed: 0,
  accepted: 0,
  stale: 0,
  active: 0,
  maxConcurrent: 0,
};
let mode = "healthy";
let modeRequest = 0;

function beginResponse(response) {
  accounting.started += 1;
  accounting.active += 1;
  accounting.maxConcurrent = Math.max(accounting.maxConcurrent, accounting.active);
  let settled = false;
  response.on("finish", () => {
    if (settled) return;
    settled = true;
    accounting.active -= 1;
  });
  response.on("close", () => {
    if (settled) return;
    settled = true;
    accounting.active -= 1;
    accounting.aborted += 1;
  });
}

function stalledJson(response) {
  response.writeHead(200, {
    "content-type": "application/json",
    "cache-control": "no-store",
  });
  response.flushHeaders();
  response.write('{"schema_version":1,"runtime_epoch":"stalled"');
  accounting.headersReceived += 1;
}

const server = createServer((request, response) => {
  beginResponse(response);
  const url = new URL(request.url, "http://127.0.0.1");
  if (url.pathname === "/api/v1/live-notes") {
    modeRequest += 1;
    if (
      mode === "stall" ||
      ((mode === "timeout_then_healthy" || mode === "abort_then_healthy") && modeRequest === 1)
    ) {
      stalledJson(response);
      return;
    }
    const body = JSON.stringify(snapshot(modeRequest, mode === "abort_then_healthy" ? 67 : 64));
    response.writeHead(200, {
      "content-type": "application/json",
      "content-length": Buffer.byteLength(body),
      "cache-control": "no-store",
    });
    accounting.headersReceived += 1;
    accounting.bodiesCompleted += 1;
    response.end(body);
    return;
  }
  if (url.pathname === "/api/v1/invalid") {
    response.writeHead(200, { "content-type": "application/json" });
    accounting.headersReceived += 1;
    accounting.bodiesCompleted += 1;
    response.end("{invalid-json");
    return;
  }
  if (url.pathname === "/api/v1/typed-error") {
    const body = JSON.stringify({ error: { code: "conflict", message: "busy" } });
    response.writeHead(409, {
      "content-type": "application/json",
      "content-length": Buffer.byteLength(body),
    });
    accounting.headersReceived += 1;
    accounting.bodiesCompleted += 1;
    response.end(body);
    return;
  }
  response.writeHead(404).end();
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;
const fetchImpl = (url, options) => fetch(new URL(url, base), options);

await test("headers plus stalled body remain covered by request timeout", async () => {
  mode = "stall";
  modeRequest = 0;
  const client = new ApiClient(fetchImpl, 80);
  const startedAt = performance.now();
  await assert.rejects(
    client.liveNotes(),
    (error) => error instanceof ApiError && error.code === "network_error"
  );
  const elapsed = performance.now() - startedAt;
  accounting.timedOut += 1;
  assert.ok(elapsed >= 50 && elapsed < 750, `timeout settled after ${elapsed} ms`);
  await waitFor(() => accounting.active === 0, "timed-out body connection cleanup");
});

await test("external abort remains wired after response headers", async () => {
  mode = "stall";
  modeRequest = 0;
  const controller = new AbortController();
  const client = new ApiClient(fetchImpl, 1000);
  const headersBefore = accounting.headersReceived;
  const startedAt = performance.now();
  const pending = client.liveNotes(controller.signal);
  await waitFor(() => accounting.headersReceived > headersBefore, "stalled response headers");
  controller.abort();
  await assert.rejects(
    pending,
    (error) => error instanceof ApiError && error.code === "network_error"
  );
  assert.ok(performance.now() - startedAt < 750);
  await waitFor(() => accounting.active === 0, "externally aborted body cleanup");
});

await test("timed-out coordinator request releases slot for healthy follow-up", async () => {
  mode = "timeout_then_healthy";
  modeRequest = 0;
  const client = new ApiClient(fetchImpl, 80);
  let active = 0;
  let maxConcurrent = 0;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => new FakeEventSource(),
    snapshotLoader: async (signal) => {
      active += 1;
      maxConcurrent = Math.max(maxConcurrent, active);
      try { return await client.liveNotes(signal); }
      finally { active -= 1; }
    },
    resnapshotMs: 20,
    freshnessMs: 500,
  });
  coordinator.start();
  await waitFor(() => coordinator.replica.status === "live", "healthy recovery after timeout");
  accounting.accepted += 1;
  accounting.timedOut += 1;
  assert.equal(modeRequest, 2);
  assert.equal(maxConcurrent, 1);
  assert.equal(coordinator.replica.snapshot.sequence, 2);
  coordinator.stop();
  await waitFor(() => accounting.active === 0, "timeout-follow-up cleanup");
});

await test("visibility stop aborts stalled body and resume recovers once", async () => {
  mode = "abort_then_healthy";
  modeRequest = 0;
  const client = new ApiClient(fetchImpl, 1000);
  let active = 0;
  let maxConcurrent = 0;
  const coordinator = new LiveNoteCoordinator({
    eventSourceFactory: () => new FakeEventSource(),
    snapshotLoader: async (signal) => {
      active += 1;
      maxConcurrent = Math.max(maxConcurrent, active);
      try { return await client.liveNotes(signal); }
      finally { active -= 1; }
    },
  });
  coordinator.start();
  await waitFor(() => modeRequest === 1 && accounting.active === 1, "first stalled request");
  coordinator.stop();
  coordinator.start();
  coordinator.start();
  await waitFor(() => coordinator.replica.status === "live", "visible replacement recovery");
  accounting.accepted += 1;
  assert.equal(modeRequest, 2);
  assert.equal(maxConcurrent, 1);
  assert.equal(coordinator.replica.snapshot.sequence, 2);
  assert.deepEqual(coordinator.replica.snapshot.physical.notes, [67]);
  coordinator.stop();
  await waitFor(() => accounting.active === 0, "visibility replacement cleanup");
});

await test("malformed success and typed HTTP failure remain distinct", async () => {
  const client = new ApiClient(fetchImpl, 500);
  await assert.rejects(
    client._request("GET", "/api/v1/invalid"),
    (error) => error instanceof ApiError && error.code === "invalid_response" && error.status === 200
  );
  accounting.failed += 1;
  await assert.rejects(
    client._request("GET", "/api/v1/typed-error"),
    (error) => error instanceof ApiError && error.code === "conflict" && error.status === 409
  );
});

await test("terminated 200 success body is classified as transport failure, not invalid response", async () => {
  const client = new ApiClient(async () => ({
    ok: true,
    status: 200,
    text: async () => {
      throw new TypeError("terminated");
    },
  }), 500);
  await assert.rejects(
    client._request("POST", "/api/v1/playback/pause", {}),
    (error) => error instanceof ApiError && error.code === "network_error" && error.status === 0
  );
});

await new Promise((done) => server.close(done));
accounting.stale = 0;
console.log(`request-accounting - ${JSON.stringify(accounting)}`);
if (failures > 0) {
  console.error(`${failures} real HTTP ApiClient test(s) failed`);
  process.exitCode = 1;
} else {
  console.log("ALL REAL HTTP API CLIENT TESTS PASSED");
}
