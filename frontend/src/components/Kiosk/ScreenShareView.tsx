import { useEffect, useRef } from 'react';
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

  // srcObject is a property, never an attribute — it cannot be set in JSX.
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
        isDragging
          ? 'cursor-grabbing shadow-[0_20px_50px_rgba(0,0,0,0.8)]'
          : 'cursor-grab shadow-[0_12px_36px_rgba(0,0,0,0.6)]'
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

      {/* Invisible bottom-right corner resize handle */}
      <div
        {...resizeHandlers}
        className="absolute bottom-0 right-0 w-7 h-7 cursor-nwse-resize touch-none z-10"
      />
    </div>
  );
}
