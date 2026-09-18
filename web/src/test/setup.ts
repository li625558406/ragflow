// 测试环境兜底：jsdom 缺失的浏览器/Node API 桩（缺口在 harness，不在产品代码）。
// 全部用「存在即不覆盖」的守卫式注入，与 Node/jsdom 版本演进兼容。
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import * as nodeUtil from 'node:util';
import { afterEach } from 'vitest';

// RTL cleanup：显式挂一道，防 transform/环境差异导致自动 cleanup 失效。
afterEach(() => cleanup());

// react-router 的 development 构建在模块顶层 new TextEncoder()——只要 import 到
// routes.tsx 的链路（logic-hooks → route-hook）就会在模块求值期用到。
if (!(globalThis as any).TextEncoder) {
  (globalThis as any).TextEncoder = nodeUtil.TextEncoder;
}
if (!(globalThis as any).TextDecoder) {
  (globalThis as any).TextDecoder = nodeUtil.TextDecoder;
}
if (!(globalThis as any).structuredClone) {
  (globalThis as any).structuredClone = (nodeUtil as any).structuredClone;
}

// routes.tsx 在模块作用域 createBrowserRouter() → new Request(...)。以下是最小桩
// （只保证构造不抛错）：若有用例真发请求，必须换真实现，否则是「静默假成功」。
class StubHeaders {
  private map = new Map<string, string>();
  constructor(init?: any) {
    if (init instanceof StubHeaders) {
      init.forEach((v: string, k: string) => this.map.set(k, v));
    } else if (Array.isArray(init)) {
      for (const [k, v] of init)
        this.map.set(String(k).toLowerCase(), String(v));
    } else if (init && typeof init === 'object') {
      for (const [k, v] of Object.entries(init)) {
        this.map.set(String(k).toLowerCase(), String(v));
      }
    }
  }
  get(k: string) {
    return this.map.get(String(k).toLowerCase()) ?? null;
  }
  set(k: string, v: string) {
    this.map.set(String(k).toLowerCase(), String(v));
  }
  has(k: string) {
    return this.map.has(String(k).toLowerCase());
  }
  forEach(cb: (v: string, k: string) => void) {
    this.map.forEach((v, k) => cb(v, k));
  }
  entries() {
    return this.map.entries();
  }
}

class StubRequest {
  url: string;
  method: string;
  headers: any;
  body: any;
  signal: any;
  constructor(input: any, init: any = {}) {
    // 同时接受 Request 实例与 URL 字符串（react-router 两种都用）。
    const base =
      typeof input === 'string' ? input : (input?.url ?? String(input));
    this.url = base;
    this.method = (init.method ?? input?.method ?? 'GET').toUpperCase();
    this.headers = new StubHeaders(init.headers);
    this.body = init.body ?? null;
    this.signal = init.signal ?? null;
  }
  clone() {
    return new StubRequest(this.url, {
      method: this.method,
      headers: this.headers,
      body: this.body,
      signal: this.signal,
    });
  }
}

class StubResponse {
  status: number;
  statusText: string;
  headers: any;
  body: any;
  ok: boolean;
  constructor(body: any = null, init: any = {}) {
    this.body = body;
    this.status = init.status ?? 200;
    this.statusText = init.statusText ?? '';
    this.headers = new StubHeaders(init.headers);
    this.ok = this.status >= 200 && this.status < 300;
  }
  async json() {
    return JSON.parse(this.body ?? 'null');
  }
  async text() {
    return String(this.body ?? '');
  }
}

for (const [key, val] of [
  ['Request', StubRequest],
  ['Response', StubResponse],
  ['Headers', StubHeaders],
] as const) {
  if (!(globalThis as any)[key]) (globalThis as any)[key] = val;
}

// jsdom 未实现滚动 API（useScrollToBottom 等直接调 container.scrollTo）。
// 补空实现（用 noop 而非 throw：滚动不是这些用例的断言对象）。
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {} as any;
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {} as any;
}
