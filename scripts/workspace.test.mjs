import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  commandAvailable,
  compareVersions,
  ensureLocalFile,
  requiredToolsAvailable,
} from "./workspace-lib.mjs";

test("runtime version comparison accepts compatible Node 24 patches", () => {
  assert.equal(compareVersions("24.14.0", [24, 14, 0]), 0);
  assert.equal(compareVersions("24.16.0", [24, 14, 0]), 1);
  assert.equal(compareVersions("23.99.0", [24, 14, 0]), -1);
});

test("environment copies never overwrite an existing local secret", () => {
  const directory = mkdtempSync(join(tmpdir(), "surf-pier-"));
  const example = join(directory, "example");
  const destination = join(directory, "local");
  writeFileSync(example, "safe-example", "utf8");

  assert.equal(ensureLocalFile(example, destination), "created");
  writeFileSync(destination, "developer-secret", "utf8");
  assert.equal(ensureLocalFile(example, destination), "kept");
  assert.equal(readFileSync(destination, "utf8"), "developer-secret");
});

test("required-tool evaluation ignores optional Docker", () => {
  const report = {
    node: { available: true, supported: true },
    pnpm: { available: true },
    uv: { available: true },
    docker: { available: false },
  };
  assert.equal(requiredToolsAvailable(report), true);
});

test("command detection uses the injected runner", () => {
  const available = commandAvailable("example", () => ({ status: 0 }));
  const missing = commandAvailable("example", () => ({ status: 1 }));
  assert.equal(available, true);
  assert.equal(missing, false);
});
