import { describe, expect, it } from 'vitest';

import {
  initialBrowserState,
  reduceBrowserMessage,
  remotePoint,
} from './useSharedBrowser';

describe('shared browser state', () => {
  it('updates the URL and latest frame together', () => {
    const next = reduceBrowserMessage(initialBrowserState, {
      type: 'frame',
      data: '/9j/abc',
      url: 'https://example.test/menu',
      title: 'Menu',
      width: 800,
      height: 600,
    });

    expect(next.url).toBe('https://example.test/menu');
    expect(next.title).toBe('Menu');
    expect(next.frame).toBe('data:image/jpeg;base64,/9j/abc');
    expect(next.width).toBe(800);
    expect(next.height).toBe(600);
  });

  it('maps a displayed point to the remote viewport after scaling', () => {
    expect(remotePoint(150, 95, { left: 50, top: 20, width: 400, height: 300 }, 800, 600))
      .toEqual({ x: 200, y: 150 });
  });
});
