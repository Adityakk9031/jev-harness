import React from 'react';
import {AbsoluteFill, Audio, Sequence, interpolate, random, staticFile, useCurrentFrame} from 'remotion';

/** One sound effect at a frame (relative to the enclosing sequence). */
export const Sfx: React.FC<{at: number; name: 'whoosh' | 'impact' | 'tick' | 'riser' | 'swell' | 'glitch' | 'boom' | 'pop'; volume?: number}> = ({
  at, name, volume = 0.5,
}) => (
  <Sequence from={Math.max(0, Math.round(at))} durationInFrames={150} layout="none">
    <Audio src={staticFile(`audio/${name}.wav`)} volume={volume} />
  </Sequence>
);

/** Shakes its children for a few frames after each hit. */
export const Shake: React.FC<{hits: number[]; strength?: number; children: React.ReactNode}> = ({hits, strength = 14, children}) => {
  const f = useCurrentFrame();
  let x = 0;
  let y = 0;
  let r = 0;
  for (const h of hits) {
    const t = f - h;
    if (t < 0 || t > 14) continue;
    const k = strength * Math.pow(1 - t / 14, 2);
    x += (random(`x${h}-${f}`) - 0.5) * 2 * k;
    y += (random(`y${h}-${f}`) - 0.5) * 2 * k;
    r += (random(`r${h}-${f}`) - 0.5) * 0.08 * k;
  }
  return <AbsoluteFill style={{transform: `translate(${x}px, ${y}px) rotate(${r}deg)`}}>{children}</AbsoluteFill>;
};

/** A quick white (or coloured) flash. */
export const Flash: React.FC<{at: number; color?: string; peak?: number; len?: number}> = ({at, color = '#ffffff', peak = 0.85, len = 14}) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [at, at + 2, at + len], [0, peak, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  if (o <= 0) return null;
  return <AbsoluteFill style={{background: color, opacity: o, zIndex: 50, mixBlendMode: 'screen'}} />;
};

/** Film grain and a soft vignette over everything. */
export const Grain: React.FC = () => {
  const f = useCurrentFrame();
  const seed = Math.floor(f / 2) % 97;
  return (
    <AbsoluteFill style={{pointerEvents: 'none', zIndex: 90}}>
      <svg width="100%" height="100%" style={{position: 'absolute', inset: 0, opacity: 0.07, mixBlendMode: 'overlay'}}>
        <filter id="grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves={2} seed={seed} stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter="url(#grain)" />
      </svg>
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.55) 100%)'}} />
    </AbsoluteFill>
  );
};
