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
    maxWidth = typeof window !== 'undefined' && window.innerWidth > 0
      ? Math.min(window.innerWidth * 0.85, 1000)
      : 800,
    offset = { top: 72, right: 24 },
  } = options;

  const initialHeight = Math.round(initialWidth / (aspectRatio > 0 ? aspectRatio : 16 / 9));
  const [size, setSize] = useState<Dimensions>({ width: initialWidth, height: initialHeight });
  const [position, setPosition] = useState<Point>(() => {
    if (typeof window === 'undefined' || window.innerWidth <= 0 || window.innerHeight <= 0) {
      return { x: 24, y: 72 };
    }
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
    if (typeof window === 'undefined') return;
    const handleWindowResize = () => {
      const bounds =
        window.innerWidth > 0 && window.innerHeight > 0
          ? { width: window.innerWidth, height: window.innerHeight }
          : { width: 1920, height: 1080 };
      setPosition((prev) => clampPosition(prev, size, bounds));
    };
    window.addEventListener('resize', handleWindowResize);
    return () => window.removeEventListener('resize', handleWindowResize);
  }, [size]);

  const onCardPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return; // Only primary button
      const target = e.currentTarget as HTMLElement;
      try {
        target.setPointerCapture?.(e.pointerId);
      } catch {}

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
      const bounds =
        typeof window !== 'undefined' && window.innerWidth > 0 && window.innerHeight > 0
          ? { width: window.innerWidth, height: window.innerHeight }
          : { width: 1920, height: 1080 };
      setPosition(clampPosition(newPos, size, bounds));
    },
    [size],
  );

  const onCardPointerUp = useCallback((e: React.PointerEvent) => {
    if (!dragStateRef.current) return;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    } catch {}
    dragStateRef.current = null;
    setIsDragging(false);
  }, []);

  const onResizePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      const target = e.currentTarget as HTMLElement;
      try {
        target.setPointerCapture?.(e.pointerId);
      } catch {}

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
      const bounds =
        typeof window !== 'undefined' && window.innerWidth > 0 && window.innerHeight > 0
          ? { width: window.innerWidth, height: window.innerHeight }
          : { width: 1920, height: 1080 };
      setPosition((prev) => clampPosition(prev, newSize, bounds));
    },
    [aspectRatio, maxWidth, minWidth],
  );

  const onResizePointerUp = useCallback((e: React.PointerEvent) => {
    if (!resizeStateRef.current) return;
    e.stopPropagation();
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
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
