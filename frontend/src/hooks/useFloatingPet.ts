import { useCallback, useEffect, useRef, useState } from 'react';
import type { PetPosition, PetSize } from '../components/Kiosk/Pet/types';

export const PET_POSITION_STORAGE_KEY = 'openjarvis_kiosk_pet_pos';
export const DEFAULT_PET_SIZE: PetSize = { width: 64, height: 64 };
export const DEFAULT_SAFE_MARGIN = 16;
export const DEFAULT_OFFSET_RIGHT_VW = 0.06; // 6vw
export const DEFAULT_OFFSET_BOTTOM_VH = 0.12; // 12vh
export const DEFAULT_WANDER_INTERVAL_MS = 10000; // 10s
export const DEFAULT_WANDER_MIN_STEP = 10;
export const DEFAULT_WANDER_MAX_STEP = 25;

export interface PetOffsetConfig {
  rightVw?: number;
  bottomVh?: number;
  rightPx?: number;
  bottomPx?: number;
}

export interface UseFloatingPetOptions {
  initialPosition?: PetPosition;
  petSize?: PetSize;
  safeMargin?: number;
  storageKey?: string;
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  enableWandering?: boolean;
  wanderIntervalMs?: number;
  bounds?: PetSize;
  offset?: PetOffsetConfig;
}

export interface FloatingPetHandlers {
  onPointerDown: (e: React.PointerEvent<HTMLElement>) => void;
  onPointerMove: (e: React.PointerEvent<HTMLElement>) => void;
  onPointerUp: (e: React.PointerEvent<HTMLElement>) => void;
  onPointerCancel: (e: React.PointerEvent<HTMLElement>) => void;
  onDoubleClick: (e: React.MouseEvent<HTMLElement>) => void;
}

export interface UseFloatingPetResult {
  position: PetPosition;
  isDragging: boolean;
  dragDeltaX: number;
  petHandlers: FloatingPetHandlers;
  resetPosition: () => void;
  setPosition: React.Dispatch<React.SetStateAction<PetPosition>>;
}

/**
 * Clamps a 2D pet position within viewport safe margins.
 */
export function clampPetPosition(
  pos: PetPosition,
  petSize: PetSize = DEFAULT_PET_SIZE,
  viewport: PetSize = { width: 1920, height: 1080 },
  margin = DEFAULT_SAFE_MARGIN,
): PetPosition {
  const effectiveMargin = Math.max(0, margin);
  const minX = effectiveMargin;
  const maxX = Math.max(effectiveMargin, viewport.width - petSize.width - effectiveMargin);
  const clampedX = Math.min(Math.max(pos.x, minX), maxX);

  const minY = effectiveMargin;
  const maxY = Math.max(effectiveMargin, viewport.height - petSize.height - effectiveMargin);
  const clampedY = Math.min(Math.max(pos.y, minY), maxY);

  return {
    x: Math.round(clampedX),
    y: Math.round(clampedY),
  };
}

/**
 * Calculates the default bottom-right position (6vw right, 12vh bottom by default),
 * clamped within viewport bounds.
 */
export function calculateDefaultPetPosition(
  viewport: PetSize,
  petSize: PetSize = DEFAULT_PET_SIZE,
  offset?: PetOffsetConfig,
  margin = DEFAULT_SAFE_MARGIN,
): PetPosition {
  const rightOffset =
    offset?.rightPx !== undefined
      ? offset.rightPx
      : viewport.width * (offset?.rightVw ?? DEFAULT_OFFSET_RIGHT_VW);
  const bottomOffset =
    offset?.bottomPx !== undefined
      ? offset.bottomPx
      : viewport.height * (offset?.bottomVh ?? DEFAULT_OFFSET_BOTTOM_VH);

  const rawX = viewport.width - petSize.width - rightOffset;
  const rawY = viewport.height - petSize.height - bottomOffset;

  return clampPetPosition({ x: rawX, y: rawY }, petSize, viewport, margin);
}

/**
 * Reads stored pet position from localStorage or provided storage.
 */
export function readStoredPetPosition(
  storage?: Pick<Storage, 'getItem'>,
  storageKey = PET_POSITION_STORAGE_KEY,
): PetPosition | null {
  try {
    const store =
      storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined);
    if (!store) return null;

    const raw = store.getItem(storageKey);
    if (!raw) return null;

    const parsed = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === 'object' &&
      typeof parsed.x === 'number' &&
      Number.isFinite(parsed.x) &&
      typeof parsed.y === 'number' &&
      Number.isFinite(parsed.y)
    ) {
      return { x: parsed.x, y: parsed.y };
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Persists pet position to storage.
 */
export function persistPetPosition(
  position: PetPosition,
  storage?: Pick<Storage, 'setItem'>,
  storageKey = PET_POSITION_STORAGE_KEY,
): void {
  try {
    const store =
      storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined);
    if (!store) return;

    const raw = JSON.stringify({
      x: Math.round(position.x),
      y: Math.round(position.y),
    });
    store.setItem(storageKey, raw);
  } catch {
    // Gracefully ignore storage write failures in restricted environments
  }
}

/**
 * Clears stored pet position from storage.
 */
export function clearStoredPetPosition(
  storage?: Pick<Storage, 'removeItem'>,
  storageKey = PET_POSITION_STORAGE_KEY,
): void {
  try {
    const store =
      storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined);
    if (!store) return;

    store.removeItem(storageKey);
  } catch {
    // Gracefully ignore storage errors
  }
}

/**
 * Computes an autonomous subtle wandering step (10-25px horizontal) clamped to safe boundaries.
 */
export function calculateWanderStep(
  currentPos: PetPosition,
  petSize: PetSize = DEFAULT_PET_SIZE,
  viewport: PetSize = { width: 1920, height: 1080 },
  stepRange = { min: DEFAULT_WANDER_MIN_STEP, max: DEFAULT_WANDER_MAX_STEP },
  margin = DEFAULT_SAFE_MARGIN,
  randomFn: () => number = Math.random,
): PetPosition {
  const dirX = randomFn() < 0.5 ? -1 : 1;
  const dist = stepRange.min + randomFn() * (stepRange.max - stepRange.min);
  const deltaX = Math.round(dirX * dist);
  const deltaY = Math.round((randomFn() - 0.5) * 6);

  const nextPos: PetPosition = {
    x: currentPos.x + deltaX,
    y: currentPos.y + deltaY,
  };

  return clampPetPosition(nextPos, petSize, viewport, margin);
}

/**
 * React hook managing floating pet physics, pointer-capture dragging, bounds clamping,
 * localStorage persistence, double-click reset, and idle wandering.
 */
export function useFloatingPet(options: UseFloatingPetOptions = {}): UseFloatingPetResult {
  const {
    initialPosition,
    petSize = DEFAULT_PET_SIZE,
    safeMargin = DEFAULT_SAFE_MARGIN,
    storageKey = PET_POSITION_STORAGE_KEY,
    storage = typeof window !== 'undefined' ? window.localStorage : undefined,
    enableWandering = true,
    wanderIntervalMs = DEFAULT_WANDER_INTERVAL_MS,
    bounds: explicitBounds,
    offset,
  } = options;

  const getViewport = useCallback((): PetSize => {
    if (explicitBounds) return explicitBounds;
    if (typeof window !== 'undefined' && window.innerWidth > 0 && window.innerHeight > 0) {
      return { width: window.innerWidth, height: window.innerHeight };
    }
    return { width: 1920, height: 1080 };
  }, [explicitBounds]);

  const [position, setPosition] = useState<PetPosition>(() => {
    const viewport = getViewport();
    if (initialPosition) {
      return clampPetPosition(initialPosition, petSize, viewport, safeMargin);
    }
    const stored = readStoredPetPosition(storage, storageKey);
    if (stored) {
      return clampPetPosition(stored, petSize, viewport, safeMargin);
    }
    return calculateDefaultPetPosition(viewport, petSize, offset, safeMargin);
  });

  const [isDragging, setIsDragging] = useState(false);
  const [dragDeltaX, setDragDeltaX] = useState(0);

  const dragStateRef = useRef<{
    startX: number;
    startY: number;
    startPosX: number;
    startPosY: number;
    lastX: number;
  } | null>(null);

  const positionRef = useRef(position);
  positionRef.current = position;

  // Window resize re-clamping
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleResize = () => {
      const vp = getViewport();
      setPosition((prev) => clampPetPosition(prev, petSize, vp, safeMargin));
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [getViewport, petSize, safeMargin]);

  // Autonomous wandering timer when idle
  useEffect(() => {
    if (!enableWandering || isDragging) return;

    const timer = setInterval(() => {
      const vp = getViewport();
      setPosition((prev) => calculateWanderStep(prev, petSize, vp, undefined, safeMargin));
    }, wanderIntervalMs);

    return () => clearInterval(timer);
  }, [enableWandering, isDragging, getViewport, petSize, safeMargin, wanderIntervalMs]);

  const resetPosition = useCallback(() => {
    clearStoredPetPosition(storage, storageKey);
    const vp = getViewport();
    const defaultPos = calculateDefaultPetPosition(vp, petSize, offset, safeMargin);
    setPosition(defaultPos);
  }, [getViewport, offset, petSize, safeMargin, storage, storageKey]);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return;
    const target = e.currentTarget;
    try {
      target.setPointerCapture?.(e.pointerId);
    } catch {}

    dragStateRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      startPosX: positionRef.current.x,
      startPosY: positionRef.current.y,
      lastX: e.clientX,
    };
    setIsDragging(true);
    setDragDeltaX(0);
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (!dragStateRef.current) return;
      const dx = e.clientX - dragStateRef.current.startX;
      const dy = e.clientY - dragStateRef.current.startY;
      const stepDeltaX = e.clientX - dragStateRef.current.lastX;
      dragStateRef.current.lastX = e.clientX;

      if (stepDeltaX !== 0) {
        setDragDeltaX(stepDeltaX);
      }

      const nextPos: PetPosition = {
        x: dragStateRef.current.startPosX + dx,
        y: dragStateRef.current.startPosY + dy,
      };

      const vp = getViewport();
      const clamped = clampPetPosition(nextPos, petSize, vp, safeMargin);
      positionRef.current = clamped;
      setPosition(clamped);
    },
    [getViewport, petSize, safeMargin],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (!dragStateRef.current) return;
      try {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      } catch {}
      dragStateRef.current = null;
      setIsDragging(false);
      setDragDeltaX(0);
      persistPetPosition(positionRef.current, storage, storageKey);
    },
    [storage, storageKey],
  );

  const onPointerCancel = useCallback((e: React.PointerEvent<HTMLElement>) => {
    if (!dragStateRef.current) return;
    try {
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    } catch {}
    dragStateRef.current = null;
    setIsDragging(false);
    setDragDeltaX(0);
  }, []);

  const onDoubleClick = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      e.stopPropagation();
      resetPosition();
    },
    [resetPosition],
  );

  return {
    position,
    isDragging,
    dragDeltaX,
    petHandlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
      onDoubleClick,
    },
    resetPosition,
    setPosition,
  };
}
