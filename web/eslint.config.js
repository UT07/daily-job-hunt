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
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
    },
  },
  {
    // The Playwright suite and its config run in Node, not the browser, and
    // they are not React. Two rules in particular are false positives here:
    // `react-hooks/rules-of-hooks` fires on Playwright's `use()` callback
    // (which is a fixture's teardown hook, not React's), and unused
    // destructured fixtures are load-bearing -- naming a fixture is how you
    // ask for it, even when the body never touches the handle.
    files: ['e2e/**/*.js', 'playwright.config.js'],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
    rules: {
      'react-hooks/rules-of-hooks': 'off',
      'react-refresh/only-export-components': 'off',
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^[A-Z_]',
        args: 'none',
        destructuredArrayIgnorePattern: '^_',
      }],
    },
  },
])
