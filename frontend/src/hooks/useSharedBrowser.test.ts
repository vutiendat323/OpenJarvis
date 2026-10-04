import { describe, expect, it } from 'vitest';

import {
  initialBrowserState,
  reduceBrowserMessage,
  remotePoint,
} from './useSharedBrowser';

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
});
