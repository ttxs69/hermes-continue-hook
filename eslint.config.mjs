// ESLint flat config — covers every JS/TS file this repository ships.
//
// Two layers, because they catch different things:
//
//   recommended            syntax and API misuse, no type information needed
//   recommendedTypeChecked uses the TypeScript program, which is what catches
//                          the errors this codebase actually produces: a
//                          promise nobody awaits (a nudge silently dropped), a
//                          promise used where a boolean was meant, an `await`
//                          on something that is not thenable
//
// Type-aware rules need a tsconfig that includes the file. tsconfig.json
// covers the TS sources, tsconfig.checkjs.json the JavaScript ones, and
// `allowDefaultProject` covers the test files, which are deliberately not part
// of any build.
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "node_modules/**",
      "dist/**",
      "build/**",
      "patch-mode/**",
      "adapters/dsh/lib/**",
      "coverage/**",
      "**/*.d.ts",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        process: "readonly",
        console: "readonly",
        Buffer: "readonly",
        URL: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        AbortController: "readonly",
        fetch: "readonly",
      },
    },
  },
  {
    // Type-aware layer, applied only where a tsconfig actually covers the file.
    files: ["bin/**/*.js", "adapters/**/*.{js,ts}", "scripts/**/*.mjs", "vitest.config.mjs"],
    extends: [...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: {
        project: ["./tsconfig.json", "./tsconfig.checkjs.json", "./adapters/dsh/tsconfig.json"],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      // Host plugins run inside their own runtime; `any` is the only honest
      // type for a third-party SDK surface that is not a dependency here.
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // Every one of these is a real event in this codebase: the hook runs
      // inside someone's agent session, so a dropped promise is a dropped
      // nudge and a rejected one is a crashed host.
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/await-thenable": "error",
      "@typescript-eslint/require-await": "error",
      // A `catch {}` that swallows everything is the documented fail-open
      // policy; the rule is about *how* it is written, not whether.
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-return": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/restrict-template-expressions": "off",
      "@typescript-eslint/no-base-to-string": "off",
    },
  },
  {
    // Cyclomatic complexity ceiling.
    //
    // Measured before the gate went in: 47 (the installer's main), 21 (the
    // uninstall), 17 (the config reader), 16 (the hook patcher). Every one of
    // them was a decision table, a parse, or a per-kind dispatch written as a
    // chain of branches. The fix was to move the decision into a pure
    // function and leave the shell to carry it out — not to sprinkle
    // `eslint-disable` over it.
    //
    // The ceiling is 10 and the highest function left in the shipped code is
    // 9. The headroom is deliberate.
    files: ["bin/**/*.js", "adapters/**/*.{js,ts}", "scripts/**/*.mjs"],
    rules: { complexity: ["error", 10] },
  },
  {
    files: ["tests/**/*.mjs"],
    rules: {
      // Tests print their own progress.
      "no-console": "off",
    },
  },
);
