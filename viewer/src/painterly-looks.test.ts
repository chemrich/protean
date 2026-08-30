import { describe, expect, it } from 'vitest';

import { PAINTERLY_LOOKS, sectorWeight } from './painterly-looks';
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
