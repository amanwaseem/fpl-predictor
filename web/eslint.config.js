// Lint for the site: ESLint's and typescript-eslint's recommended rules, the
// rules of hooks, and Vite's fast-refresh rule. Layout is Prettier's job, so
// eslint-config-prettier turns off every rule that would fight it.
import js from "@eslint/js";
import { fileURLToPath } from "node:url";
import prettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import { defineConfig, includeIgnoreFile } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  // What is built or generated: one list, Prettier's.
  includeIgnoreFile(fileURLToPath(new URL(".prettierignore", import.meta.url))),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
      prettier,
    ],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
  },
  // Run by Node, not the browser: the end-to-end tests and the tools' configs.
  {
    files: ["e2e/**/*.ts", "*.config.ts"],
    languageOptions: { globals: globals.node },
  },
]);
