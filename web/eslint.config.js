// Lint for the site: ESLint's and typescript-eslint's recommended rules, the
// rules of hooks, and Vite's fast-refresh rule. Layout is Prettier's job, so
// eslint-config-prettier turns off every rule that would fight it.
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  // Generated from docs/openapi.json by `npm run gen:api`.
  globalIgnores(["dist", "test-results", "playwright-report", "src/api/schema.d.ts"]),
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
]);
