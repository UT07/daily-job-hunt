import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // argsIgnorePattern mirrors the existing varsIgnorePattern convention
      // (capitalized/`_`-prefixed = intentionally unchecked, matching React
      // component-naming). Without it, a destructured-and-renamed function
      // parameter like `function AssetIcon({ icon: Icon })` was flagged as
      // "defined but never used" even when used as a JSX tag name
      // (`<Icon />`) in the function body — audit-dashboard.md P2-12 confirmed
      // this as a false positive in JobTable.jsx, Sidebar.jsx and
      // MobileNav.jsx, all of which use the same `icon: Icon` pattern.
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^[A-Z_]' }],
    },
  },
])
