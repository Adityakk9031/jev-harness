import React from 'react';
import {AbsoluteFill, Audio, Sequence, interpolateColors, staticFile, useCurrentFrame} from 'remotion';
import {Background} from './components/Background';
import {Captions} from './components/Captions';
import {Grain, Sfx} from './components/Fx';
import {SceneShell} from './components/SceneShell';
import {Architecture} from './scenes/Architecture';
import {Cta} from './scenes/Cta';
import {Hook} from './scenes/Hook';
import {Intro} from './scenes/Intro';
import {Problem} from './scenes/Problem';
import {Promises} from './scenes/Promises';
import {Cal1, Cal2, Comp, Guard1, Guard2, Guard3, Rev1, Rev2, Rev3} from './scenes/Tools';
import {C} from './theme';
import {SCENES, SceneId, sceneById} from './timeline';

const BODY: Record<SceneId, React.FC> = {
  hook: Hook, problem: Problem, intro: Intro, arch: Architecture,
  guard1: Guard1, guard2: Guard2, guard3: Guard3, cal1: Cal1, cal2: Cal2, comp: Comp,
  rev1: Rev1, rev2: Rev2, rev3: Rev3, promises: Promises, cta: Cta,
};

const ACCENT: Record<SceneId, string> = {
  hook: C.jev, problem: C.warn, intro: C.jev, arch: C.cyan,
  guard1: C.jev, guard2: C.warn, guard3: C.jev, cal1: C.cyan, cal2: C.cyan, comp: C.violet,
  rev1: C.amber, rev2: C.amber, rev3: C.warn, promises: C.jev, cta: C.jev,
};

const accentAt = (f: number) => {
  let k = 0;
  while (k + 1 < SCENES.length && SCENES[k + 1].start <= f) k++;
  const s = SCENES[k];
  if (k === 0) return ACCENT[s.id];
  return interpolateColors(f - s.start, [0, 24], [ACCENT[SCENES[k - 1].id], ACCENT[s.id]]);
};

export const Launch: React.FC = () => {
  const f = useCurrentFrame();
  const intro = sceneById('intro');
  return (
    <AbsoluteFill style={{background: C.bg}}>
      <Background accent={accentAt(f)} />
      {SCENES.map((s) => {
        const Body = BODY[s.id];
        return (
          <Sequence key={s.id} from={s.start} durationInFrames={s.duration} name={s.id}>
            <SceneShell duration={s.duration} enter={s.id === 'intro' || s.id === 'cta' ? 1 : 14}>
              <Body />
            </SceneShell>
            <Captions words={s.words} />
            <Sequence from={s.lead} layout="none">
              <Audio src={staticFile(`voice/${s.id}.wav`)} volume={1} />
            </Sequence>
          </Sequence>
        );
      })}
      {SCENES.slice(1).map((s) => (
        <Sfx key={s.id} at={s.start - 9} name="whoosh" volume={s.id === 'intro' || s.id === 'cta' ? 0 : 0.3} />
      ))}
      <Sfx at={intro.start - 60} name="riser" volume={0.55} />
      <Sfx at={sceneById('cta').start - 45} name="riser" volume={0.4} />
      <Audio src={staticFile('audio/music.wav')} volume={1} />
      <Grain />
    </AbsoluteFill>
  );
};
