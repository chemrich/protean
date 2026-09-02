const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction.ts', 'utf8');

code = code.replace(
  'const toDrawingBuffer = args[2];',
  'const toDrawingBuffer = args[2];\n    // console.log("opacityAverage:", scene.opacityAverage);'
);
fs.writeFileSync('viewer/src/refraction.ts', code);
