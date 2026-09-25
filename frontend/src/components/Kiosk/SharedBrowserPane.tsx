import { ArrowLeft, ArrowRight, Keyboard, RotateCw, AppWindow, Columns2, Maximize2, Minimize2, ZoomIn, ZoomOut, GripVertical } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent, PointerEvent, TouchEvent, WheelEvent } from 'react';

import { remotePoint } from '@/hooks/useSharedBrowser';
import type { BrowserCommand, BrowserViewState } from '@/hooks/useSharedBrowser';

type BrowserController = BrowserViewState & { send: (command: BrowserCommand) => void };

export interface SharedBrowserPaneProps {
  browser: BrowserController;
  isFloating?: boolean;
  isMaximized?: boolean;
  onToggleFloating?: () => void;
  onToggleMaximize?: () => void;
  dragHandleProps?: Record<string, unknown>;
  onZoom?: (delta: number) => void;
}

export function SharedBrowserPane({
  browser,
  isFloating = false,
  isMaximized = false,
  onToggleFloating,
  onToggleMaximize,
  dragHandleProps,
  onZoom,
}: SharedBrowserPaneProps) {
  const [address, setAddress] = useState(browser.url);
  const [chromeHovered, setChromeHovered] = useState(false);
  const [chromePinned, setChromePinned] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const moveTimeRef = useRef(0);
  const composingRef = useRef(false);
  const touchRef = useRef(false);
  const editable = browser.focusedElement?.tag === 'input' || browser.focusedElement?.tag === 'textarea' || browser.focusedElement?.editable;
  const chromeVisible = chromeHovered || chromePinned;

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
    <section
      data-testid="shared-browser-pane"
      className="relative flex h-full w-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-[#1d1f22] text-white"
    >
      <div
        className="absolute inset-x-0 top-0 z-20 flex flex-col transition-transform duration-200 ease-out"
        style={{ transform: chromeVisible ? 'translateY(0)' : 'translateY(calc(-100% + 12px))' }}
        onMouseEnter={() => setChromeHovered(true)}
        onMouseLeave={() => setChromeHovered(false)}
        onFocusCapture={() => setChromeHovered(true)}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setChromeHovered(false);
        }}
      >
        <div
          data-testid="browser-controls"
          aria-hidden={!chromeVisible}
          className={`flex h-12 shrink-0 items-center gap-2 border-b border-white/10 bg-[#1d1f22] px-3 select-none ${
            chromeVisible ? 'visible pointer-events-auto' : 'invisible pointer-events-none'
          }`}
          onDoubleClick={(e) => {
            if (isFloating && onToggleMaximize) {
              e.stopPropagation();
              onToggleMaximize();
            }
          }}
        >
          {isFloating && (
            <div
              aria-label="Drag window"
              title="Drag to move (Kéo để di chuyển)"
              className="flex items-center p-1 text-white/40 hover:text-white pointer-events-none"
            >
              <GripVertical size={16} />
            </div>
          )}
          <button
            type="button"
            aria-label="Back"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => browser.send({ type: 'back' })}
            className="rounded p-2 hover:bg-white/10"
          >
            <ArrowLeft size={16} />
          </button>
          <button
            type="button"
            aria-label="Forward"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => browser.send({ type: 'forward' })}
            className="rounded p-2 hover:bg-white/10"
          >
            <ArrowRight size={16} />
          </button>
          <button
            type="button"
            aria-label="Reload"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => browser.send({ type: 'reload' })}
            className="rounded p-2 hover:bg-white/10"
          >
            <RotateCw size={16} />
          </button>
          <form
            onSubmit={navigate}
            onPointerDown={(e) => e.stopPropagation()}
            className="min-w-0 flex-1"
          >
            <input
              aria-label="Browser address"
              value={address}
              onPointerDown={(e) => e.stopPropagation()}
              onChange={(event) => setAddress(event.target.value)}
              spellCheck={false}
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm outline-none focus:border-cyan-400"
            />
          </form>
          {editable && (
            <button
              type="button"
              aria-label="Type in focused field"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => inputRef.current?.focus({ preventScroll: true })}
              className="rounded p-2 hover:bg-white/10"
            >
              <Keyboard size={16} />
            </button>
          )}
          {browser.agentAction && <span role="status" className="shrink-0 text-xs text-cyan-300">AI working…</span>}
          <span className="hidden shrink-0 text-xs text-white/60 sm:inline">
            {browser.status === 'disconnected' ? 'Reconnecting…' : browser.status === 'connected' ? (browser.loading ? 'Loading…' : 'Live') : 'Connecting…'}
          </span>
          {isFloating && onToggleMaximize && (
            <button
              type="button"
              aria-label={isMaximized ? 'Restore size' : 'Maximize'}
              title={isMaximized ? 'Thu nhỏ lại' : 'Phóng to hết cỡ'}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onToggleMaximize();
              }}
              className="rounded p-1.5 hover:bg-white/10 text-white/70 hover:text-white cursor-pointer"
            >
              {isMaximized ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          )}
          {onToggleFloating && (
            <button
              type="button"
              aria-label={isFloating ? 'Dock to split' : 'Pop out to floating window'}
              title={isFloating ? 'Quay lại dạng chia đôi [7:3]' : 'Mở thành cửa sổ nổi di chuyển tự do'}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onToggleFloating();
              }}
              className="rounded p-1.5 hover:bg-white/10 text-white/70 hover:text-white cursor-pointer"
            >
              {isFloating ? <Columns2 size={15} /> : <AppWindow size={15} />}
            </button>
          )}
        </div>
        <button
          data-testid="browser-controls-handle"
          type="button"
          aria-label={chromeVisible ? 'Hide browser controls' : 'Show browser controls'}
          aria-expanded={chromeVisible}
          title="Browser controls"
          {...(isFloating ? dragHandleProps : {})}
          onClick={() => {
            setChromeHovered(false);
            setChromePinned((pinned) => !pinned);
          }}
          className={`mx-auto flex h-3 w-14 shrink-0 items-center justify-center rounded-b-md border border-t-0 border-[#7b5737]/30 bg-[#f4e6cf] shadow-sm ${
            isFloating ? 'cursor-grab active:cursor-grabbing' : ''
          }`}
        >
          <span aria-hidden="true" className="h-0.5 w-6 rounded-full bg-[#7b5737]/60" />
        </button>
      </div>
      {onZoom && (
        <div
          data-testid="browser-zoom-controls"
          className="absolute bottom-2 right-2 z-30 flex items-center gap-1 opacity-40 transition-opacity hover:opacity-100 focus-within:opacity-100"
          onPointerDown={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            aria-label="Zoom out"
            title="Thu nhỏ"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onZoom(-1)}
            className="rounded p-1.5 text-[#7b5737]/70 hover:text-[#4b2d17] cursor-pointer"
          >
            <ZoomOut size={15} />
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            title="Phóng to"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onZoom(1)}
            className="rounded p-1.5 text-[#7b5737]/70 hover:text-[#4b2d17] cursor-pointer"
          >
            <ZoomIn size={15} />
          </button>
        </div>
      )}
      {browser.error && <div role="alert" className="bg-red-950 px-3 py-1 text-xs text-red-200">{browser.error}</div>}
      <div
        ref={viewportRef}
        role="application"
        aria-label="Shared browser viewport"
        tabIndex={0}
        className="relative min-h-0 w-full flex-1 cursor-default overflow-hidden outline-none"
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
      </div>
    </section>
  );
}
