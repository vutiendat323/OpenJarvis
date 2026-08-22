# Kiosk Floating Screen Share Design

**Date:** 2026-08-22  
**Status:** Approved  
**Scope:** `frontend/src/components/Kiosk/ScreenShareView.tsx`, `frontend/src/pages/KioskPage.tsx`, `frontend/src/hooks/useDraggableResizable.ts` (and unit tests)

---

## 1. Context & Motivation

On the `/kiosk` page, when the operator activates **Share Screen**, the current `ScreenShareView` renders as a full-viewport static background (`absolute inset-0 h-full w-full`), replacing the primary `/display.html` iframe host.

### User Requirements
- **Picture-in-Picture Floating Window**: Render the shared screen as an overlay card instead of taking over the full background.
- **Draggable (Di chuyển)**: Users can click and drag anywhere on the frame to move it around the screen freely, with boundary clamping.
- **Resizable (Co giãn)**: A resize handle in the corner (bottom-right) with diagonal resize arrows (styled like Google AI Studio Playground) allows users to resize the window dynamically while preserving aspect ratio.
- **Minimalist Aesthetic**: No extra top header bars or overlaid close/stop buttons on the card. The existing Kiosk control buttons remain the control surface.
- **Persistent Background**: The underlying `/display.html` iframe and particle visualizer stay mounted and active underneath.

---

## 2. Component Architecture

### A. Pure Geometry & Clamp Logic (`frontend/src/utils/floatingGeometry.ts`)
Extract pure math functions for testability:
1. `clampPosition(pos: { x: number, y: number }, size: { width: number, height: number }, bounds: { width: number, height: number }): { x: number, y: number }`
   - Keeps the window within `[MARGIN, bounds.width - size.width - MARGIN]` and `[MARGIN, bounds.height - size.height - MARGIN]`.
2. `computeResizeDimensions(startSize: { width: number, height: number }, deltaX: number, aspectRatio: number, minWidth: number, maxWidth: number): { width: number, height: number }`
   - Calculates new width based on drag delta, clamps within `[minWidth, maxWidth]`, and calculates proportional height.

### B. Drag & Resize Hook (`frontend/src/hooks/useDraggableResizable.ts`)
- Manages local state:
  - `position`: `{ x: number, y: number }` (default: offset from top-right, e.g., `x: window.innerWidth - initialWidth - 24, y: 72`).
  - `size`: `{ width: number, height: number }` (default width: 380px, height: 214px for 16:9).
  - `isDragging`: boolean (to toggle cursor between `cursor-grab` and `cursor-grabbing`).
  - `isResizing`: boolean.
- Uses standard Pointer Events (`onPointerDown`, `setPointerCapture`, `onPointerMove`, `onPointerUp`) on the card and resize handle.
- Listens to window `resize` events to clamp position within updated viewport bounds.

### C. Updated `ScreenShareView` Component (`frontend/src/components/Kiosk/ScreenShareView.tsx`)
- Props:
  - `stream`: `MediaStream | null`
  - `floating?: boolean` (default: `true`)
- When `floating={true}`:
  - Renders a floating container with `position: fixed` or `position: absolute`, `left: position.x`, `top: position.y`, `width: size.width`, `height: size.height`.
  - Styling: `rounded-2xl overflow-hidden border border-white/15 bg-[#06060f] select-none shadow-[0_12px_36px_rgba(0,0,0,0.6),0_0_0_1px_rgba(255,255,255,0.08)]`.
  - Video element: `w-full h-full object-contain pointer-events-none`.
  - Bottom-right corner resize handle:
    - Positioned `absolute bottom-1.5 right-1.5 p-1 rounded-md bg-black/40 text-white/70 hover:text-white cursor-nwse-resize`.
    - Shows diagonal resize icon `<Maximize2 size={12} className="rotate-90" />` (matching AI Studio style).
    - `onPointerDown` stops propagation so it doesn't trigger card dragging.
- When `floating={false}`:
  - Renders full-size / inline for non-floating contexts (such as `ChatPage`).

### D. Kiosk Page Integration (`frontend/src/pages/KioskPage.tsx`)
- Keep `<iframe src="/display.html" ... />` always mounted.
- When `share.status === 'live'`, render `<ScreenShareView stream={share.stream} floating />` at `z-index: 25`.

---

## 3. Testing Strategy
1. **Unit Tests (`frontend/src/utils/floatingGeometry.test.ts`)**:
   - Verify `clampPosition` bounds clamping on all 4 viewport edges.
   - Verify `computeResizeDimensions` preserves aspect ratio and respects `minWidth`/`maxWidth`.
2. **Component & Regression Verification**:
   - Run existing frontend test suite (`npm run test` or `vitest run`).
   - Visual verification on `http://127.0.0.1:5173/kiosk` with active screen share.
