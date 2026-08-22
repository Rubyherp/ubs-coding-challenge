import http from "node:http";
import { createHandler } from "./api.js";
import { RiskEngine } from "./risk-engine.js";

const port = parsePort(process.env.PORT ?? "8080");
const engine = new RiskEngine();
const server = http.createServer(createHandler(engine));

server.requestTimeout = 30_000;
server.headersTimeout = 35_000;
server.keepAliveTimeout = 5_000;

server.listen(port, "0.0.0.0", () => {
  console.log(`Ghost Chains listening on port ${port}`);
});

for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => {
    server.close((error) => {
      if (error) {
        console.error("Graceful shutdown failed", error);
        process.exitCode = 1;
      }
    });
  });
}

function parsePort(value) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > 65_535) {
    throw new Error("PORT must be an integer between 0 and 65535");
  }
  return parsed;
}
