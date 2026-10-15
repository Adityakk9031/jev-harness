import React from 'react';
import {AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
import focus from '../focus.json';
import {C, glow} from '../theme';
import {H, W} from '../timeline';
import {Cam, MOVE, camAt, camTransform} from './Camera';

export type FocusKey = keyof typeof focus;
/** `focus` zooms to a known rectangle; with no focus the first key floats the window in 3D, later ones show it flat. */
export type CamKey = {at: number; shot: string; focus?: FocusKey; color?: string; zoom?: number};

const FLOAT: Cam = {cx: W / 2, cy: H / 2, s: 0.7, ry: -17, rx: 8};
const FLAT: Cam = {cx: W / 2, cy: H / 2, s: 0.88};

const camFor = (k: CamKey, first: boolean): Cam => {
  if (!k.focus) return first ? FLOAT : FLAT;
  const r = focus[k.focus];
  const s = Math.max(1.25, Math.min(k.zoom ?? 2.6, 1500 / r.w, 520 / r.h));
  return {cx: r.x + r.w / 2, cy: r.y + r.h / 2, s};
};

/**
 * A real screenshot under a camera. The scene opens on the window floating in 3D, then each key
 * flies the camera to the line being talked about and puts a glowing spotlight on it.
 */
export const Shot: React.FC<{keys: CamKey[]}> = ({keys}) => {
  const f = useCurrentFrame();
  const stops = keys.map((k, n) => ({...camFor(k, n === 0), at: k.at}));
  const {cam, i, p} = camAt(stops, f, MOVE);
  // While the first (floating) key holds, slowly turn the window toward the viewer.
  if (i === 0 && !keys[0].focus) {
    const turn = interpolate(f, [0, 90], [0, 1], {extrapolateRight: 'clamp'});
    cam.ry = -17 + 7 * turn;
    cam.s = 0.7 + 0.05 * turn;
  }
  const cur = keys[i];
  const prev = keys[Math.max(0, i - 1)];
  const shotChanged = i > 0 && prev.shot !== cur.shot;
  const fadeIn = shotChanged ? interpolate(f - cur.at, [0, 9], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1;
  const spot = cur.focus ? focus[cur.focus] : null;
  const spotIn = interpolate(f - cur.at, [MOVE * 0.55, MOVE + 4], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const color = cur.color ?? C.jev;
  const pulse = 0.75 + 0.25 * Math.sin(f / 5);
  const blur = i === 0 ? 0 : Math.sin(p * Math.PI) * 2.5;
  const flat = (cam.ry ?? 0) === 0 && cam.s >= 1;
  const glare = interpolate(f, [8, 70], [-0.4, 1.4], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{perspective: 2000}}>
      <AbsoluteFill style={{transform: camTransform(cam, flat), transformOrigin: '50% 50%', filter: blur > 0.2 ? `blur(${blur}px)` : undefined}}>
        {shotChanged && <Img src={staticFile(`shots/${prev.shot}`)} style={{position: 'absolute', width: W, height: H, borderRadius: 22, opacity: 1 - fadeIn}} />}
        <Img
          src={staticFile(`shots/${cur.shot}`)}
          style={{
            position: 'absolute', width: W, height: H, borderRadius: 22, opacity: fadeIn,
            boxShadow: `0 50px 140px rgba(0,0,0,0.75), 0 0 0 2px rgba(255,255,255,0.06), ${glow(color, 30)}`,
          }}
        />
        {i === 0 && !keys[0].focus && (
          <AbsoluteFill
            style={{
              borderRadius: 22, overflow: 'hidden', pointerEvents: 'none',
              background: `linear-gradient(115deg, transparent ${glare * 100 - 18}%, rgba(255,255,255,0.10) ${glare * 100}%, transparent ${glare * 100 + 18}%)`,
            }}
          />
        )}
        {spot && (
          <div
            style={{
              position: 'absolute', left: spot.x - 8, top: spot.y - 6, width: spot.w + 16, height: spot.h + 12,
              borderRadius: 10, border: `${Math.max(1.5, 4.2 / cam.s)}px solid ${color}`, opacity: spotIn,
              boxShadow: `0 0 0 9999px rgba(3,5,10,${0.62 * spotIn}), ${glow(color, 22 * pulse)}`,
            }}
          />
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
