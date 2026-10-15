import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';
import {H, W} from '../timeline';

/** A camera position over a 1920x1080 layer: centre point, scale, and optional 3D tilt. */
export type Cam = {cx: number; cy: number; s: number; ry?: number; rx?: number};
export type CamStop = Cam & {at: number};

export const MOVE = 24; // frames per camera move
export const heavy = Easing.bezier(0.83, 0, 0.17, 1);

const full = (c: Cam): Required<Cam> => ({cx: c.cx, cy: c.cy, s: c.s, ry: c.ry ?? 0, rx: c.rx ?? 0});

/** Where move `i` has got to at frame `f`; each move starts from wherever the previous one was. */
const poseAt = (stops: CamStop[], i: number, f: number, move: number): Required<Cam> => {
  if (i === 0) return full(stops[0]);
  const from = poseAt(stops, i - 1, stops[i].at, move);
  const to = full(stops[i]);
  const p = interpolate(f - stops[i].at, [0, move], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: heavy});
  const mix = (a: number, b: number) => a + (b - a) * p;
  return {cx: mix(from.cx, to.cx), cy: mix(from.cy, to.cy), s: mix(from.s, to.s), ry: mix(from.ry, to.ry), rx: mix(from.rx, to.rx)};
};

/** Camera state at frame `f` for a list of stops, with a heavy ease and a mid-move zoom punch. */
export const camAt = (stops: CamStop[], f: number, move = MOVE) => {
  let i = 0;
  while (i + 1 < stops.length && stops[i + 1].at <= f) i++;
  const cur = stops[i];
  const p = i === 0 ? 1 : interpolate(f - cur.at, [0, move], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: heavy});
  const cam = poseAt(stops, i, f, move);
  const drift = Math.min(1.035, 1 + 0.00035 * (f - cur.at));
  const punch = i === 0 ? 0 : Math.sin(p * Math.PI) * 0.07;
  cam.s *= drift * (1 + punch);
  return {cam, i, p, cur, moving: p > 0 && p < 1};
};

/** CSS transform (origin: layer centre) that puts (cx, cy) at the middle of the frame. */
export const camTransform = ({cx, cy, s, ry = 0, rx = 0}: Cam, clamp = true) => {
  let tx = W / 2 - cx * s;
  let ty = H / 2 - cy * s;
  if (clamp && s >= 1) {
    // Keep the shot mostly in frame, but let the camera centre a line near an edge.
    tx = Math.min(180, Math.max(W - W * s - 180, tx));
    ty = Math.min(240, Math.max(H - H * s - 240, ty));
  }
  const dx = tx + (W / 2) * (s - 1);
  const dy = ty + (H / 2) * (s - 1);
  return `translate(${dx}px, ${dy}px) scale(${s}) rotateY(${ry}deg) rotateX(${rx}deg)`;
};

/** Moves its children (a 1920x1080 layer) like a camera between stops. */
export const CameraLayer: React.FC<{stops: CamStop[]; children: React.ReactNode; clamp?: boolean}> = ({stops, children, clamp = false}) => {
  const f = useCurrentFrame();
  const {cam, p, i} = camAt(stops, f);
  const blur = i === 0 ? 0 : Math.sin(p * Math.PI) * 2.2;
  return (
    <AbsoluteFill style={{perspective: 2200}}>
      <AbsoluteFill style={{transform: camTransform(cam, clamp), transformOrigin: '50% 50%', filter: blur > 0.2 ? `blur(${blur}px)` : undefined}}>
        {children}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
