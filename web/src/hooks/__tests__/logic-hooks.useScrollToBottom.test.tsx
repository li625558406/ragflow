import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

vi.mock('eventsource-parser/stream', () => ({}));

import { act, renderHook } from '@testing-library/react';
import { useScrollToBottom } from '../logic-hooks';

function createMockContainer({ atBottom = true } = {}) {
  const scrollTop = atBottom ? 100 : 0;
  const clientHeight = 100;
  const scrollHeight = 200;
  const listeners: Record<string, (event: Event) => void> = {};
  return {
    current: {
      scrollTop,
      clientHeight,
      scrollHeight,
      scrollTo: vi.fn(),
      addEventListener: vi.fn((event, cb) => {
        listeners[event] = cb;
      }),
      removeEventListener: vi.fn(),
    },
    listeners,
  } as any;
}

// Helper to flush all timers and microtasks
async function flushAll() {
  vi.runAllTimers();
  // Flush microtasks
  await Promise.resolve();
  // Sometimes, effects queue more timers, so run again
  vi.runAllTimers();
  await Promise.resolve();
}

describe('useScrollToBottom', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('should set isAtBottom true when user is at bottom', () => {
    const containerRef = createMockContainer({ atBottom: true });
    const { result } = renderHook(() => useScrollToBottom([], containerRef));
    expect(result.current.isAtBottom).toBe(true);
  });

  it('should set isAtBottom false when user is not at bottom', () => {
    const containerRef = createMockContainer({ atBottom: false });
    const { result } = renderHook(() => useScrollToBottom([], containerRef));
    expect(result.current.isAtBottom).toBe(false);
  });

  it('should scroll to bottom when isAtBottom is true and messages change', async () => {
    const containerRef = createMockContainer({ atBottom: true });

    const { rerender } = renderHook(
      ({ messages }) => useScrollToBottom(messages, containerRef),
      { initialProps: { messages: [] as string[] } },
    );

    rerender({ messages: ['msg1'] });
    await flushAll();

    expect(containerRef.current.scrollTo).toHaveBeenCalled();
  });

  it('should NOT scroll to bottom when isAtBottom is false and messages change', async () => {
    const containerRef = createMockContainer({ atBottom: false });

    const { rerender } = renderHook(
      ({ messages }) => useScrollToBottom(messages, containerRef),
      { initialProps: { messages: [] as string[] } },
    );

    // Simulate user scrolls up before messages change
    await act(async () => {
      containerRef.current.scrollTop = 0;
      containerRef.current.addEventListener.mock.calls[0][1]();
      await flushAll();
      // Advance fake timers by 10ms instead of real setTimeout
      vi.advanceTimersByTime(10);
    });

    rerender({ messages: ['msg1'] });
    await flushAll();

    expect(containerRef.current.scrollTo).not.toHaveBeenCalled();

    // Optionally, flush again after the assertion to see if it gets called late
    await flushAll();
  });

  it('should indicate button should appear when user is not at bottom', () => {
    const containerRef = createMockContainer({ atBottom: false });
    const { result } = renderHook(() => useScrollToBottom([], containerRef));
    // The button should appear in the UI when isAtBottom is false
    expect(result.current.isAtBottom).toBe(false);
  });
});

const originalRAF = global.requestAnimationFrame;
beforeAll(() => {
  global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
});
afterAll(() => {
  global.requestAnimationFrame = originalRAF;
});
