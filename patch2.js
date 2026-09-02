const fs = require('fs');
let code = fs.readFileSync('viewer/src/refraction.ts', 'utf8');

const replacement = `  (PostprocessingPass.prototype as any).render = function (this: any, ...args: any[]) {
    const result = originalPostprocessingRender.apply(this, args);
    const camera = args[0];
    const scene = args[1];
    const toDrawingBuffer = args[2];
`;
code = code.replace(`  (PostprocessingPass.prototype as any).render = function (this: any, ...args: any[]) {
    const result = originalPostprocessingRender.apply(this, args);
    const scene = args[1];
`, replacement);

fs.writeFileSync('viewer/src/refraction.ts', code.replace('scene.opacityAverage < 1 && settings.enabled', 'scene.opacityAverage < 1 && scene.opacityAverage > 0 && settings.enabled'));
