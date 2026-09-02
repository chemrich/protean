const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction.ts', 'utf8');
code = code.replace(
  'if (false && scene.opacityAverage < 1 && settings.enabled) {',
  'if (scene.opacityAverage < 1 && settings.enabled) {'
);
fs.writeFileSync('viewer/src/refraction.ts', code);
