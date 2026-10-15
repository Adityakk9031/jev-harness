import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {Sfx} from '../components/Fx';
import {C, INTER, MONO, glow} from '../theme';

export const Logo: React.FC<{size?: number}> = ({size = 190}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: size * 0.12}}>
    <div
      style={{
        width: size * 0.62, height: size * 0.62, borderRadius: size * 0.16, background: `linear-gradient(135deg, ${C.jev}, ${C.cyan})`,
        display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: glow(C.jev, size * 0.25),
        fontFamily: MONO, fontWeight: 800, fontSize: size * 0.34, color: '#05070d',
      }}
    >
      {'>_'}
    </div>
    <div style={{fontFamily: INTER, fontWeight: 900, fontSize: size, color: C.ink, letterSpacing: -size * 0.045}}>
      jev<span style={{color: C.jev}}>-</span>harness
    </div>
  </div>
);

export const Intro: React.FC = () => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const zoom = spring({frame: f, fps, config: {damping: 14, stiffness: 90}});
  const flash = interpolate(f, [0, 3, 14], [0, 0.9, 0], {extrapolateRight: 'clamp'});
  const tag = 'Test your agent setup the way you test your code.';
  const typed = Math.round(interpolate(f, [26, 26 + tag.length * 1.1], [0, tag.length], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
  const ring = interpolate(f, [0, 40], [0, 1], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <div
        style={{
          position: 'absolute', width: 1400 * ring, height: 1400 * ring, borderRadius: '50%', border: `3px solid ${C.jev}`,
          opacity: 1 - ring, boxShadow: glow(C.jev, 60),
        }}
      />
      <div style={{transform: `scale(${3.2 - 2.2 * zoom})`, filter: `blur(${(1 - zoom) * 18}px)`}}>
        <Logo />
      </div>
      <div style={{fontFamily: INTER, fontWeight: 600, fontSize: 52, color: C.dim, marginTop: 50, height: 70}}>
        {tag.slice(0, typed)}
        <span style={{opacity: f % 16 < 8 ? 1 : 0, color: C.jev}}>▍</span>
      </div>
      <AbsoluteFill style={{background: '#fff', opacity: flash}} />
      <Sfx at={0} name="boom" volume={0.8} />
    </AbsoluteFill>
  );
};
