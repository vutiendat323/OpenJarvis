import { useEffect, useRef } from 'react';

export function ScreenShareView({ stream }: { stream: MediaStream | null }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  // srcObject is a property, never an attribute — it cannot be set in JSX.
  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = stream;
  }, [stream]);

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
