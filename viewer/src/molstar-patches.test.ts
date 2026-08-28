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
  MOLSTAR_SHADERS_ALREADY_FIXED,
  MOLSTAR_SHADERS_NEEDING_THE_FIX,
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
    expect(lf(patched as string)).toContain(FIXED_BACKGROUND_TEST);
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

  it('is a no-op on a shader that never had the predicate', () => {
    expect(patchBackgroundTest(read('quad.vert.js'), '/x/mol-gl/shader/quad.vert.js')).toBeNull();
  });
});
