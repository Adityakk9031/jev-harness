import {loadFont as loadInter} from '@remotion/google-fonts/Inter';
import {loadFont as loadMono} from '@remotion/google-fonts/JetBrainsMono';

export const INTER = loadInter('normal', {weights: ['400', '600', '800', '900'], subsets: ['latin']}).fontFamily;
export const MONO = loadMono('normal', {weights: ['500', '800'], subsets: ['latin']}).fontFamily;

export const C = {
  bg: '#05070d',
  ink: '#f4f7fb',
  dim: '#8a94a6',
  jev: '#7CFFB2',
  cyan: '#5EE7FF',
  violet: '#A78BFA',
  warn: '#FF5C7A',
  amber: '#FFC857',
  card: 'rgba(18, 24, 38, 0.72)',
  line: 'rgba(255,255,255,0.10)',
};

export const glow = (color: string, size = 40) =>
  `0 0 ${size}px ${color}55, 0 0 ${size * 2}px ${color}22`;
