import { describe, expect, it } from 'vitest';

import { PAINTERLY_LOOKS, boostChroma, rgb2hsl, sectorWeight } from './painterly-looks';
import { painterly_brush_frag } from './painterly-shaders';

describe('the brush sector weight', () => {
  // The spread between the flattest and the busiest sector the brush will meet
  // on a real subject. Below is a smooth shaded interior, above is a boundary
  // between two colours — which is exactly what the filter is supposed to
  // refuse to average across.
  const FLAT = 0.02 ** 2;
  const BUSY = 0.12 ** 2;

  it('can actually tell a flat sector from a busy one', () => {
    // Asked of the *formula*, at a reference that governs it — not of each
    // shipped look. Whether a look wants the abstraction is taste; whether the
    // filter is capable of it is correctness, and only the second belongs in a
    // test. The broken form's entire dynamic range was 1.004x, which a
    // "differs" assertion would have accepted.
    const flat = sectorWeight(FLAT, 8, 0.03);
    const busy = sectorWeight(BUSY, 8, 0.03);
    expect(flat / busy).toBeGreaterThan(50);
  });

  it('is ungoverned at varRef 1.0, which is what chiaroscuro asks for', () => {
    // The look Charlie picked runs the filter with no reference at all, so it
    // is an anisotropic Gaussian blur and *cannot* separate a flat sector from
    // a busy one. That is the look, not a defect — it was compared against the
    // governed version and chosen. Asserted so that a later reader who finds
    // the ratio below and calls it broken has to change this line and read
    // why first.
    const flat = sectorWeight(FLAT, 8, 1.0);
    const busy = sectorWeight(BUSY, 8, 1.0);
    expect(flat / busy).toBeLessThan(1.001);
    expect(PAINTERLY_LOOKS.chiaroscuro.varRef).toBe(1.0);
  });

  it('runs the right way round: harder means more selective', () => {
    const ratio = (hardness: number) =>
      sectorWeight(FLAT, hardness, 0.03) / sectorWeight(BUSY, hardness, 0.03);
    // The broken form ran *backwards* — its (vanishing) selectivity fell as
    // hardness rose, so the knob's own docstring described the opposite of what
    // it did.
    expect(ratio(12)).toBeGreaterThan(ratio(8));
    expect(ratio(8)).toBeGreaterThan(ratio(4));
  });

  it('is the formula the shader is running', () => {
    // The duplication's price, paid here. A change to the GLSL that does not
    // reach `sectorWeight` leaves every assertion above testing a formula
    // nobody runs — which is this project's oldest failure wearing a test's
    // clothes.
    expect(painterly_brush_frag).toContain('float scaled = variance / (uVarRef * uVarRef);');
    expect(painterly_brush_frag).toContain(
      'float w = 1.0 / (1.0 + pow(scaled, 0.5 * uHardness));'
    );
  });
});

describe("impasto's hue-preserving chroma boost", () => {
  // A saturated blue-violet, the specific chain the naive approach is known
  // to fail on: pushed hard, it drifts toward magenta rather than just
  // getting bolder.
  const BLUE_VIOLET: [number, number, number] = [0.35, 0.08, 0.85];
  const hueDeg = (rgb: [number, number, number]) => rgb2hsl(rgb)[0] * 360;

  const naiveBoost = (rgb: [number, number, number], boost: number): [number, number, number] => {
    const [r, g, b] = rgb;
    const L = 0.299 * r + 0.587 * g + 0.114 * b;
    const clamp = (x: number) => Math.min(1, Math.max(0, x));
    return [clamp(L + (r - L) * (1 + boost)), clamp(L + (g - L) * (1 + boost)), clamp(L + (b - L) * (1 + boost))];
  };

  it('cannot rotate hue, by construction — only saturation is in the equation', () => {
    const before = hueDeg(BLUE_VIOLET);
    // Well past impasto's own 0.5, to show this holds under a boost harder
    // than any shipped look asks for, not just the one that happens to ship.
    for (const boost of [0.5, 2.0, 5.0]) {
      const after = hueDeg(boostChroma(BLUE_VIOLET, boost));
      expect(Math.abs(after - before)).toBeLessThan(1e-6);
    }
  });

  it('is not a phantom fix: the naive per-channel clamp really does rotate this hue toward magenta', () => {
    // The positive control. If this drifted by nothing either, the
    // hue-preserving version above would be solving a problem that does not
    // exist rather than the one its own docstring names.
    const before = hueDeg(BLUE_VIOLET);
    const after = hueDeg(naiveBoost(BLUE_VIOLET, 5.0));
    const drift = after - before;
    expect(drift).toBeGreaterThan(15);
    // Magenta is 300°; drifting *toward* it from this blue-violet's ~261°
    // means increasing, not decreasing or wrapping the other way.
    expect(after).toBeGreaterThan(before);
    expect(after).toBeLessThan(300);
  });

  it('is the formula the shader is running', () => {
    // Same duplication price as sectorWeight above: a change to the GLSL
    // tone-mapping that does not reach here leaves the tests above
    // exercising a formula nobody runs.
    expect(painterly_brush_frag).toContain('if (uChromaBoost > 0.0) {');
    expect(painterly_brush_frag).toContain(
      'hsl.y = clamp(hsl.y * (1.0 + uChromaBoost), 0.0, 1.0);'
    );
  });

  it('is also the conversion the shader is running, not just the call site', () => {
    // The call site above is two lines; the conversion it calls is the part
    // that actually has room for a wrong branch boundary, a transposed
    // r/g/b, or a sign flip to hide in — none of which would touch the two
    // lines the test above checks. Every branch of `rgb2hsl`, `hueToRgb` and
    // `hsl2rgb` that decides *which* value comes back, quoted verbatim.
    for (const line of [
      // rgb2hsl: which channel is max decides the hue branch, and each
      // branch's own algebra decides where in the wheel it lands.
      'if (maxc == c.r) {',
      'h = mod((c.g - c.b) / d, 6.0);',
      '} else if (maxc == c.g) {',
      'h = (c.b - c.r) / d + 2.0;',
      'h = (c.r - c.g) / d + 4.0;',
      's = d / (1.0 - abs(2.0 * l - 1.0));',
      // hueToRgb: the four-way split a hue is reconstructed through.
      'if (t < 1.0 / 6.0) return p + (q - p) * 6.0 * t;',
      'if (t < 0.5) return q;',
      'if (t < 2.0 / 3.0) return p + (q - p) * (2.0 / 3.0 - t) * 6.0;',
      // hsl2rgb: the p/q pair every hueToRgb call above is built from.
      'float q = l < 0.5 ? l * (1.0 + s) : l + s - l * s;',
      'float p = 2.0 * l - q;',
      'hueToRgb(p, q, h + 1.0 / 3.0)',
      'hueToRgb(p, q, h - 1.0 / 3.0)',
    ]) {
      expect(painterly_brush_frag).toContain(line);
    }
  });
});
