import React, { useEffect, useRef, useState } from 'react';

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
 * Generates the SVG path for the speech bubble border with an integrated tail,
 * perfectly matching the glowing curved border in the reference image sample.
 */
export function getBubblePath(
  w: number,
  h: number,
  r: number = 18,
  tailPos: 'right' | 'left' = 'right'
): string {
  const radius = Math.min(r, Math.floor(w / 4), Math.floor(h / 2));

  if (tailPos === 'left') {
    return [
      `M ${radius} 0`,
      `H ${w - radius}`,
      `A ${radius} ${radius} 0 0 1 ${w} ${radius}`,
      `V ${h - radius}`,
      `A ${radius} ${radius} 0 0 1 ${w - radius} ${h}`,
      `H 14`,
      `C 6 ${h + 1}, -1 ${h + 5}, -7 ${h + 8}`,
      `C -1 ${h + 3}, 2 ${h - 6}, 0 ${h - 14}`,
      `V ${radius}`,
      `A ${radius} ${radius} 0 0 1 ${radius} 0`,
      `Z`,
    ].join(' ');
  }

  // Tail at bottom-right pointing towards robot (matching image sample)
  return [
    `M ${radius} 0`,
    `H ${w - radius}`,
    `A ${radius} ${radius} 0 0 1 ${w} ${radius}`,
    `V ${h - 14}`,
    `C ${w - 2} ${h - 6}, ${w + 1} ${h + 3}, ${w + 7} ${h + 8}`,
    `C ${w + 1} ${h + 5}, ${w - 6} ${h + 1}, ${w - 14} ${h}`,
    `H ${radius}`,
    `A ${radius} ${radius} 0 0 1 0 ${h - radius}`,
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
  maxChars = 140,
  className = '',
  style,
  role = 'assistant',
  onClick,
}: CodexPetSpeechBubbleProps) {
  const formattedText = formatSpeechText(text, maxChars);
  const speechContent = parseSpeechContent(formattedText);
  const isSpeaking = voiceStatus === 'speaking';
  const showTyping = typing || (Boolean(voiceStatus && voiceStatus !== 'idle') && !formattedText);

  const isVisible =
    visible !== undefined
      ? visible
      : Boolean(formattedText || showTyping);

  const [dimensions, setDimensions] = useState<{ width: number; height: number }>({
    width: 240,
    height: 68,
  });
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const node = containerRef.current;
    const updateSize = () => {
      const rect = node.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      if (w > 0 && h > 0) {
        setDimensions((prev) =>
          prev.width === w && prev.height === h ? prev : { width: w, height: h }
        );
      }
    };

    updateSize();

    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(updateSize);
      ro.observe(node);
      return () => ro.disconnect();
    }
  }, [formattedText, showTyping]);

  if (!isVisible) {
    return null;
  }

  // Positioning classes: default 'left' (Bubble on left, Robot on right matching sample photo)
  let posClasses = 'right-full -mr-7 top-1';
  if (position === 'top') {
    posClasses = 'bottom-full mb-2 left-1/2 -translate-x-1/2';
  } else if (position === 'top-left') {
    posClasses = 'bottom-full mb-2 left-0';
  } else if (position === 'top-right') {
    posClasses = 'bottom-full mb-2 right-0';
  }

  const effectiveTailPosition: 'right' | 'left' =
    position === 'top-right' ? 'left' : 'right';

  const pathData = getBubblePath(
    dimensions.width,
    dimensions.height,
    18,
    effectiveTailPosition
  );

  const isVerticalPosition = position === 'top' || position === 'top-left' || position === 'top-right';
  const defaultHorizontalOffset = -28;
  const effectiveMarginRight = offsetY !== 14 ? offsetY : defaultHorizontalOffset;

  return (
    <div
      data-testid="codex-pet-speech-bubble"
      data-role={role}
      data-position={position}
      role="status"
      aria-live="polite"
      onClick={onClick}
      className={`absolute pointer-events-auto select-none z-30 transition-all duration-200 ease-out animate-in fade-in zoom-in-95 ${posClasses} ${className}`}
      style={{
        ...(isVerticalPosition
          ? { marginBottom: `${offsetY}px` }
          : { marginRight: `${effectiveMarginRight}px` }),
        ...style,
      }}
    >
      {/* Speech bubble card wrapper */}
      <div
        ref={containerRef}
        className="relative w-max min-w-[170px] max-w-[260px] sm:max-w-[290px] px-5 py-3 text-xs font-medium leading-relaxed whitespace-normal break-words"
        style={{
          background: 'transparent',
          color: '#ffffff',
        }}
      >
        {/* Seamless glowing cyan neon SVG border with integrated tail matching sample image */}
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
            d={pathData}
            fill="none"
            stroke="#22d3ee"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              filter:
                'drop-shadow(0 0 3px #00f0ff) drop-shadow(0 0 8px rgba(6, 182, 212, 0.75))',
            }}
          />
        </svg>

        {showTyping ? (
          <div
            data-testid="speech-bubble-typing"
            className="flex items-center gap-1.5 py-1 px-2 justify-center"
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
          <div data-testid="speech-bubble-text" className="break-words whitespace-pre-wrap flex flex-col">
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
