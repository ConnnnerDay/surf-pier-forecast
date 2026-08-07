import { resolve } from "node:path";

import {
  ensureLocalFile,
  environmentReport,
  requiredToolsAvailable,
  runCommand,
} from "./workspace-lib.mjs";

function printReport(report) {
  for (const [name, result] of Object.entries(report)) {
    const status = result.available && result.supported !== false ? "ok" : "missing";
    console.log(`${status.padEnd(7)} ${name.padEnd(8)} ${result.detail}`);
  }
}

function doctor() {
  const report = environmentReport();
  printReport(report);
  if (!requiredToolsAvailable(report)) process.exitCode = 1;
}

function setup(withPostgres) {
  const report = environmentReport();
  printReport(report);
  if (!requiredToolsAvailable(report)) {
    throw new Error("Install the required tools reported above before running setup.");
  }
  if (withPostgres && !report.docker.available) {
    throw new Error("Docker is required when setup is run with --with-postgres.");
  }

  const webEnv = ensureLocalFile(
    resolve("apps/web/.env.example"),
    resolve("apps/web/.env.local"),
  );
  const apiEnv = ensureLocalFile(resolve("apps/api/.env.example"), resolve("apps/api/.env"));
  console.log(`${webEnv.padEnd(7)} apps/web/.env.local`);
  console.log(`${apiEnv.padEnd(7)} apps/api/.env`);

  runCommand("pnpm", ["install", "--frozen-lockfile"]);
  runCommand(process.env.SURF_PIER_UV_BIN ?? "uv", [
    "--directory",
    "apps/api",
    "sync",
    "--frozen",
  ]);
  if (withPostgres) runCommand("docker", ["compose", "up", "-d", "--wait", "postgres"]);
  seed();
  console.log("Setup complete. Run pnpm dev:web and pnpm dev:api in separate terminals.");
}

function seed() {
  console.log(
    "Seed complete: the scaffold defines no application records. " +
      "This stable command will gain idempotent seed data with the database-model sprint.",
  );
}

const [command = "doctor", ...args] = process.argv.slice(2);

try {
  if (command === "doctor") doctor();
  else if (command === "setup") setup(args.includes("--with-postgres"));
  else if (command === "seed") seed();
  else throw new Error(`Unknown workspace command: ${command}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
}
