import { defineConfig } from "vitest/config";

// Vitest is the pytest of this repository: it runs the JS suites, reports
// coverage, and fails the build when coverage drops.
//
// Scope of the coverage number, stated honestly:
//
//   adapters/opencode  imported by its tests  → real statement coverage
//   bin/, adapters/pi, scripts                 run as a *subprocess* (that is
//                                              the only faithful way to test an
//                                              installer that edits $HOME), so
//                                              V8 coverage in this process
//                                              cannot see them
//
// The installer therefore has behavioural coverage — 33 installer tests and 15
// lifecycle invariants, all driving the real CLI in a throwaway HOME — but not
// statement coverage. Counting it as 0% would be a lie in the other direction,
// so it is excluded rather than averaged in.
export default defineConfig({
  test: {
    include: ["tests/**/*.test.mjs"],
    // Every suite spawns the CLI in a throwaway HOME; the dsh suite also builds
    // its plugin with tsc, which is slow enough to need headroom.
    testTimeout: 60_000,
    hookTimeout: 60_000,
    // The dsh suite writes into a fixed fixture directory.
    fileParallelism: false,
    coverage: {
      provider: "v8",
      reporter: ["text"],
      include: ["adapters/opencode/**/*.js"],
      thresholds: {
        lines: 80,
        functions: 95,
        statements: 80,
        branches: 65,
      },
    },
  },
});
