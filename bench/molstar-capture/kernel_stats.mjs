// What the SSAO kernel actually looks like, in each release.
//
// Mol* 5.6.0 replaced 256 Math.random() hemisphere vectors with best-candidate
// blue noise. Whether that costs anything is measured elsewhere (run
// 33254381944, via bundle_tweak's `candidates-1`); this says what changed.
//
// It is here rather than in a notebook because a first pass at it was wrong
// twice, in opposite directions, and both errors are the kind that read as
// confident prose:
//
//   * Reading only `getRandomHemisphereVector` says 5.6.0's kernel is TIGHTER
//     than 5.5.0's — in-plane 0.594x — because it doubles z before normalising.
//     That leaves out `generateBlueNoiseVectors`, and the best-candidate pass on
//     top is the whole story: maximising the minimum distance to the samples
//     already chosen is systematically easier to satisfy further out, so the
//     selection biases the radial distribution outward.
//
//   * Sampling the algorithm with a random RNG gives its EXPECTATION, +41%
//     radius and +16% in-plane. But 5.6.0's generator is a PCG with a fixed
//     seed, so Mol* uploads ONE kernel, every time, and its actual figures are
//     +34% and +7%. The expectation overstates the in-plane spread by more than
//     a factor of two, and in-plane is the part that moves a texture fetch.
//
// So: replicate the real generators and print the real numbers. 5.5.0's table
// is built with Math.random() at module load, so it genuinely IS a
// distribution — a different kernel every page load, +/-6% on mean |s| — which
// is a source of run-to-run variance 5.6.0 does not have and 5.5.0 rows do.
//
//     node bench/molstar-capture/kernel_stats.mjs
//
// The PCG, the two generators and the scale ramp are transcribed from
// molstar 5.6.0's lib/mol-data/util/hash-functions.js and
// lib/mol-canvas3d/passes/ssao.js.

class PCG {
  constructor(seed = 26699) { this.state = seed >>> 0; }
  int() {
    const oldstate = this.state;
    this.state = Math.imul(this.state, 1664525) + 1013904223;
    this.state = this.state >>> 0;
    const xorshifted = ((oldstate >>> 18) ^ oldstate) >>> 5;
    const rot = oldstate >>> 27;
    return (((xorshifted >>> rot) | (xorshifted << (32 - rot))) >>> 0);
  }
  float() { return this.int() / 0x100000000; }
}
const V = {
  set: (o,x,y,z)=>{o[0]=x;o[1]=y;o[2]=z;return o},
  normalize: (o,a)=>{const d=Math.hypot(a[0],a[1],a[2])||1;o[0]=a[0]/d;o[1]=a[1]/d;o[2]=a[2]/d;return o},
  scale: (o,a,s)=>{o[0]=a[0]*s;o[1]=a[1]*s;o[2]=a[2]*s;return o},
  distance: (a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]),
};
const pcg = new PCG();
function getRandomHemisphereVector() {
  const v = [0,0,0];
  while (true) {
    const x = pcg.float()*2-1, y = pcg.float()*2-1;
    if (x*x + y*y < 1) {
      const z = 2*Math.sqrt(1-x*x-y*y)*(pcg.float() < 0.5 ? -1 : 1);
      V.set(v,x,y,z); V.normalize(v,v); V.scale(v,v,pcg.float()); break;
    }
  }
  if (v[2] < 0) v[2] = -v[2];
  return v;
}
function generateBlueNoiseVectors(count, out) {
  if (out.length >= count) return out;
  if (out.length === 0) out.push(getRandomHemisphereVector());
  const candidateCount = Math.max(10, Math.min(30, Math.floor(count/10)));
  for (let i = out.length; i < count; i++) {
    let best, bestD = -1;
    for (let j = 0; j < candidateCount; j++) {
      const c = getRandomHemisphereVector();
      let mind = Infinity;
      for (const e of out) mind = Math.min(mind, V.distance(c,e));
      if (mind > bestD) { bestD = mind; best = c; }
    }
    out.push(best);
  }
  return out;
}
// 5.5.0: a 256-entry table built once with Math.random()
function oldVectors(n, rnd) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const v = [rnd()*2-1, rnd()*2-1, rnd()];
    V.normalize(v,v); V.scale(v,v,rnd()); out.push(v);
  }
  return out;
}
function samples(vecs, n) {
  const s = [];
  for (let i = 0; i < n; i++) {
    let sc = (i*i + 2*i + 1)/(n*n); sc = 0.1 + sc*(1-0.1);
    s.push([vecs[i][0]*sc, vecs[i][1]*sc, vecs[i][2]*sc]);
  }
  return s;
}
const stat = s => ({
  r: s.reduce((a,v)=>a+Math.hypot(v[0],v[1],v[2]),0)/s.length,
  xy: s.reduce((a,v)=>a+Math.hypot(v[0],v[1]),0)/s.length,
});
const N = 128;
// The 5.6.0 pass builds 32 first (SsaoPass construction) then grows to 128.
const cache = [];
generateBlueNoiseVectors(32, cache);
generateBlueNoiseVectors(N, cache);
const b = stat(samples(cache, N));
console.log(`5.6.0, the exact kernel Mol* uploads (PCG seed 26699, cache grown 32 -> 128):`);
console.log(`   mean |s| ${b.r.toFixed(4)}   mean |s.xy| ${b.xy.toFixed(4)}`);
// 5.5.0's table is Math.random, so it differs per page load: report the spread.
let rs=[], xys=[];
for (let t = 0; t < 400; t++) { const a = stat(samples(oldVectors(256, Math.random), N)); rs.push(a.r); xys.push(a.xy); }
const mean = a => a.reduce((x,y)=>x+y,0)/a.length;
const sd = a => Math.sqrt(mean(a.map(x=>(x-mean(a))**2)));
console.log(`5.5.0, Math.random so a fresh table every page load, 400 draws:`);
console.log(`   mean |s| ${mean(rs).toFixed(4)} +- ${sd(rs).toFixed(4)}   mean |s.xy| ${mean(xys).toFixed(4)} +- ${sd(xys).toFixed(4)}`);
console.log(`ratio 5.6.0 / 5.5.0:  |s| ${(b.r/mean(rs)).toFixed(3)}x   |s.xy| ${(b.xy/mean(xys)).toFixed(3)}x`);
