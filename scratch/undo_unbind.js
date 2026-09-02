const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction.ts', 'utf8');

code = code.replace(
  'state.refraction.update();\n  // Unbind ALL textures to prevent feedback loops\n  for (let i = 0; i < 8; i++) {\n    gl.activeTexture(gl.TEXTURE0 + i);\n    gl.bindTexture(gl.TEXTURE_2D, null);\n  }\n  webgl.state.currentTextureBuffers.fill(null);',
  'state.refraction.update();'
);

fs.writeFileSync('viewer/src/refraction.ts', code);
