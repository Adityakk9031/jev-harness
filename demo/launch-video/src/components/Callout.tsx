import React from 'react';
import {interpolate, random, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, INTER, glow} from '../theme';

type Pos = 'tr' | 'tl' | 'br' | 'bl' | 'center' | 'right' | 'left';
const place: Record<Pos, React.CSSProperties> = {
  tr: {top: 120, right: 80},
  tl: {top: 120, left: 80},
  br: {bottom: 170, right: 80},
  bl: {bottom: 170, left: 80},
  right: {top: '50%', right: 80, transform: 'translateY(-50%)'},
  left: {top: '50%', left: 80, transform: 'translateY(-50%)'},
  center: {top: '50%', left: '50%', transform: 'translate(-50%, -50%)'},
};

/** A big stat card. `count` animates the leading number up from zero; `glitch` adds an RGB split on entry. */
export const Callout: React.FC<{
  from: number; to?: number; big: string; label: string; color?: string; pos?: Pos; count?: number; size?: number; glitch?: boolean;
}> = ({from, to, big, label, color = C.jev, pos = 'tr', count, size = 120, glitch = false}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (f < from || (to !== undefined && f > to + 10)) return null;
  const s = spring({frame: f - from, fps, config: {damping: 12, stiffness: 150, mass: 0.9}});
  const out = to === undefined ? 0 : interpolate(f, [to, to + 10], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  let shown = big;
  if (count !== undefined) {
    const n = Math.round(interpolate(f - from, [0, 26], [0, count], {extrapolateRight: 'clamp', easing: (t) => 1 - Math.pow(1 - t, 3)}));
    shown = big.replace(String(count), String(n));
  }
  const g = glitch ? Math.max(0, 1 - (f - from) / 16) : 0;
  const jx = g * (random(`gx${f}`) - 0.5) * 30;
  const split = g * 10;
  const text: React.CSSProperties = {fontFamily: INTER, fontWeight: 900, fontSize: size, lineHeight: 1, letterSpacing: -3, whiteSpace: 'nowrap'};
  return (
    <div style={{position: 'absolute', ...place[pos], zIndex: 20}}>
      <div
        style={{
          transform: `scale(${(0.55 + 0.45 * s) * (1 - 0.15 * out)}) rotate(${(1 - s) * -5}deg) translateX(${jx}px)`,
          opacity: Math.min(1, s * 1.5) * (1 - out),
          background: 'rgba(10,14,24,0.86)', backdropFilter: 'blur(16px)', border: `2px solid ${color}99`, borderRadius: 30,
          padding: '28px 44px', boxShadow: `${glow(color, 34)}, 0 30px 90px rgba(0,0,0,0.7)`, minWidth: 420,
        }}
      >
        <div style={{position: 'relative'}}>
          {split > 0.5 && <div style={{...text, position: 'absolute', left: -split, top: 0, color: '#ff2a6d', opacity: 0.7, mixBlendMode: 'screen'}}>{shown}</div>}
          {split > 0.5 && <div style={{...text, position: 'absolute', left: split, top: 0, color: '#00e5ff', opacity: 0.7, mixBlendMode: 'screen'}}>{shown}</div>}
          <div style={{...text, position: 'relative', color, textShadow: `0 0 30px ${color}66`}}>{shown}</div>
        </div>
        <div style={{fontFamily: INTER, fontWeight: 700, fontSize: 34, color: C.ink, marginTop: 16, maxWidth: 660, lineHeight: 1.2}}>{label}</div>
      </div>
    </div>
  );
};
