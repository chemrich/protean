const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction.ts', 'utf8');

code = code.replace(
  'state.refraction.render(); const err1 = webgl.gl.getError(); if (err1 !== 0) console.error("ERR1 AFTER REFRACTION:", err1);',
  'state.refraction.render(); const err1 = webgl.gl.getError(); if (err1 !== 0) window.GL_ERROR_1 = err1;'
);
code = code.replace(
  'state.copy.render(); const err2 = webgl.gl.getError(); if (err2 !== 0) console.error("ERR2 AFTER COPY:", err2);',
  'state.copy.render(); const err2 = webgl.gl.getError(); if (err2 !== 0) window.GL_ERROR_2 = err2;'
);

fs.writeFileSync('viewer/src/refraction.ts', code);
