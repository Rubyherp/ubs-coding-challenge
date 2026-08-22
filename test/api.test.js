import assert from "node:assert/strict";
import { Readable } from "node:stream";
import { beforeEach, test } from "node:test";
import { createHandler } from "../src/api.js";
import { RiskEngine } from "../src/risk-engine.js";

const engine = new RiskEngine();
const handler = createHandler(engine);

beforeEach(() => engine.reset());

async function request(path, { method = "GET", body, headers = {} } = {}) {
  const payload = body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body);
  const requestObject = Readable.from(payload ? [Buffer.from(payload)] : []);
  requestObject.method = method;
  requestObject.url = path;
  requestObject.headers = headers;

  const responseHeaders = new Map();
  const responseObject = {
    status: null,
    chunks: [],
    setHeader(name, value) {
      responseHeaders.set(name.toLowerCase(), String(value));
    },
    writeHead(status, values) {
      this.status = status;
      for (const [name, value] of Object.entries(values ?? {})) {
        responseHeaders.set(name.toLowerCase(), String(value));
      }
    },
    end(chunk) {
      if (chunk !== undefined) this.chunks.push(Buffer.from(chunk));
    }
  };

  await handler(requestObject, responseObject);
  const text = Buffer.concat(responseObject.chunks).toString("utf8");
  const response = {
    status: responseObject.status,
    headers: { get: (name) => responseHeaders.get(name.toLowerCase()) ?? null }
  };
  return { response, json: JSON.parse(text) };
}

function tx(overrides = {}) {
  return {
    txId: "tx-1",
    fromUserId: "meridian",
    toUserId: "apex",
    amount: 370,
    createdAt: "2026-06-08T12:00:00Z",
    ...overrides
  };
}

test("health endpoint returns the required payload", async () => {
  const { response, json } = await request("/ghost-chains/health");
  assert.equal(response.status, 200);
  assert.deepEqual(json, { status: "ok" });
  assert.match(response.headers.get("content-type"), /^application\/json/);
});

test("transaction endpoint accepts missing optionals and unknown fields", async () => {
  const { response, json } = await request("/ghost-chains/transactions", {
    method: "POST",
    body: { transactions: [tx({ futurePhaseField: { accepted: true } })], topLevelUnknown: true }
  });

  assert.equal(response.status, 200);
  assert.equal(json.transactions[0].txId, "tx-1");
  assert.equal(typeof json.transactions[0].riskScore, "number");
});

test("reset endpoint clears all state", async () => {
  await request("/ghost-chains/transactions", { method: "POST", body: { transactions: [tx()] } });
  const reset = await request("/ghost-chains/reset", {
    method: "POST",
    body: { clearTransactions: true }
  });

  assert.equal(reset.response.status, 200);
  assert.deepEqual(reset.json, { clearTransactions: true });
  assert.equal(engine.diagnostics().activeTransactions, 0);
});

test("invalid JSON and malformed transactions return 400 without partial mutation", async () => {
  const malformedJson = await request("/ghost-chains/transactions", { method: "POST", body: "{" });
  assert.equal(malformedJson.response.status, 400);

  const malformedBatch = await request("/ghost-chains/transactions", {
    method: "POST",
    body: { transactions: [tx(), tx({ txId: "bad", amount: "not-a-number" })] }
  });
  assert.equal(malformedBatch.response.status, 400);
  assert.equal(engine.diagnostics().activeTransactions, 0);
});

test("conflicting duplicate IDs return 409 without partial mutation", async () => {
  const result = await request("/ghost-chains/transactions", {
    method: "POST",
    body: { transactions: [tx(), tx({ toUserId: "different" })] }
  });

  assert.equal(result.response.status, 409);
  assert.equal(result.json.error, "duplicate_tx_id");
  assert.equal(engine.diagnostics().activeTransactions, 0);
});

test("empty batches are valid no-ops", async () => {
  const { response, json } = await request("/ghost-chains/transactions", {
    method: "POST",
    body: { transactions: [] }
  });
  assert.equal(response.status, 200);
  assert.deepEqual(json, { transactions: [] });
});

test("routing returns 404 and method mismatches return 405 with Allow", async () => {
  const missing = await request("/does-not-exist");
  assert.equal(missing.response.status, 404);

  const wrongMethod = await request("/ghost-chains/health", { method: "POST" });
  assert.equal(wrongMethod.response.status, 405);
  assert.equal(wrongMethod.response.headers.get("allow"), "GET");
});
