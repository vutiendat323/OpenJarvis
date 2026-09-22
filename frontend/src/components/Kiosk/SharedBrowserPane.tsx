import { ArrowLeft, ArrowRight, Keyboard, RotateCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent, PointerEvent, TouchEvent, WheelEvent } from 'react';

import { remotePoint } from '@/hooks/useSharedBrowser';
import type { BrowserCommand, BrowserViewState } from '@/hooks/useSharedBrowser';

type BrowserController = BrowserViewState & { send: (command: BrowserCommand) => void };

export function SharedBrowserPane({ browser }: { browser: BrowserController }) {
  const [address, setAddress] = useState(browser.url);
  const viewportRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const moveTimeRef = useRef(0);
  const composingRef = useRef(false);
  const touchRef = useRef(false);
  const editable = browser.focusedElement?.tag === 'input' || browser.focusedElement?.tag === 'textarea' || browser.focusedElement?.editable;

  useEffect(() => {
    if (!touchRef.current) return;
    if (editable) inputRef.current?.focus({ preventScroll: true });
    else inputRef.current?.blur();
  }, [editable, browser.focusedElement?.name]);

  useEffect(() => setAddress(browser.url), [browser.url]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.round(entry.contentRect.width);
      const height = Math.round(entry.contentRect.height);
      if (width >= 64 && height >= 64) browser.send({ type: 'resize', width, height });
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [browser.send, browser.status]);

  const point = (clientX: number, clientY: number) => {
    const viewport = viewportRef.current;
    if (!viewport || !browser.width || !browser.height) return null;
    return remotePoint(clientX, clientY, viewport.getBoundingClientRect(), browser.width, browser.height);
  };

  const navigate = (event: FormEvent) => {
    event.preventDefault();
    const value = address.trim();
    if (!value) return;
    const url = /^https?:\/\//i.test(value) ? value : `https://${value}`;
    browser.send({ type: 'navigate', url });
  };

  const pointer = (event: PointerEvent<HTMLDivElement>, action: 'pressed' | 'released' | 'moved') => {
    if (event.pointerType === 'touch') return;
    touchRef.current = false;
    const position = point(event.clientX, event.clientY);
    if (!position) return;
    if (action === 'pressed') viewportRef.current?.focus();
    if (action === 'moved' && cursorRef.current) {
      cursorRef.current.style.transform = `translate(${event.clientX - event.currentTarget.getBoundingClientRect().left}px, ${event.clientY - event.currentTarget.getBoundingClientRect().top}px)`;
    }
    if (action === 'moved') {
      const now = performance.now();
      if (now - moveTimeRef.current < 16) return;
      moveTimeRef.current = now;
    }
    if (action === 'pressed') event.currentTarget.setPointerCapture(event.pointerId);
    const button = event.button === 2 ? 'right' : event.button === 1 ? 'middle' : 'left';
    browser.send({ type: 'pointer', event: action, ...position, button: action === 'moved' ? (event.buttons ? 'left' : 'none') : button });
    if (action === 'released') inputRef.current?.focus({ preventScroll: true });
  };

  const wheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const position = point(event.clientX, event.clientY);
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? browser.height : 1;
    if (position) browser.send({ type: 'wheel', ...position, delta_x: event.deltaX * unit, delta_y: event.deltaY * unit });
  };

  const touch = (event: TouchEvent<HTMLDivElement>, action: 'start' | 'move' | 'end' | 'cancel') => {
    touchRef.current = true;
    event.preventDefault();
    const current = event.changedTouches[0];
    if (!current) return;
    const position = point(current.clientX, current.clientY);
    if (position) browser.send({ type: 'touch', event: action, ...position });
  };

  return (
    <section data-testid="shared-browser-pane" className="flex min-h-0 min-w-0 flex-1 flex-col bg-[#1d1f22] text-white">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-white/10 px-3">
        <button type="button" aria-label="Back" onClick={() => browser.send({ type: 'back' })} className="rounded p-2 hover:bg-white/10"><ArrowLeft size={16} /></button>
        <button type="button" aria-label="Forward" onClick={() => browser.send({ type: 'forward' })} className="rounded p-2 hover:bg-white/10"><ArrowRight size={16} /></button>
        <button type="button" aria-label="Reload" onClick={() => browser.send({ type: 'reload' })} className="rounded p-2 hover:bg-white/10"><RotateCw size={16} /></button>
        <form onSubmit={navigate} className="min-w-0 flex-1">
          <input aria-label="Browser address" value={address} onChange={(event) => setAddress(event.target.value)} spellCheck={false} className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm outline-none focus:border-cyan-400" />
        </form>
        {editable && <button type="button" aria-label="Type in focused field" onClick={() => inputRef.current?.focus({ preventScroll: true })} className="rounded p-2 hover:bg-white/10"><Keyboard size={16} /></button>}
        {browser.agentAction && <span role="status" className="shrink-0 text-xs text-cyan-300">AI working…</span>}
        <span className="hidden shrink-0 text-xs text-white/60 sm:inline">{browser.status === 'disconnected' ? 'Reconnecting…' : browser.status === 'connected' ? (browser.loading ? 'Loading…' : 'Live') : 'Connecting…'}</span>
      </div>
      {browser.error && <div role="alert" className="bg-red-950 px-3 py-1 text-xs text-red-200">{browser.error}</div>}
      <div
        ref={viewportRef}
        role="application"
        aria-label="Shared browser viewport"
        tabIndex={0}
        className="relative min-h-0 flex-1 cursor-crosshair overflow-hidden outline-none"
        style={{ touchAction: 'none' }}
        onPointerDown={(event) => pointer(event, 'pressed')}
        onPointerUp={(event) => pointer(event, 'released')}
        onPointerMove={(event) => pointer(event, 'moved')}
        onContextMenu={(event) => event.preventDefault()}
        onWheel={wheel}
        onTouchStart={(event) => touch(event, 'start')}
        onTouchMove={(event) => touch(event, 'move')}
        onTouchEnd={(event) => touch(event, 'end')}
        onTouchCancel={(event) => touch(event, 'cancel')}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || composingRef.current) return;
          if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.target === inputRef.current) return;
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') return;
          event.preventDefault();
          const modifiers = (event.altKey ? 1 : 0) | (event.ctrlKey ? 2 : 0) | (event.metaKey ? 4 : 0) | (event.shiftKey ? 8 : 0);
          browser.send({ type: 'key', event: 'down', key: event.key, code: event.code, key_code: event.keyCode, modifiers, text: event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey ? event.key : '' });
        }}
        onKeyUp={(event) => {
          event.preventDefault();
          browser.send({ type: 'key', event: 'up', key: event.key, code: event.code, key_code: event.keyCode });
        }}
        onPaste={(event) => {
          event.preventDefault();
          browser.send({ type: 'text', text: event.clipboardData.getData('text') });
        }}
      >
        <textarea ref={inputRef} aria-label="Type into shared browser" autoComplete="off" autoCapitalize="off" spellCheck={false} className="pointer-events-none absolute h-px w-px opacity-0"
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={(event) => {
            composingRef.current = false;
            if (event.currentTarget.value) browser.send({ type: 'text', text: event.currentTarget.value });
            event.currentTarget.value = '';
          }}
          onInput={(event) => {
            if (composingRef.current) return;
            if (event.currentTarget.value) browser.send({ type: 'text', text: event.currentTarget.value });
            event.currentTarget.value = '';
          }}
        />
        {browser.frame ? (
          <img src={browser.frame} alt={browser.title || 'Shared browser page'} draggable={false} className="h-full w-full select-none object-fill" />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-white/50">Connecting to shared browser…</div>
        )}
        <div ref={cursorRef} aria-hidden className="pointer-events-none absolute left-0 top-0 h-3 w-3 rounded-full border-2 border-cyan-300 opacity-70" />
      </div>
    </section>
  );
}
