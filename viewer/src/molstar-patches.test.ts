// @vitest-environment node
//
// Not jsdom, which is this project's default and the right one for everything
// else here. These tests read the shipped Mol* shaders off disk, and under jsdom
// Vite externalises `node:fs` for browser compatibility and `join` arrives
// undefined.
/**
 * Guards the Mol* shader patch, in the two directions it can go wrong.
 *
 * A find-and-replace against a dependency's source has two failure modes and
 * neither one shows up in a picture. It can stop matching, and every capture
 * silently costs three times what it should. Or upstream can fix the thing, and
 * protean carries a patch nobody needs against files nobody is watching. The
 * build catches the first (`vite.config.ts` errors if it patched nothing); these
 * catch both, against the real files in `node_modules`.
 */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  BROKEN_BACKGROUND_TEST,
  FIXED_BACKGROUND_TEST,
  FIXED_BACKGROUND_TEST_16BIT,
  MOLSTAR_SHADERS_ALREADY_FIXED,
  MOLSTAR_SHADERS_NEEDING_THE_FIX,
  SHADER_NEEDING_16BIT_FIX,
  patchBackgroundTest,
} from './molstar-patches';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const SHADERS = join(HERE, '..', 'node_modules', 'molstar', 'lib', 'mol-gl', 'shader');

/** Mol* ships these shaders with CRLF inside the GLSL; compare on LF. */
function lf(text: string): string {
  return text.replace(/\r\n/g, '\n');
}

function read(relative: string): string {
  const path = join(SHADERS, relative);
  if (!existsSync(path)) {
    throw new Error(
      `${relative} is not in the installed Mol*. The patch in molstar-patches.ts ` +
        'names shaders that no longer exist; re-derive the list before trusting it.'
    );
  }
  return readFileSync(path, 'utf8');
}

describe('the Mol* background-test patch', () => {
  it.each(MOLSTAR_SHADERS_NEEDING_THE_FIX)('rewrites %s', (relative) => {
    const source = read(relative);
    // The shipped file is the thing being asserted about, not a fixture: if
    // Mol* reindents this function or renames it, this fails here rather than
    // in a capture nobody is timing.
    expect(lf(source)).toContain(BROKEN_BACKGROUND_TEST);

    const patched = patchBackgroundTest(source, `/x/mol-gl/shader/${relative}`);
    expect(patched).not.toBeNull();
    const expected = relative.includes(SHADER_NEEDING_16BIT_FIX)
      ? FIXED_BACKGROUND_TEST_16BIT
      : FIXED_BACKGROUND_TEST;
    expect(lf(patched as string)).toContain(expected);
    expect(lf(patched as string)).not.toContain(BROKEN_BACKGROUND_TEST);
    // The line endings the file arrived with are the ones it leaves with.
    expect((patched as string).includes('\r\n')).toBe(source.includes('\r\n'));
  });

  it.each(MOLSTAR_SHADERS_ALREADY_FIXED)('leaves %s alone, upstream got there', (relative) => {
    const source = read(relative);
    expect(lf(source)).toContain(FIXED_BACKGROUND_TEST);
    expect(patchBackgroundTest(source, `/x/mol-gl/shader/${relative}`)).toBeNull();
  });

  it('touches nothing outside Mol*’s shaders', () => {
    // The id check is what stops this replacing the same text in protean's own
    // source, or in a test fixture, or in this file.
    const source = read('ssao.frag.js');
    expect(patchBackgroundTest(source, '/x/src/painterly.ts')).toBeNull();
  });

  it('gives ssao-blur the 16-bit constant, because the 24-bit one cannot fire', () => {
    // This is the defect the first version of the patch shipped: one constant
    // for six shaders, when ssao-blur reads a different encoding. The assertion
    // is not "we chose a different number" but "the number we chose is the only
    // one of the two that can ever be true for that encoding" — computed here
    // from Mol*'s own packUnitIntervalToRG / unpackRGToUnitInterval rather than
    // quoted, so a change to either of them fails this test.
    const packThenUnpack = (v: number): number => {
      // packUnitIntervalToRG: c = fract(vec2(1.0, 256.0) * v); c.x -= c.y / 256.0
      const fract = (x: number): number => x - Math.floor(x);
      let x = fract(v);
      const y = fract(v * 256);
      x -= y / 256;
      // the render target is uint8 rgba, so both channels quantise
      const q = (c: number): number => Math.min(255, Math.max(0, Math.round(c * 255))) / 255;
      // unpackRGToUnitInterval: dot(v, vec2(1.0, 1.0 / 256.0))
      return Math.fround(q(x) + q(y) / 256);
    };
    // What a transparent-background texel becomes by the time the blur reads it.
    const seen = packThenUnpack(Math.fround(16777215 / 16777216));
    expect(seen).toBeCloseTo(0.99998468, 8);
    expect(seen >= 0.99999994).toBe(false); // the 24-bit constant: never fires
    expect(seen >= 0.999).toBe(true); // the 16-bit constant: fires

    const patched = patchBackgroundTest(
      read('ssao-blur.frag.js'),
      '/x/mol-gl/shader/ssao-blur.frag.js'
    );
    expect(lf(patched as string)).toContain(FIXED_BACKGROUND_TEST_16BIT);
    expect(lf(patched as string)).not.toContain(FIXED_BACKGROUND_TEST);
  });

  it('is a no-op on a shader that never had the predicate', () => {
    expect(patchBackgroundTest(read('quad.vert.js'), '/x/mol-gl/shader/quad.vert.js')).toBeNull();
  });
});
