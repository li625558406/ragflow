import react from '@vitejs/plugin-react';
import path from 'node:path';
import { defineConfig } from 'vitest/config';

// 单测配置独立于产物构建（vite.config.ts 是 build 配置）。环境/别名与已删除的
// 本地 jest 脚手架（.scratch/jest.local.cjs）等价；CSS 按默认策略返回空模块。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@parent': path.resolve(__dirname, '..'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/__tests__/**/*.test.{ts,tsx}'],
  },
});
