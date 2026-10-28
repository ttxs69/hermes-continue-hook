// ESLint flat config — covers every JS/TS file this repository ships.
//
// The package ships five hand-written modules (the installer, the OpenCode
// plugin, the Pi extension, a packaging helper and the dsh plugin). Before this
// file existed none of them were linted at all: the only static check was the
// dsh job's tc, which compiles one directory.
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
      "**/*.d.ts",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // Node-only code: the installer, the tests and the packaging helper.
    files: ["bin/**/*.js", "tests/**/*.mjs", "scripts/**/*.mjs"],
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
    // Host plugins run inside their own runtime; `any` is the only honest type
    // for a third-party SDK surface that is not a dependency of this package.
    files: ["adapters/**/*.js", "adapters/**/*.ts"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        process: "readonly",
        console: "readonly",
        Buffer: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        AbortController: "readonly",
        fetch: "readonly",
      },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    files: ["tests/**/*.mjs"],
    rules: {
      // Tests print their own progress.
      "no-console": "off",
    },
  },
);
