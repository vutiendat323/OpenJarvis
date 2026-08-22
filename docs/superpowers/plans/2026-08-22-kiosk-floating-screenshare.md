# Kiosk Floating Screen Share Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the Kiosk Screen Share UI from a static full-screen background into a floating, draggable, and resizable Picture-in-Picture card inspired by Google AI Studio Playground.

**Architecture:** A pure geometry math utility (`floatingGeometry.ts`) handles bounds clamping and aspect-ratio-preserving resize calculations. A zero-dependency pointer events hook (`useDraggableResizable.ts`) drives position and size state. `ScreenShareView.tsx` renders the floating card with video stream and a bottom-right resize handle. `KioskPage.tsx` preserves `/display.html` as the persistent base layer and overlays the floating screen share when active.

**Tech Stack:** React 19, TypeScript, Lucide React icons, Tailwind CSS, Vitest.

## Global Constraints
- Target directory: `frontend/` in `OpenJarvis-v2` worktree.
- Zero extra external dependencies (use native Pointer Events with `setPointerCapture`).
- Never use `git stash`. Do not stage or modify the 9 untracked user paths.
- All tests must pass with `npm test` (`vitest run`).

---

### Task 1: Floating Geometry & Clamping Math Helpers

**Files:**
- Create: `frontend/src/utils/floatingGeometry.ts`
- Test: `frontend/src/utils/floatingGeometry.test.ts`

**Interfaces:**
- Consumes: Standard JS numbers and 2D coordinate/dimension objects.
- Produces:
  ```typescript
  export interface Point { x: number; y: number; }
  export interface Dimensions { width: number; height: number; }
  
  export function clampPosition(
    pos: Point,
    size: Dimensions,
    bounds: Dimensions,
    margin?: number,
  ): Point;
  
  export function computeResizeDimensions(
    startWidth: number,
    deltaX: number,
    aspectRatio: number,
    minWidth?: number,
    maxWidth?: number,
  ): Dimensions;
  
  export function getInitialPosition(
    size: Dimensions,
    bounds: Dimensions,
    offset?: { top?: number; right?: number },
  ): Point;
  ```

- [ ] **Step 1: Write the failing tests for geometry helpers**

Create `frontend/src/utils/floatingGeometry.test.ts`:
```typescript
import { describe, expect, it } from 'vitest';
import {
  clampPosition,
  computeResizeDimensions,
  getInitialPosition,
} from './floatingGeometry';

describe('floatingGeometry', () => {
  describe('clampPosition', () => {
    const size = { width: 380, height: 214 };
    const bounds = { width: 1920, height: 1080 };
    const margin = 16;

    it('keeps position within bounds when already inside', () => {
      const pos = { x: 500, y: 300 };
      expect(clampPosition(pos, size, bounds, margin)).toEqual({ x: 500, y: 300 });
    });

    it('clamps left and top margins', () => {
      const pos = { x: -50, y: -10 };
      expect(clampPosition(pos, size, bounds, margin)).toEqual({ x: 16, y: 16 });
    });

    it('clamps right and bottom margins', () => {
      const pos = { x: 1900, y: 1000 };
      expect(clampPosition(pos, size, bounds, margin)).toEqual({
        x: 1920 - 380 - 16,
        y: 1080 - 214 - 16,
      });
    });
  });

  describe('computeResizeDimensions', () => {
    const aspectRatio = 16 / 9;

    it('scales width and height proportionally with delta', () => {
      const res = computeResizeDimensions(380, 20, aspectRatio, 240, 800);
      expect(res.width).toBe(400);
      expect(res.height).toBe(225);
    });

    it('enforces minWidth constraint', () => {
      const res = computeResizeDimensions(250, -100, aspectRatio, 240, 800);
      expect(res.width).toBe(240);
      expect(res.height).toBe(135);
    });

    it('enforces maxWidth constraint', () => {
      const res = computeResizeDimensions(750, 100, aspectRatio, 240, 800);
      expect(res.width).toBe(800);
      expect(res.height).toBe(450);
    });
  });

  describe('getInitialPosition', () => {
    it('calculates top-right aligned offset correctly', () => {
      const size = { width: 380, height: 214 };
      const bounds = { width: 1920, height: 1080 };
      const pos = getInitialPosition(size, bounds, { top: 72, right: 24 });
      expect(pos).toEqual({
        x: 1920 - 380 - 24,
        y: 72,
      });
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test src/utils/floatingGeometry.test.ts` (in `frontend/`)
Expected: FAIL because `floatingGeometry.ts` does not exist yet.

- [ ] **Step 3: Implement floating geometry helpers**

Create `frontend/src/utils/floatingGeometry.ts`:
```typescript
export interface Point {
  x: number;
  y: number;
}

export interface Dimensions {
  width: number;
  height: number;
}

export function clampPosition(
  pos: Point,
  size: Dimensions,
  bounds: Dimensions,
  margin = 16,
): Point {
  const minX = margin;
  const minY = margin;
  const maxX = Math.max(minX, bounds.width - size.width - margin);
  const maxY = Math.max(minY, bounds.height - size.height - margin);

  return {
    x: Math.min(Math.max(pos.x, minX), maxX),
    y: Math.min(Math.max(pos.y, minY), maxY),
  };
}

export function computeResizeDimensions(
  startWidth: number,
  deltaX: number,
  aspectRatio: number,
  minWidth = 240,
  maxWidth = 1000,
): Dimensions {
  const targetWidth = Math.min(Math.max(startWidth + deltaX, minWidth), maxWidth);
  const targetHeight = Math.round(targetWidth / (aspectRatio > 0 ? aspectRatio : 16 / 9));
  return {
    width: Math.round(targetWidth),
    height: targetHeight,
  };
}

export function getInitialPosition(
  size: Dimensions,
  bounds: Dimensions,
  offset: { top?: number; right?: number } = { top: 72, right: 24 },
): Point {
  const right = offset.right ?? 24;
  const top = offset.top ?? 72;
  const initialX = bounds.width > 0 ? bounds.width - size.width - right : 24;
  return clampPosition({ x: initialX, y: top }, size, bounds, 16);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test src/utils/floatingGeometry.test.ts`
Expected: PASS (all 3 suites, 5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/utils/floatingGeometry.ts src/utils/floatingGeometry.test.ts
git commit -m "feat(kiosk): add floating geometry calculation utilities"
```

---

### Task 2: Draggable & Resizable Hook and ScreenShareView Component

**Files:**
- Create: `frontend/src/hooks/useDraggableResizable.ts`
- Modify: `frontend/src/components/Kiosk/ScreenShareView.tsx`

**Interfaces:**
- Consumes: `clampPosition`, `computeResizeDimensions`, `getInitialPosition` from `frontend/src/utils/floatingGeometry.ts`.
- Produces:
  ```typescript
  export interface UseDraggableResizableOptions {
    initialWidth?: number;
    aspectRatio?: number;
    minWidth?: number;
    maxWidth?: number;
    offset?: { top?: number; right?: number };
  }
  
  export function useDraggableResizable(options?: UseDraggableResizableOptions): {
    position: { x: number; y: number };
    size: { width: number; height: number };
    isDragging: boolean;
    isResizing: boolean;
    onCardPointerDown: (e: React.PointerEvent) => void;
    onResizePointerDown: (e: React.PointerEvent) => void;
  };
  
  export function ScreenShareView({
    stream,
    floating,
  }: {
    stream: MediaStream | null;
    floating?: boolean;
  }): React.JSX.Element;
  ```

- [ ] **Step 1: Create `frontend/src/hooks/useDraggableResizable.ts`**

```typescript
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  clampPosition,
  computeResizeDimensions,
  getInitialPosition,
  type Dimensions,
  type Point,
} from '@/utils/floatingGeometry';

export interface UseDraggableResizableOptions {
  initialWidth?: number;
  aspectRatio?: number;
  minWidth?: number;
  maxWidth?: number;
  offset?: { top?: number; right?: number };
}

export function useDraggableResizable(options: UseDraggableResizableOptions = {}) {
  const {
    initialWidth = 380,
    aspectRatio = 16 / 9,
    minWidth = 240,
    maxWidth = typeof window !== 'undefined' ? Math.min(window.innerWidth * 0.85, 1000) : 800,
    offset = { top: 72, right: 24 },
  } = options;

  const initialHeight = Math.round(initialWidth / aspectRatio);
  const [size, setSize] = useState<Dimensions>({ width: initialWidth, height: initialHeight });
  const [position, setPosition] = useState<Point>(() => {
    if (typeof window === 'undefined') return { x: 24, y: 72 };
    return getInitialPosition(
      { width: initialWidth, height: initialHeight },
      { width: window.innerWidth, height: window.innerHeight },
      offset,
    );
  });

  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);

  const dragStateRef = useRef<{
    startX: number;
    startY: number;
    startPosX: number;
    startPosY: number;
  } | null>(null);

  const resizeStateRef = useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);

  // Re-clamp on window resize
  useEffect(() => {
    const handleWindowResize = () => {
      setPosition((prev) =>
        clampPosition(prev, size, { width: window.innerWidth, height: window.innerHeight }),
      );
    };
    window.addEventListener('resize', handleWindowResize);
    return () => window.removeEventListener('resize', handleWindowResize);
  }, [size]);

  const onCardPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return; // Only primary button
      const target = e.currentTarget as HTMLElement;
      target.setPointerCapture(e.pointerId);

      dragStateRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        startPosX: position.x,
        startPosY: position.y,
      };
      setIsDragging(true);
    },
    [position],
  );

  const onCardPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragStateRef.current) return;
      const dx = e.clientX - dragStateRef.current.startX;
      const dy = e.clientY - dragStateRef.current.startY;
      const newPos = {
        x: dragStateRef.current.startPosX + dx,
        y: dragStateRef.current.startPosY + dy,
      };
      const bounds = { width: window.innerWidth, height: window.innerHeight };
      setPosition(clampPosition(newPos, size, bounds));
    },
    [size],
  );

  const onCardPointerUp = useCallback((e: React.PointerEvent) => {
    if (!dragStateRef.current) return;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {}
    dragStateRef.current = null;
    setIsDragging(false);
  }, []);

  const onResizePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      const target = e.currentTarget as HTMLElement;
      target.setPointerCapture(e.pointerId);

      resizeStateRef.current = {
        startX: e.clientX,
        startWidth: size.width,
      };
      setIsResizing(true);
    },
    [size.width],
  );

  const onResizePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!resizeStateRef.current) return;
      e.stopPropagation();
      const dx = e.clientX - resizeStateRef.current.startX;
      const newSize = computeResizeDimensions(
        resizeStateRef.current.startWidth,
        dx,
        aspectRatio,
        minWidth,
        maxWidth,
      );
      setSize(newSize);
      setPosition((prev) =>
        clampPosition(prev, newSize, { width: window.innerWidth, height: window.innerHeight }),
      );
    },
    [aspectRatio, maxWidth, minWidth],
  );

  const onResizePointerUp = useCallback((e: React.PointerEvent) => {
    if (!resizeStateRef.current) return;
    e.stopPropagation();
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {}
    resizeStateRef.current = null;
    setIsResizing(false);
  }, []);

  return {
    position,
    size,
    isDragging,
    isResizing,
    cardHandlers: {
      onPointerDown: onCardPointerDown,
      onPointerMove: onCardPointerMove,
      onPointerUp: onCardPointerUp,
      onPointerCancel: onCardPointerUp,
    },
    resizeHandlers: {
      onPointerDown: onResizePointerDown,
      onPointerMove: onResizePointerMove,
      onPointerUp: onResizePointerUp,
      onPointerCancel: onResizePointerUp,
    },
  };
}
```

- [ ] **Step 2: Update `frontend/src/components/Kiosk/ScreenShareView.tsx`**

```tsx
import { useEffect, useRef } from 'react';
import { Maximize2 } from 'lucide-react';
import { useDraggableResizable } from '@/hooks/useDraggableResizable';

export function ScreenShareView({
  stream,
  floating = false,
}: {
  stream: MediaStream | null;
  floating?: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { position, size, isDragging, cardHandlers, resizeHandlers } =
    useDraggableResizable({ initialWidth: 380, aspectRatio: 16 / 9 });

  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = stream;
  }, [stream]);

  if (!floating) {
    return (
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="absolute inset-0 h-full w-full"
        style={{ objectFit: 'contain', background: '#06060f' }}
      />
    );
  }

  return (
    <div
      {...cardHandlers}
      className={`fixed z-25 select-none rounded-2xl overflow-hidden touch-none transition-shadow ${
        isDragging ? 'cursor-grabbing shadow-[0_20px_50px_rgba(0,0,0,0.8)]' : 'cursor-grab shadow-[0_12px_36px_rgba(0,0,0,0.6)]'
      }`}
      style={{
        left: `${position.x}px`,
        top: `${position.y}px`,
        width: `${size.width}px`,
        height: `${size.height}px`,
        background: '#06060f',
        border: '1px solid rgba(255, 255, 255, 0.16)',
      }}
    >
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="w-full h-full object-contain pointer-events-none"
      />

      {/* Subtle bottom-right corner resize handle (Google AI Studio style) */}
      <div
        {...resizeHandlers}
        title="Resize"
        className="absolute bottom-1.5 right-1.5 p-1 rounded-md bg-black/50 hover:bg-black/80 text-white/60 hover:text-white cursor-nwse-resize touch-none transition-colors"
      >
        <Maximize2 size={12} className="rotate-90" />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Run test suite**

Run: `npm test`
Expected: PASS (all tests).

- [ ] **Step 4: Commit**

```bash
git add src/hooks/useDraggableResizable.ts src/components/Kiosk/ScreenShareView.tsx
git commit -m "feat(kiosk): make screen share view floating, draggable and resizable"
```

---

### Task 3: KioskPage Integration & Verification

**Files:**
- Modify: `frontend/src/pages/KioskPage.tsx`

- [ ] **Step 1: Update `frontend/src/pages/KioskPage.tsx` to mount persistent background iframe and floating screen share**

In `frontend/src/pages/KioskPage.tsx`, replace lines 102-110:
```tsx
      <iframe
        src="/display.html"
        title="Display"
        className="absolute inset-0 h-full w-full border-0"
      />
      {share.status === 'live' && (
        <ScreenShareView stream={share.stream} floating />
      )}
```

- [ ] **Step 2: Run all frontend tests**

Run: `npm test`
Expected: PASS.

- [ ] **Step 3: Build frontend to verify types & bundling**

Run: `npm run build`
Expected: `tsc -b && vite build` succeeds with exit code 0.

- [ ] **Step 4: Commit**

```bash
git add src/pages/KioskPage.tsx
git commit -m "feat(kiosk): render screen share as floating overlay on top of display iframe"
```
