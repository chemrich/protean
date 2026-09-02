const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction-shaders.ts', 'utf8');
code = code.replace(
  'gl_FragColor = vec4(finalColor, max(opaqueColor.a, transparentColor.a));',
  'gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0);\n    return;'
);
fs.writeFileSync('viewer/src/refraction-shaders.ts', code);
