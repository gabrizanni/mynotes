/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tauri expects a fixed port and must not see src-tauri changes as frontend changes.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**", "**/tests/shots/**"] },
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: {
    target: "es2022",
    sourcemap: false,
    assetsInlineLimit: 0,
    // Two pages: the main window and the recording capsule (src-tauri/src/capsule.rs).
    rollupOptions: { input: { main: "index.html", capsule: "capsule.html" } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["tests/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "tests/unit/**/*.test.{ts,tsx}"],
    // Stylesheets stay blank in tests, except `?raw` imports (the token contrast test reads them).
    css: { include: [/\.css\?raw$/], modules: { classNameStrategy: "non-scoped" } },
    restoreMocks: true,
  },
});
