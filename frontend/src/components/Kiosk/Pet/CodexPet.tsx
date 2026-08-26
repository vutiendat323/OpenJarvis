import React, { useEffect, useRef, useState } from 'react';
import { getFrameCoordinates } from './codexPetAtlas';
import { CodexPetSpeechBubble } from './CodexPetSpeechBubble';
import type {
  PetAnimationConfig,
  PetManifest,
  PetPosition,
  PetState,
} from './types';

export const DEFAULT_MINTY_MANIFEST: PetManifest = {
  name: 'minty',
  size: { width: 32, height: 32 },
  scale: 2,
  defaultFps: 8,
  spriteUrl: '/pets/minty/sprite.png',
  columns: 4,
  rows: 7,
  animations: {
    idle: { sequence: [0, 1, 2, 3], frameRate: 4, loop: true },
    'running-left': { sequence: [4, 5, 6, 7], frameRate: 8, loop: true },
    'running-right': { sequence: [8, 9, 10, 11], frameRate: 8, loop: true },
    waving: { sequence: [12, 13, 14, 15], frameRate: 6, loop: true },
    alert: { sequence: [16, 17, 18, 19], frameRate: 6, loop: true },
    failed: { sequence: [20, 21, 22, 23], frameRate: 4, loop: false, holdLastFrame: true },
    review: { sequence: [24, 25, 26, 27], frameRate: 6, loop: true },
  },
};

export interface CodexPetProps {
  /** Sprite manifest (defaults to minty) */
  manifest?: PetManifest;
  /** Current animation state */
  state?: PetState | string;
  /** Optional frame rate override */
  fps?: number;
  /** Scale factor (defaults to manifest.scale ?? 1) */
  scale?: number;
  /** Render mode: 'css' (sprite step background) or 'canvas' */
  renderMode?: 'css' | 'canvas';
  /** Controlled frame index (disables autonomous timer) */
  frameIndex?: number;
  /** Callback fired on each frame advance */
  onFrameChange?: (frameIndex: number) => void;
  /** Callback fired when non-looping animation finishes */
  onAnimationComplete?: () => void;
  /** Absolute coordinates if positioned externally */
  position?: PetPosition;
  /** Whether user is actively dragging the pet */
  isDragging?: boolean;
  /** Custom speech bubble React element */
  speechBubble?: React.ReactNode;
  /** Speech text for standard speech bubble */
  speechText?: string;
  /** Voice status for standard speech bubble */
  voiceStatus?: string;
  /** Whether to render drop shadow */
  showShadow?: boolean;
  /** Additional CSS class names */
  className?: string;
  /** Additional inline styles */
  style?: React.CSSProperties;
  /** Mouse and pointer event handlers */
  onClick?: React.MouseEventHandler<HTMLDivElement>;
  onDoubleClick?: React.MouseEventHandler<HTMLDivElement>;
  onPointerDown?: React.PointerEventHandler<HTMLDivElement>;
  onPointerMove?: React.PointerEventHandler<HTMLDivElement>;
  onPointerUp?: React.PointerEventHandler<HTMLDivElement>;
  onPointerCancel?: React.PointerEventHandler<HTMLDivElement>;
}

/**
 * Calculates next frame step and completion status for a given animation configuration.
 */
export function calculatePetFrameStep(
  currentFrame: number,
  animConfig: PetAnimationConfig,
): { nextFrame: number; isFinished: boolean } {
  const sequence = animConfig.sequence.length > 0 ? animConfig.sequence : [0];
  const maxIdx = sequence.length - 1;

  if (sequence.length <= 1) {
    return { nextFrame: 0, isFinished: false };
  }

  if (animConfig.loop === false) {
    if (currentFrame >= maxIdx) {
      return {
        nextFrame: maxIdx,
        isFinished: true,
      };
    }
    const next = currentFrame + 1;
    return {
      nextFrame: next,
      isFinished: false,
    };
  }

  const next = (currentFrame + 1) % sequence.length;
  return { nextFrame: next, isFinished: false };
}

/**
 * CodexPet sprite animation renderer.
 * Supports pixel-perfect CSS background step slicing and HTML5 Canvas drawing.
 */
export function CodexPet({
  manifest = DEFAULT_MINTY_MANIFEST,
  state = 'idle',
  fps,
  scale: scaleProp,
  renderMode = 'css',
  frameIndex: controlledFrameIndex,
  onFrameChange,
  onAnimationComplete,
  position,
  isDragging = false,
  speechBubble,
  speechText,
  voiceStatus,
  showShadow = true,
  className = '',
  style,
  onClick,
  onDoubleClick,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
}: CodexPetProps) {
  const scale = scaleProp ?? manifest.scale ?? 1;
  const width = manifest.size.width * scale;
  const height = manifest.size.height * scale;

  const [internalFrameIndex, setInternalFrameIndex] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const loadedSrcRef = useRef<string | null>(null);
  const hasCompletedRef = useRef(false);

  // Active animation definition
  const animConfig: PetAnimationConfig =
    manifest.animations[state] ||
    (state === 'running-left' && manifest.animations['running-right']) ||
    (state === 'running-right' && manifest.animations['running-left']) ||
    manifest.animations.idle || { sequence: [0], loop: true };

  const effectiveFps = fps ?? animConfig.frameRate ?? manifest.defaultFps ?? 8;
  const activeFrameIndex = controlledFrameIndex !== undefined ? controlledFrameIndex : internalFrameIndex;

  // Reset frame and completion tracking on state or animConfig change
  useEffect(() => {
    setInternalFrameIndex(0);
    hasCompletedRef.current = false;
  }, [state, animConfig]);

  // Frame cycling timer
  useEffect(() => {
    if (controlledFrameIndex !== undefined) return;
    if (effectiveFps <= 0) return;

    const intervalMs = Math.max(16, Math.round(1000 / effectiveFps));
    const timer = setInterval(() => {
      setInternalFrameIndex((prev) => {
        const { nextFrame, isFinished } = calculatePetFrameStep(prev, animConfig);
        if (isFinished && !hasCompletedRef.current) {
          hasCompletedRef.current = true;
          // Schedule callback outside of state updater
          queueMicrotask(() => {
            onAnimationComplete?.();
          });
        }
        if (nextFrame !== prev) {
          queueMicrotask(() => {
            onFrameChange?.(nextFrame);
          });
        }
        return nextFrame;
      });
    }, intervalMs);

    return () => clearInterval(timer);
  }, [animConfig, controlledFrameIndex, effectiveFps, onAnimationComplete, onFrameChange]);

  const coords = getFrameCoordinates(manifest, state, activeFrameIndex);
  const spriteUrl = manifest.spriteUrl ?? '/pets/minty/sprite.png';

  // Canvas renderer effect
  useEffect(() => {
    if (renderMode !== 'canvas' || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    if (!imageRef.current || loadedSrcRef.current !== spriteUrl) {
      const img = new Image();
      img.src = spriteUrl;
      imageRef.current = img;
      loadedSrcRef.current = spriteUrl;
      img.onload = () => {
        renderCanvasFrame(ctx, img, coords, width, height);
      };
    } else if (imageRef.current.complete) {
      renderCanvasFrame(ctx, imageRef.current, coords, width, height);
    }
  }, [coords, height, renderMode, spriteUrl, width]);

  const positionStyle: React.CSSProperties = position
    ? {
        position: 'absolute',
        left: 0,
        top: 0,
        transform: `translate3d(${position.x}px, ${position.y}px, 0)`,
      }
    : {};

  return (
    <div
      data-testid="codex-pet"
      data-state={state}
      data-dragging={isDragging ? 'true' : 'false'}
      role="img"
      aria-label={`Codex Pet (${manifest.name}) - ${state}`}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      className={`relative inline-flex flex-col items-center justify-center select-none touch-none ${className}`}
      style={{
        width: `${width}px`,
        height: `${height}px`,
        cursor: isDragging ? 'grabbing' : 'grab',
        ...positionStyle,
        ...style,
      }}
    >
      {/* Speech bubble */}
      {speechBubble ? (
        speechBubble
      ) : speechText || voiceStatus === 'speaking' ? (
        <CodexPetSpeechBubble
          text={speechText}
          voiceStatus={voiceStatus}
        />
      ) : null}

      {/* Pet sprite */}
      {renderMode === 'canvas' ? (
        <canvas
          ref={canvasRef}
          data-testid="codex-pet-canvas"
          width={width}
          height={height}
          style={{
            width: `${width}px`,
            height: `${height}px`,
            imageRendering: 'pixelated',
          }}
        />
      ) : (
        <div
          data-testid="codex-pet-sprite"
          className="relative overflow-hidden pointer-events-none"
          style={{
            width: `${width}px`,
            height: `${height}px`,
          }}
        >
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: `${manifest.size.width}px`,
              height: `${manifest.size.height}px`,
              backgroundImage: `url(${spriteUrl})`,
              backgroundPosition: `-${coords.x}px -${coords.y}px`,
              backgroundRepeat: 'no-repeat',
              imageRendering: 'pixelated',
              transform: `scale(${scale})`,
              transformOrigin: 'top left',
            }}
          />
        </div>
      )}

      {/* Pixel ground shadow */}
      {showShadow && (
        <div
          data-testid="codex-pet-shadow"
          className="absolute -bottom-1 pointer-events-none rounded-full"
          style={{
            width: `${Math.round(width * 0.65)}px`,
            height: `${Math.max(4, Math.round(6 * (scale / 2)))}px`,
            background: 'radial-gradient(ellipse at center, rgba(0, 0, 0, 0.4) 0%, rgba(0, 0, 0, 0) 75%)',
          }}
        />
      )}
    </div>
  );
}

function renderCanvasFrame(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  coords: { x: number; y: number; width: number; height: number },
  destW: number,
  destH: number,
) {
  ctx.clearRect(0, 0, destW, destH);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(
    img,
    coords.x,
    coords.y,
    coords.width,
    coords.height,
    0,
    0,
    destW,
    destH,
  );
}
