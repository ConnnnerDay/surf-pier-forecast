import { describe, expect, it } from "vitest";

import { productStatus } from "./product-status";

describe("productStatus", () => {
  it("describes the scaffold without claiming product functionality", () => {
    expect(productStatus()).toContain("foundation");
    expect(productStatus()).not.toContain("live forecast");
  });
});
