// @vitest-environment node
//
// Not jsdom, unlike the rest of the viewer's tests: three of these read source
// text off disk — Mol*'s, and this module's own — and jsdom stubs `node:fs`
// out from under them. Nothing here needs a DOM.

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { refreshCopy } from './painterly';

/** The blit's uniforms, in the shape `createCopyRenderable` builds them.
 *
 * `uTexSize` deliberately shares one Vec2 across updates, because the real one
 * does: `refreshCopy` writes through `Vec2.set` into the existing ref value
 * rather than allocating. A fixture that handed out a fresh array each time
 * would pass whether or not the write happened.
 */
function fakeCopy(texture: unknown, width: number, height: number) {
  let updates = 0;
  return {
    values: {
      tColor: { ref: { value: texture } },
      uTexSize: { ref: { value: [width, height] } },
    },
    update: () => {
      updates += 1;
    },
    get updateCount() {
      return updates;
    },
  };
}

describe('the painterly blit, after a resize', () => {
  // The bug this file exists for. `syncSize` calls `scratch.setSize`, which
  // redefines the existing texture rather than replacing it, so a refresh
  // guarded on `tColor.ref.value !== scratch.texture` never fires and the blit
  // goes on sampling `gl_FragCoord.xy / uTexSize` at the size the pass was
  // built with. The frame still renders. Only its scale is wrong, which is
  // exactly the failure nothing in the suite is looking for.
  it('takes the new size even though the texture object is the same one', () => {
    const texture = { id: 'scratch' };
    const copy = fakeCopy(texture, 1200, 1000);

    refreshCopy(copy, texture, 1200, 1000);
    expect(copy.values.uTexSize.ref.value).toEqual([1200, 1000]);

    // The resize. Same object, new dimensions — what `setSize` leaves behind.
    refreshCopy(copy, texture, 800, 600);
    expect(
      copy.values.uTexSize.ref.value,
      'uTexSize kept the old size, so the blit samples at the wrong scale'
    ).toEqual([800, 600]);
  });

  it('still repoints the texture when the object really did change', () => {
    // The identity check was not pointless — it is what `tColor` is for. The
    // fix drops the guard, not the update, so this has to keep working.
    const first = { id: 'first' };
    const copy = fakeCopy(first, 800, 600);
    const second = { id: 'second' };

    refreshCopy(copy, second, 800, 600);

    expect(copy.values.tColor.ref.value).toBe(second);
  });

  it('tells the renderable that its values moved', () => {
    // `ValueCell.update` marks the cell; `renderable.update()` is what syncs it
    // to the render item. Writing the uniform without that call would leave the
    // GPU on the old value and every assertion above would still pass.
    const texture = { id: 'scratch' };
    const copy = fakeCopy(texture, 800, 600);

    refreshCopy(copy, texture, 400, 300);

    expect(copy.updateCount).toBe(1);
  });
});

describe("the Mol* behaviour this depends on", () => {
  // Read from the installed package rather than importing it: these are
  // assertions about Mol*'s source text, and importing the module would only
  // tell us it parses.
  const molstar = (path: string) =>
    readFileSync(new URL(`../node_modules/molstar/lib/${path}`, import.meta.url), 'utf8');

  it('setSize redefines the existing texture rather than replacing it', () => {
    // The premise. If Mol* ever starts handing out a new texture object on
    // resize, the unconditional refresh becomes belt-and-braces rather than
    // load-bearing — and whoever reads this should know which it is.
    const source = molstar('mol-gl/webgl/render-target.js');
    const setSize = source.slice(source.indexOf('setSize: (width, height)'));
    expect(setSize).toContain('targetTexture.define(_width, _height)');
    expect(
      setSize.slice(0, setSize.indexOf('}')),
      'setSize now assigns a new texture; re-read painterly.ts refreshCopy'
    ).not.toContain('targetTexture =');
  });

  it('the copy shader really does divide by uTexSize', () => {
    // If it sampled with an interpolated varying instead, `uTexSize` would not
    // matter and this whole file would be guarding nothing.
    expect(molstar('mol-gl/shader/copy.frag.js')).toContain(
      'gl_FragCoord.xy / uTexSize'
    );
  });
});

describe('the call site', () => {
  it('goes through refreshCopy, with no identity guard left behind', () => {
    // The function is only worth testing if the render path uses it. This is
    // the join that a unit test on an exported helper otherwise cannot make.
    const source = readFileSync(new URL('./painterly.ts', import.meta.url), 'utf8');
    expect(source).toContain('refreshCopy(state.copy, state.scratch.texture, width, height)');
    expect(
      source,
      'the guarded refresh is back; it cannot fire after a resize'
    ).not.toContain('state.copy.values.tColor.ref.value !== state.scratch.texture');
  });
});
