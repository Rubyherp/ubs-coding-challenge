import { DuplicateConflictError } from "./risk-engine.js";
import { normalizeTransaction, ValidationError } from "../../phase-1/src/transaction.js";

const MAX_BODY_BYTES = 5 * 1024 * 1024;
const MAX_BATCH_SIZE = 10_000;

export function createHandler(engine) {
  return async function handler(request, response) {
    try {
      const url = new URL(request.url, "http://localhost");

      if (url.pathname === "/ghost-chains/health") {
        if (request.method !== "GET") return methodNotAllowed(response, ["GET"]);
        return sendJson(response, 200, { status: "ok" });
      }

      if (url.pathname === "/ghost-chains/reset") {
        if (request.method !== "POST") return methodNotAllowed(response, ["POST"]);
        const body = await readJson(request);
        if (body === null || typeof body !== "object" || Array.isArray(body) || body.clearTransactions !== true) {
          throw new ValidationError("clearTransactions must be true");
        }
        engine.reset();
        return sendJson(response, 200, { clearTransactions: true });
      }

      if (url.pathname === "/ghost-chains/transactions") {
        if (request.method !== "POST") return methodNotAllowed(response, ["POST"]);
        const body = await readJson(request);
        if (body === null || typeof body !== "object" || Array.isArray(body) || !Array.isArray(body.transactions)) {
          throw new ValidationError("transactions must be an array");
        }
        if (body.transactions.length > MAX_BATCH_SIZE) {
          throw new ValidationError(`transactions must contain at most ${MAX_BATCH_SIZE} items`);
        }

        // Normalize the entire request before mutating state, preventing partial
        // application when a later item is malformed.
        const transactions = body.transactions.map(normalizeTransaction);
        const results = engine.processBatch(transactions);
        return sendJson(response, 200, { transactions: results });
      }

      return sendJson(response, 404, { error: "not_found" });
    } catch (error) {
      if (error instanceof ValidationError || error instanceof SyntaxError) {
        return sendJson(response, 400, { error: "invalid_request", message: error.message });
      }
      if (error instanceof DuplicateConflictError) {
        return sendJson(response, 409, { error: "duplicate_tx_id", message: error.message });
      }
      if (error?.code === "BODY_TOO_LARGE") {
        return sendJson(response, 413, { error: "payload_too_large" });
      }

      console.error("Unhandled request error", error);
      return sendJson(response, 500, { error: "internal_error" });
    }
  };
}

async function readJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) {
      const error = new Error("request body is too large");
      error.code = "BODY_TOO_LARGE";
      throw error;
    }
    chunks.push(chunk);
  }
  if (size === 0) throw new ValidationError("request body must contain JSON");
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function methodNotAllowed(response, methods) {
  response.setHeader("Allow", methods.join(", "));
  return sendJson(response, 405, { error: "method_not_allowed" });
}

function sendJson(response, status, body) {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
    "Cache-Control": "no-store"
  });
  response.end(payload);
}
