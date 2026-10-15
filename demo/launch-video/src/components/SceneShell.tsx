import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';

/** Zoom-through transitions: each scene flies in from behind and out past the camera. */
export const SceneShell: React.FC<{children: React.ReactNode; duration: number; enter?: number; exit?: number}> = ({
  children, duration, enter = 14, exit = 9,
}) => {
  const f = useCurrentFrame();
  const inP = interpolate(f, [0, enter], [0, 1], {extrapolateRight: 'clamp', easing: Easing.bezier(0.16, 1, 0.3, 1)});
  const outP = interpolate(f, [duration - exit, duration], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.cubic)});
  const scale = (0.84 + 0.16 * inP) * (1 + 0.35 * outP);
  const blur = (1 - inP) * 16 + outP * 18;
  return (
    <AbsoluteFill style={{opacity: Math.min(inP * 1.5, 1) * (1 - outP), transform: `scale(${scale})`, filter: blur > 0.3 ? `blur(${blur}px)` : undefined}}>
      {children}
    </AbsoluteFill>
  );
};
