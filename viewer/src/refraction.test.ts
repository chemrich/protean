import { describe, expect, it } from 'vitest';
import {
  beerLambertAbsorption,
  gaussianWeights,
  refractionState,
  schlickFresnel,
  screenSpaceDitherAngle,
  setRefraction,
  snellRefractionOffset,
  spectralDispersionOffsets,
  vogelSpiralKernel,
} from './refraction';

describe('refraction optics and mathematical physics', () => {
  describe('Snell refraction offset', () => {
    it('produces zero deflection at perpendicular normal incidence', () => {
      const viewDir: [number, number, number] = [0, 0, 1];
      const normal: [number, number, number] = [0, 0, 1];
      const offset = snellRefractionOffset(viewDir, normal, 10.0, 1.5, 800, 600, 0.08);

      expect(Math.abs(offset[0])).toBeCloseTo(0, 5);
      expect(Math.abs(offset[1])).toBeCloseTo(0, 5);
    });

    it('deflects lateral screen coordinates on curved or tilted surfaces', () => {
      const viewDir: [number, number, number] = [0, 0, 1];
      const normal: [number, number, number] = [0.7071, 0, 0.7071];
      const offset = snellRefractionOffset(viewDir, normal, 10.0, 1.5, 800, 600, 0.08);

      expect(Math.abs(offset[0])).toBeGreaterThan(0.001);
    });

    it('scales inversely with perspective depth distance', () => {
      const viewDir: [number, number, number] = [0, 0, 1];
      const normal: [number, number, number] = [0.5, 0.5, 0.7071];
      const offsetNear = snellRefractionOffset(viewDir, normal, 5.0, 1.5, 800, 800, 0.08);
      const offsetFar = snellRefractionOffset(viewDir, normal, 20.0, 1.5, 800, 800, 0.08);

      expect(Math.abs(offsetNear[0])).toBeGreaterThan(Math.abs(offsetFar[0]));
      expect(Math.abs(offsetNear[0]) / Math.abs(offsetFar[0])).toBeCloseTo(4.0, 1);
    });

    it('corrects for viewport aspect ratio', () => {
      const viewDir: [number, number, number] = [0, 0, 1];
      const normal: [number, number, number] = [0.5, 0.5, 0.7071];
      const offsetSquare = snellRefractionOffset(viewDir, normal, 10.0, 1.5, 800, 800, 0.08);
      const offsetWide = snellRefractionOffset(viewDir, normal, 10.0, 1.5, 1600, 800, 0.08);

      expect(offsetWide[1]).toBeCloseTo(offsetSquare[1] * 2.0, 4);
    });

    it('falls back to reflection on Total Internal Reflection (TIR)', () => {
      const viewDir: [number, number, number] = [0.99, 0, 0.141];
      const normal: [number, number, number] = [0, 0, 1];
      // Simulate incident ray where k < 0
      const offset = snellRefractionOffset(viewDir, normal, 10.0, 0.5, 800, 600, 0.08);
      expect(Number.isFinite(offset[0])).toBe(true);
      expect(Number.isFinite(offset[1])).toBe(true);
    });
  });

  describe('dielectric Schlick Fresnel factor', () => {
    it('returns F0 = 0.04 at normal incidence', () => {
      const viewDir: [number, number, number] = [0, 0, 1];
      const normal: [number, number, number] = [0, 0, 1];
      const f = schlickFresnel(viewDir, normal, 0.04);

      expect(f).toBeCloseTo(0.04, 3);
    });

    it('returns 1.0 at grazing angles', () => {
      const viewDir: [number, number, number] = [0, 0, 1];
      const normal: [number, number, number] = [1, 0, 0];
      const f = schlickFresnel(viewDir, normal, 0.04);

      expect(f).toBeCloseTo(1.0, 3);
    });

    it('monotonically increases from normal incidence to grazing angle', () => {
      const viewDir: [number, number, number] = [0, 0, 1];
      const angles = [0, 0.2, 0.4, 0.6, 0.8, 1.0];
      const fresnels = angles.map((cosTheta) => {
        const sinTheta = Math.sqrt(1 - cosTheta * cosTheta);
        const normal: [number, number, number] = [sinTheta, 0, cosTheta];
        return schlickFresnel(viewDir, normal, 0.04);
      });

      for (let i = 1; i < fresnels.length; i++) {
        expect(fresnels[i]).toBeLessThanOrEqual(fresnels[i - 1] + 1e-4);
      }
      expect(fresnels[0]).toBeCloseTo(1.0, 2);
      expect(fresnels[fresnels.length - 1]).toBeCloseTo(0.04, 2);
    });
  });

  describe('3-tap spectral chromatic dispersion', () => {
    it('generates distinct wavelength sampling offsets for R, G, B channels', () => {
      const baseOffset: [number, number] = [0.05, 0.03];
      const dispersion = 0.02;
      const taps = spectralDispersionOffsets(baseOffset, dispersion);

      expect(taps.g).toEqual(baseOffset);
      expect(taps.r[0]).toBeCloseTo(baseOffset[0] * 0.98, 5);
      expect(taps.b[0]).toBeCloseTo(baseOffset[0] * 1.02, 5);
      expect(taps.r[1]).toBeCloseTo(baseOffset[1] * 0.98, 5);
      expect(taps.b[1]).toBeCloseTo(baseOffset[1] * 1.02, 5);
    });
  });

  describe('12-tap Vogel Golden Angle spiral kernel', () => {
    it('generates exactly 12 taps within the unit disc', () => {
      const kernel = vogelSpiralKernel(12);
      expect(kernel.length).toBe(12);

      for (const [x, y] of kernel) {
        const radius = Math.hypot(x, y);
        expect(radius).toBeLessThanOrEqual(1.0001);
        expect(radius).toBeGreaterThan(0.0);
      }
    });

    it('follows the Golden Angle distribution (~2.39996 rad)', () => {
      const kernel = vogelSpiralKernel(12);
      const GOLDEN_ANGLE = Math.PI * (3.0 - Math.sqrt(5.0));

      for (let i = 0; i < 12; i++) {
        const [x, y] = kernel[i];
        const angle = Math.atan2(y, x);
        const expectedAngle = (i * GOLDEN_ANGLE) % (2 * Math.PI);
        const diff = Math.abs(Math.sin(angle - expectedAngle));
        expect(diff).toBeCloseTo(0, 4);
      }
    });

    it('computes Gaussian attenuation weights decreasing from center', () => {
      const weights = gaussianWeights(12, 0.707);
      expect(weights.length).toBe(12);

      expect(weights[0]).toBeGreaterThan(weights[weights.length - 1]);
      for (const w of weights) {
        expect(w).toBeGreaterThan(0);
        expect(w).toBeLessThanOrEqual(1.0);
      }
    });
  });

  describe('Beer-Lambert absorption tinting', () => {
    it('deepens absorption tint as optical path length increases at silhouette edges', () => {
      const baseColor: [number, number, number] = [0.45, 0.73, 0.63]; // seafoam green
      const viewDir: [number, number, number] = [0, 0, 1];
      const normalCenter: [number, number, number] = [0, 0, 1];
      const normalEdge: [number, number, number] = [0.95, 0, 0.31];

      const tintCenter = beerLambertAbsorption(baseColor, normalCenter, viewDir, 0.75);
      const tintEdge = beerLambertAbsorption(baseColor, normalEdge, viewDir, 0.75);

      // Edge has longer optical path length -> more absorption (lower RGB values)
      expect(tintEdge[0]).toBeLessThan(tintCenter[0]);
      expect(tintEdge[1]).toBeLessThan(tintCenter[1]);
      expect(tintEdge[2]).toBeLessThan(tintCenter[2]);
    });
  });

  describe('screen-space dither hash', () => {
    it('produces pseudo-random rotation angles in [0, 2pi)', () => {
      const angle1 = screenSpaceDitherAngle(100, 200);
      const angle2 = screenSpaceDitherAngle(101, 200);
      const angle3 = screenSpaceDitherAngle(100, 201);

      expect(angle1).toBeGreaterThanOrEqual(0);
      expect(angle1).toBeLessThan(2 * Math.PI);
      expect(angle2).toBeGreaterThanOrEqual(0);
      expect(angle2).toBeLessThan(2 * Math.PI);
      expect(angle3).toBeGreaterThanOrEqual(0);
      expect(angle3).toBeLessThan(2 * Math.PI);

      expect(angle1).not.toEqual(angle2);
      expect(angle1).not.toEqual(angle3);
    });
  });

  describe('refraction settings state management', () => {
    it('maintains default glass parameters and updates on request', () => {
      const state = refractionState();
      expect(state.ior).toBe(1.50);
      expect(state.fresnelF0).toBe(0.04);

      setRefraction({ roughness: 0.7, bumpiness: 0.45, bumpFrequency: 4.0 });
      const updated = refractionState();
      expect(updated.roughness).toBe(0.7);
      expect(updated.bumpiness).toBe(0.45);
      expect(updated.bumpFrequency).toBe(4.0);
    });
  });
});
