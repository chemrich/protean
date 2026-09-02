const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction.ts', 'utf8');
code = code.replace(
  'state.refraction.render();',
  'state.refraction.render(); console.log("after refraction:", webgl.gl.getError());'
);
code = code.replace(
  'state.copy.render();',
  'state.copy.render(); console.log("after copy:", webgl.gl.getError());'
);
fs.writeFileSync('viewer/src/refraction.ts', code);
