// @vitest-environment jsdom

import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  initialBrowserState,
  reduceBrowserMessage,
  remotePoint,
  useSharedBrowser,
} from './useSharedBrowser';

class TestWebSocket {
  static OPEN = 1;
  static instances: TestWebSocket[] = [];
  readyState = TestWebSocket.OPEN;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor() {
    TestWebSocket.instances.push(this);
  }

  emit(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
}

describe('shared browser state', () => {
  it.each([
    { format: undefined, data: '/9j/abc', mime: 'jpeg' },
    { format: 'png', data: 'iVBORabc', mime: 'png' },
  ])('updates the URL and $mime frame together', ({ format, data, mime }) => {
    const next = reduceBrowserMessage(initialBrowserState, {
      type: 'frame',
      data,
      format,
      url: 'https://example.test/menu',
      title: 'Menu',
      width: 800,
      height: 600,
    });

    expect(next.url).toBe('https://example.test/menu');
    expect(next.title).toBe('Menu');
    expect(next.frame).toBe(`data:image/${mime};base64,${data}`);
    expect(next.width).toBe(800);
    expect(next.height).toBe(600);
  });

  it('maps a displayed point to the remote viewport after scaling', () => {
    expect(remotePoint(150, 95, { left: 50, top: 20, width: 400, height: 300 }, 800, 600))
      .toEqual({ x: 200, y: 150 });
  });

  it('keeps frame updates from rerendering the Kiosk state owner', () => {
    TestWebSocket.instances = [];
    vi.stubGlobal('WebSocket', TestWebSocket);
    let renderCount = 0;

    const { result, unmount } = renderHook(() => {
      renderCount += 1;
      return useSharedBrowser();
    });

    try {
      const socket = TestWebSocket.instances[0]!;
      act(() => socket.onopen?.(new Event('open')));
      act(() => socket.emit({
        type: 'state',
        url: 'https://example.test/menu',
        title: 'Menu',
        width: 800,
        height: 600,
        loading: false,
        focused_element: { tag: 'body', name: '', type: '' },
      }));
      const rendersBeforeFrame = renderCount;

      act(() => socket.emit({
        type: 'frame',
        format: 'jpeg',
        data: '/9j/abc',
        url: 'https://example.test/menu',
        title: 'Menu',
        width: 800,
        height: 600,
        loading: false,
        focused_element: { tag: 'body', name: '', type: '' },
      }));

      expect(renderCount).toBe(rendersBeforeFrame);
      expect(result.current.frameStore.getSnapshot()).toBe('data:image/jpeg;base64,/9j/abc');

      act(() => socket.emit({ type: 'frame', data: '/9j/next', title: 'New menu' }));
      expect(result.current.title).toBe('New menu');
      expect(result.current.frameStore.getSnapshot()).toBe('data:image/jpeg;base64,/9j/next');
    } finally {
      unmount();
      vi.unstubAllGlobals();
    }
  });
});
