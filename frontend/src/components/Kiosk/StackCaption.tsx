export interface StackCaptionLines {
  top: string;
  mid: string;
  bottom: string;
}

/**
 * Computes 3-line sliding window stack caption with 1 word per line:
 * - Line 1 (top): word 2 steps ago (words[n - 3]), small & faded
 * - Line 2 (mid): previous word (words[n - 2]), medium opacity
 * - Line 3 (bottom): newest active spoken word (words[n - 1]), medium-bold white
 */
export function getStackCaptionLines(text: string): StackCaptionLines {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const n = words.length;
  if (n === 0) return { top: '', mid: '', bottom: '' };
  if (n === 1) return { top: '', mid: '', bottom: words[0] };
  if (n === 2) return { top: '', mid: words[0], bottom: words[1] };
  return {
    top: words[n - 3],
    mid: words[n - 2],
    bottom: words[n - 1],
  };
}

export interface StackCaptionProps {
  text: string;
  isFading?: boolean;
  className?: string;
  role?: 'assistant' | 'user' | 'error';
}

export function StackCaption({
  text,
  isFading = false,
  className = '',
}: StackCaptionProps) {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const { top, mid, bottom } = getStackCaptionLines(trimmed);

  return (
    <div
      data-testid="stack-caption"
      className={`flex flex-col items-center justify-end text-center pointer-events-none transition-opacity duration-300 ease-out select-none ${
        isFading ? 'opacity-0' : 'opacity-100'
      } ${className}`}
    >
      {/* Screen-reader accessible full text */}
      <span className="sr-only">{trimmed}</span>

      {/* 3-line Stack Caption: 1 word per line, compact font sizes */}
      <div className="flex flex-col items-center leading-tight gap-0.5 sm:gap-1" aria-hidden="true">
        {top && (
          <span className="text-[11px] sm:text-xs font-normal text-white/35 tracking-normal transition-all duration-200">
            {top}
          </span>
        )}
        {mid && (
          <span className="text-xs sm:text-sm font-medium text-white/65 tracking-tight transition-all duration-200">
            {mid}
          </span>
        )}
        {bottom && (
          <span className="text-base sm:text-lg font-bold text-white tracking-tight drop-shadow-sm transition-all duration-200">
            {bottom}
          </span>
        )}
      </div>
    </div>
  );
}
