import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // Some hooks (e.g. useUserProfile.js) live under hooks/ as .js but render
  // JSX. Tell the React plugin to transform JSX in .js files. The oxc.lang
  // override teaches Vite's built-in oxc transform to parse .js as JSX so it
  // doesn't reject it before plugin-react sees it.
  plugins: [react({ include: /\.(js|jsx|ts|tsx)$/ })],
  oxc: {
    lang: 'jsx',
    include: /\.(js|jsx|ts|tsx)$/,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    globals: true,
  },
})
