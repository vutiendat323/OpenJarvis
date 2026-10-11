import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { layoutSpeechBubble, type BubbleTailSide } from './speechBubbleGeometry';

export interface CodexPetSpeechBubbleProps {
  /** Text content to show in the speech bubble */
  text?: string;
  /** Force visibility override */
  visible?: boolean;
  /** Voice status from voice assistant (e.g. 'speaking', 'listening', 'idle') */
  voiceStatus?: string;
  /** Whether to show an animated typing/speaking indicator */
  typing?: boolean;
  /** Alignment relative to pet head */
  position?: 'left' | 'top' | 'top-left' | 'top-right';
  /** Custom vertical/horizontal offset in pixels */
  offsetY?: number;
  /** Maximum number of characters before truncating with ellipsis */
  maxChars?: number;
  /** Additional CSS class names */
  className?: string;
  /** Additional inline styles */
  style?: React.CSSProperties;
  /** Role label */
  role?: 'assistant' | 'user' | 'system';
  /** Click handler */
  onClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
}

/**
 * Formats and truncates speech text if maxChars is specified.
 */
export function formatSpeechText(text?: string, maxChars?: number): string {
  if (!text) return '';
  const trimmed = text.trim();
  if (!trimmed) return '';
  if (typeof maxChars === 'number' && maxChars > 3 && trimmed.length > maxChars) {
    return trimmed.slice(0, maxChars - 3) + '...';
  }
  return trimmed;
}

export interface SpeechContent {
  title?: string;
  body: string;
}

/**
 * Parses speech text into an optional title (greeting / headline) and body,
 * matching the visual hierarchy seen in modern conversational UI cards.
 */
export function parseSpeechContent(text?: string): SpeechContent {
  if (!text) return { body: '' };
  const trimmed = text.trim();
  if (!trimmed) return { body: '' };

  if (trimmed.includes('\n')) {
    const lines = trimmed.split('\n').map((l) => l.trim()).filter(Boolean);
    if (lines.length > 1) {
      return { title: lines[0], body: lines.slice(1).join(' ') };
    }
  }

  // Matches short greeting/intro sentence ending in !, ?, ., or , (e.g. "Hello!", "Xin chào bạn,")
  const match = trimmed.match(/^([^.,!?\n]{1,24}[.,!?])\s+(.+)$/);
  if (match) {
    return { title: match[1], body: match[2] };
  }

  return { body: trimmed };
}

/**
 * Generates one filled card outline with a short tail facing the pet.
 */
export function getBubblePath(
  w: number,
  h: number,
  r: number = 18,
  tailPos: BubbleTailSide = 'right',
  tailOffset?: number,
): string {
  const radius = Math.max(0, Math.min(r, Math.floor(w / 4), Math.floor(h / 2) - 8));
  const length = tailPos === 'top' || tailPos === 'bottom' ? w : h;
  const t = Math.max(radius + 8, Math.min(tailOffset ?? length / 2, length - radius - 8));
  return [
    `M ${radius} 0`,
    ...(tailPos === 'top' ? [`H ${t - 8}`, `Q ${t - 3} 0 ${t} -10`, `Q ${t + 3} 0 ${t + 8} 0`] : []),
    `H ${w - radius}`,
    `A ${radius} ${radius} 0 0 1 ${w} ${radius}`,
    ...(tailPos === 'right' ? [`V ${t - 8}`, `Q ${w} ${t - 3} ${w + 10} ${t}`, `Q ${w} ${t + 3} ${w} ${t + 8}`] : []),
    `V ${h - radius}`,
    `A ${radius} ${radius} 0 0 1 ${w - radius} ${h}`,
    ...(tailPos === 'bottom' ? [`H ${t + 8}`, `Q ${t + 3} ${h} ${t} ${h + 10}`, `Q ${t - 3} ${h} ${t - 8} ${h}`] : []),
    `H ${radius}`,
    `A ${radius} ${radius} 0 0 1 0 ${h - radius}`,
    ...(tailPos === 'left' ? [`V ${t + 8}`, `Q 0 ${t + 3} -10 ${t}`, `Q 0 ${t - 3} 0 ${t - 8}`] : []),
    `V ${radius}`,
    `A ${radius} ${radius} 0 0 1 ${radius} 0`,
    `Z`,
  ].join(' ');
}

/**
 * Floating speech bubble component rendered next to or above the Codex Pet.
 */
export function CodexPetSpeechBubble({
  text,
  visible,
  voiceStatus,
  typing = false,
  position = 'left',
  offsetY = 14,
  maxChars,
  className = '',
  style,
  role = 'assistant',
  onClick,
}: CodexPetSpeechBubbleProps) {
  const formattedText = formatSpeechText(text, maxChars);
  const speechContent = parseSpeechContent(formattedText);
  const isWorking = voiceStatus === 'processing' || voiceStatus === 'inference' || voiceStatus === 'tool';
  const showTyping = typing || isWorking || (voiceStatus === 'speaking' && !formattedText);

  const isVisible =
    visible !== undefined
      ? visible
      : Boolean(formattedText || showTyping);

  const [dimensions, setDimensions] = useState<{ width: number; height: number }>({
    width: 240,
    height: 68,
  });
  const containerRef = useRef<HTMLDivElement | null>(null);
  const outerRef = useRef<HTMLDivElement | null>(null);
  const [layout, setLayout] = useState<(ReturnType<typeof layoutSpeechBubble> & { left: number; top: number }) | null>(null);
  const updateGeometry = useCallback(() => {
    if (!containerRef.current) return;
    const node = containerRef.current;
    const rect = node.getBoundingClientRect();
    const w = Math.round(rect.width);
    const h = Math.round(rect.height);
    if (w > 0 && h > 0) {
      setDimensions((prev) =>
        prev.width === w && prev.height === h ? prev : { width: w, height: h }
      );
      const boundary = outerRef.current?.closest('[data-pet-boundary]')?.getBoundingClientRect()
        ?? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
      const pet = outerRef.current?.parentElement?.getBoundingClientRect();
      if (pet) {
        const next = layoutSpeechBubble(
          boundary,
          { x: pet.left - boundary.left, y: pet.top - boundary.top, width: pet.width, height: pet.height },
          { width: w, height: h },
          position,
          offsetY,
        );
        const left = next.x + boundary.left - pet.left;
        const top = next.y + boundary.top - pet.top;
        setLayout((prev) => prev?.left === left && prev.top === top && prev.position === next.position && prev.tailOffset === next.tailOffset
          ? prev : { ...next, left, top });
      }
    }
  }, [position, offsetY]);
  // Reposition before paint when the pet moves, including drag and split toggles.
  useLayoutEffect(() => { updateGeometry(); });
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const boundary = outerRef.current?.closest('[data-pet-boundary]');
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(updateGeometry);
      ro.observe(node);
      if (boundary) ro.observe(boundary);
      return () => ro.disconnect();
    }
    window.addEventListener('resize', updateGeometry);
    return () => window.removeEventListener('resize', updateGeometry);
  }, [isVisible, updateGeometry]);

  if (!isVisible) {
    return null;
  }

  // Positioning classes: default 'left' (Bubble on left, Robot on right matching sample photo)
  const resolvedPosition = layout?.position ?? position;
  let posClasses = 'right-full -mr-7 top-1';
  if (resolvedPosition === 'top') {
    posClasses = 'bottom-full mb-2 left-1/2 -translate-x-1/2';
  } else if (resolvedPosition === 'top-left') {
    posClasses = 'bottom-full mb-2 left-0';
  } else if (resolvedPosition === 'top-right') {
    posClasses = 'bottom-full mb-2 right-0';
  }

  const effectiveTailPosition = layout?.tailSide ?? (position === 'left' ? 'right' : 'bottom');

  const pathData = getBubblePath(
    dimensions.width,
    dimensions.height,
    18,
    effectiveTailPosition,
    layout?.tailOffset,
  );

  const isVerticalPosition = resolvedPosition !== 'left';
  const defaultHorizontalOffset = -28;
  const effectiveMarginRight = offsetY !== 14 ? offsetY : defaultHorizontalOffset;

  return (
    <div
      ref={outerRef}
      data-testid="codex-pet-speech-bubble"
      data-role={role}
      data-position={resolvedPosition}
      role="status"
      aria-live="polite"
      onClick={onClick}
      className={`absolute pointer-events-auto select-none z-30 transition-opacity duration-200 ease-out ${layout ? '' : posClasses} ${className}`}
      style={{
        ...(layout ? { left: layout.left, top: layout.top } : isVerticalPosition
          ? { marginBottom: `${offsetY}px` }
          : { marginRight: `${effectiveMarginRight}px` }),
        ...style,
      }}
    >
      {/* Speech bubble card wrapper */}
      <div
        ref={containerRef}
        className="relative w-max min-w-[170px] max-w-[260px] px-4 py-3 text-xs font-medium leading-relaxed whitespace-normal break-words"
        style={{
          width: 'min(260px, var(--pet-bubble-max-width, calc(100vw - 40px)))',
          minWidth: 0,
          maxWidth: 'var(--pet-bubble-max-width, calc(100vw - 40px))',
          color: '#ffffff',
        }}
      >
        {/* Opaque card and tail share one border so background particles cannot cover the text. */}
        <svg
          data-testid="speech-bubble-border-svg"
          aria-hidden="true"
          className="absolute inset-0 pointer-events-none overflow-visible"
          width="100%"
          height="100%"
          viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
          fill="none"
        >
          <path
            data-testid="speech-bubble-tail"
            data-tail-side={effectiveTailPosition}
            d={pathData}
            fill="#082329"
            fillOpacity="0.98"
            stroke="#67e8f9"
            strokeWidth="1"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              filter:
                'drop-shadow(0 4px 10px rgba(0, 0, 0, 0.3))',
            }}
          />
        </svg>

        {showTyping ? (
          <div
            data-testid="speech-bubble-typing"
            className="relative flex items-center gap-1.5 py-1 px-2 justify-center"
          >
            <span
              className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.8)] animate-bounce"
              style={{ animationDelay: '0ms' }}
            />
            <span
              className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.8)] animate-bounce"
              style={{ animationDelay: '150ms' }}
            />
            <span
              className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.8)] animate-bounce"
              style={{ animationDelay: '300ms' }}
            />
          </div>
        ) : (
          <div
            data-testid="speech-bubble-text"
            className="relative break-words whitespace-pre-wrap flex flex-col"
            style={{ maxHeight: 'min(200px, calc(var(--pet-bubble-max-height, 100vh) - 24px))', overflowY: 'auto' }}
          >
            {speechContent.title && (
              <span className="text-[15px] sm:text-[16px] font-bold text-white tracking-tight leading-snug">
                {speechContent.title}
              </span>
            )}
            <span
              className={`${
                speechContent.title
                  ? 'mt-1 text-[13px] sm:text-[14px] text-white/90 font-normal'
                  : 'text-[14px] sm:text-[15px] font-medium text-white'
              } leading-snug`}
            >
              {speechContent.body}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
