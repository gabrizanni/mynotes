import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without globals, so Testing Library's auto-cleanup needs this hook.
afterEach(() => {
  cleanup();
});
