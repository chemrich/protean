const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction.ts', 'utf8');

// Change signature of buildRefractionState
code = code.replace(
  'function buildRefractionState(webgl: any, width: number, height: number): RefractionPassState {',
  'function buildRefractionState(webgl: any, width: number, height: number, tColor: any, tTrans: any, tDepthOp: any, tDepthTrans: any): RefractionPassState {'
);

// Replace the ValueCell.create(scratch.texture) with the actual textures
code = code.replace(
  'tColor: ValueCell.create(scratch.texture),',
  'tColor: ValueCell.create(tColor),'
);
code = code.replace(
  'tTransparentColor: ValueCell.create(scratch.texture),',
  'tTransparentColor: ValueCell.create(tTrans),'
);
code = code.replace(
  'tDepthOpaque: ValueCell.create(scratch.texture),',
  'tDepthOpaque: ValueCell.create(tDepthOp),'
);
code = code.replace(
  'tDepthTransparent: ValueCell.create(scratch.texture),',
  'tDepthTransparent: ValueCell.create(tDepthTrans),'
);

// Update call site
code = code.replace(
  'state = buildRefractionState(webgl, width, height);',
  'state = buildRefractionState(webgl, width, height, colorSource.texture, transparentColorSource.texture, depthOpaque, depthTransparent);'
);

fs.writeFileSync('viewer/src/refraction.ts', code);
