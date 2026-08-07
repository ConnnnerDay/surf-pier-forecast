import { existsSync, copyFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const MINIMUM_NODE = [24, 14, 0];

function invocationFor(command, args) {
  if (command === "pnpm" && process.env.npm_execpath) {
    return { command: process.execPath, args: [process.env.npm_execpath, ...args] };
  }
  const executable = process.platform === "win32" && command === "pnpm" ? "pnpm.cmd" : command;
  return { command: executable, args };
}

export function compareVersions(actual, minimum) {
  const parsed = actual.split(".").map((part) => Number.parseInt(part, 10));
  for (let index = 0; index < minimum.length; index += 1) {
    const difference = (parsed[index] ?? 0) - minimum[index];
    if (difference !== 0) return Math.sign(difference);
  }
  return 0;
}

export function ensureLocalFile(example, destination) {
  if (existsSync(destination)) return "kept";
  copyFileSync(example, destination);
  return "created";
}

export function commandAvailable(command, runner = spawnSync) {
  const invocation = invocationFor(command, ["--version"]);
  const result = runner(invocation.command, invocation.args, {
    encoding: "utf8",
    stdio: "pipe",
  });
  return result.status === 0;
}

export function runCommand(command, args, runner = spawnSync) {
  const invocation = invocationFor(command, args);
  const result = runner(invocation.command, invocation.args, {
    cwd: resolve(process.cwd()),
    stdio: "inherit",
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed with status ${result.status}`);
  }
}

export function environmentReport({ runner = spawnSync } = {}) {
  return {
    node: {
      available: true,
      detail: process.versions.node,
      supported: compareVersions(process.versions.node, MINIMUM_NODE) >= 0,
    },
    pnpm: {
      available: commandAvailable("pnpm", runner),
      detail: "required",
    },
    uv: {
      available: commandAvailable(process.env.SURF_PIER_UV_BIN ?? "uv", runner),
      detail: "required",
    },
    docker: {
      available: commandAvailable("docker", runner),
      detail: "optional unless --with-postgres is used",
    },
  };
}

export function requiredToolsAvailable(report) {
  return report.node.supported && report.pnpm.available && report.uv.available;
}
