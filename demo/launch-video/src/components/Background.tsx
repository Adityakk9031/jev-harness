import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {C} from '../theme';

const Orb: React.FC<{x: number; y: number; r: number; color: string; speed: number; phase: number}> = ({x, y, r, color, speed, phase}) => {
  const f = useCurrentFrame();
  const dx = Math.sin(f * speed + phase) * 160;
  const dy = Math.cos(f * speed * 0.8 + phase) * 110;
  return (
    <div
      style={{
        position: 'absolute', left: x + dx - r, top: y + dy - r, width: r * 2, height: r * 2, borderRadius: '50%',
        background: `radial-gradient(circle, ${color}50 0%, ${color}00 70%)`, filter: 'blur(50px)',
      }}
    />
  );
};

/** Living backdrop: drifting colour orbs, a slow grid, and light streaks; `accent` follows the current tool. */
export const Background: React.FC<{accent?: string}> = ({accent = C.jev}) => {
  const f = useCurrentFrame();
  const shift = (f * 0.5) % 80;
  const sweep = ((f * 2.2) % 3200) - 600;
  return (
    <AbsoluteFill style={{background: C.bg, overflow: 'hidden'}}>
      <Orb x={300} y={260} r={560} color={accent} speed={0.006} phase={0} />
      <Orb x={1620} y={820} r={640} color={C.violet} speed={0.005} phase={2} />
      <Orb x={1500} y={160} r={420} color={C.cyan} speed={0.008} phase={4} />
      <AbsoluteFill
        style={{
          backgroundImage:
            'linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px)',
          backgroundSize: '80px 80px', backgroundPosition: `${shift}px ${shift}px`,
          maskImage: 'radial-gradient(ellipse at center, black 25%, transparent 75%)',
          opacity: interpolate(f, [0, 20], [0, 1], {extrapolateRight: 'clamp'}),
        }}
      />
      <div
        style={{
          position: 'absolute', top: -400, left: sweep, width: 260, height: 2000, transform: 'rotate(24deg)',
          background: `linear-gradient(90deg, transparent, ${accent}14, transparent)`, filter: 'blur(20px)',
        }}
      />
    </AbsoluteFill>
  );
};
