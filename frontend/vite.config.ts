import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

export default defineConfig(({ mode }) => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const env = loadEnv(mode, root, 'API_');
  const target = env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000';
  const parsed = new URL(target);
  if (
    !['http:', 'https:'].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password
  ) {
    throw new Error(
      'API_PROXY_TARGET must be an HTTP(S) URL without credentials',
    );
  }
  return {
    plugins: [react()],
    // Terser avoids the native JS minifier; preserve the small CSS bundle as-is.
    build: { minify: 'terser', cssMinify: false },
    server: {
      port: 3000,
      strictPort: true,
      proxy: {
        '/api': { target, rewrite: (path) => path.replace(/^\/api/, '') },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./tests/setup.ts'],
      restoreMocks: true,
    },
  };
});
