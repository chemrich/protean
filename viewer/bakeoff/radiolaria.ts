/**
 * SP-01 Radiolaria, as a real Mol* representation.
 *
 * Each atom becomes a geodesic strut lattice rather than a solid sphere: take
 * an icosphere, keep only its edges, and draw each edge as a thin cylinder. You
 * can see straight through the molecule, which is the plan's P1 — the strongest
 * occlusion fix in its catalogue.
 *
 * The channel is B-factor rather than the plan's SASA, because `sasa()` is
 * Track B item 2 and is not built yet. Mobile atoms get thinner struts, so a
 * disordered loop reads as lace and an ordered core as basketwork.
 */
import { Sphere } from 'molstar/lib/mol-geo/primitive/sphere';
import { MeshBuilder } from 'molstar/lib/mol-geo/geometry/mesh/mesh-builder';
import { addSimpleCylinder } from 'molstar/lib/mol-geo/geometry/mesh/builder/cylinder';
import { Mesh } from 'molstar/lib/mol-geo/geometry/mesh/mesh';
import { ParamDefinition as PD } from 'molstar/lib/mol-util/param-definition';
import {
  UnitsMeshParams,
  UnitsMeshVisual,
} from 'molstar/lib/mol-repr/structure/units-visual';
import {
  ElementIterator,
  getElementLoci,
  eachElement,
} from 'molstar/lib/mol-repr/structure/visual/util/element';
import { StructureElement } from 'molstar/lib/mol-model/structure';
import { Vec3 } from 'molstar/lib/mol-math/linear-algebra';
import { Representation } from 'molstar/lib/mol-repr/representation';
import {
  UnitsRepresentation,
  StructureRepresentationStateBuilder,
  StructureRepresentationProvider,
} from 'molstar/lib/mol-repr/structure/representation';

/** Each undirected edge once. Triangles share edges, so a naive pass draws every strut three times. */
function edgesOf(indices: ArrayLike<number>): Array<[number, number]> {
  const seen = new Set<number>();
  const out: Array<[number, number]> = [];
  for (let i = 0; i < indices.length; i += 3) {
    const tri = [indices[i], indices[i + 1], indices[i + 2]];
    for (let e = 0; e < 3; e++) {
      const a = tri[e];
      const b = tri[(e + 1) % 3];
      const key = a < b ? a * 65536 + b : b * 65536 + a;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push([a, b]);
    }
  }
  return out;
}

/** B-factor over this unit, normalised, so porosity has a domain that fits the structure. */
function bRange(unit: any): [number, number] {
  const B = unit.model.atomicConformation.B_iso_or_equiv;
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < unit.elements.length; i++) {
    const b = B.value(unit.elements[i]);
    if (b < lo) lo = b;
    if (b > hi) hi = b;
  }
  return hi > lo ? [lo, hi] : [0, 1];
}

function createRadiolariaMesh(
  ctx: any, unit: any, structure: any, theme: any, props: any, mesh?: Mesh
): Mesh {
  const { detail, sizeFactor, strutRadius, porosityLow, porosityHigh, segments } = props;
  const shell = Sphere(detail);
  const edges = edgesOf(shell.indices);
  (window as any).__radio = {
    detail, segments, strutRadius,
    shellVertices: shell.vertices.length / 3,
    edges: edges.length,
    atoms: unit.elements.length,
  };
  const elements = unit.elements;
  const count = elements.length;

  // Deliberately generous: MeshBuilder grows its buffers, and an under-estimate
  // costs a reallocation per atom on a structure with thousands of them.
  const perAtom = edges.length * (segments + 1) * 2;
  const state = MeshBuilder.createState(count * perAtom, count * perAtom, mesh);

  // Called on the conformation, never detached. `invariantPosition` is a class
  // method reading `this._x` (mol-math/geometry/symmetry-operator.js), so
  // `const pos = unit.conformation.invariantPosition` — which is what the
  // plan's own §4.1 skeleton writes — throws
  // "Cannot read properties of undefined (reading '_x')" on the first atom.
  const conformation = unit.conformation;
  const l = StructureElement.Location.create(structure);
  l.unit = unit;

  const B = unit.model.atomicConformation.B_iso_or_equiv;
  const [bLo, bHi] = bRange(unit);

  const centre = Vec3();
  const a = Vec3();
  const b = Vec3();

  for (let i = 0; i < count; i++) {
    const ei = elements[i];
    l.element = ei;
    conformation.invariantPosition(ei, centre);
    const radius = theme.size.size(l) * sizeFactor;

    // The binding. Porosity rises with B-factor, and a thinner strut is what
    // "more porous" means for a lattice.
    const t = (B.value(ei) - bLo) / (bHi - bLo);
    const porosity = porosityLow + (porosityHigh - porosityLow) * t;
    const r = strutRadius * (1 - porosity) * radius;

    // Picking, highlighting and every per-atom colour theme run off this. The
    // plan calls losing it an automatic reject at review, and it is one line.
    state.currentGroup = i;

    for (const [p, q] of edges) {
      Vec3.set(a, shell.vertices[3 * p], shell.vertices[3 * p + 1], shell.vertices[3 * p + 2]);
      Vec3.set(b, shell.vertices[3 * q], shell.vertices[3 * q + 1], shell.vertices[3 * q + 2]);
      Vec3.scaleAndAdd(a, centre, a, radius);
      Vec3.scaleAndAdd(b, centre, b, radius);
      addSimpleCylinder(state, a, b, {
        radiusTop: r,
        radiusBottom: r,
        radialSegments: segments,
        topCap: false,
        bottomCap: false,
      });
    }
  }
  const built = MeshBuilder.getMesh(state);
  (window as any).__radio.meshVertices = built.vertexCount;
  (window as any).__radio.perAtom = Math.round(built.vertexCount / Math.max(1, count));
  return built;
}

export const RadiolariaParams = {
  ...UnitsMeshParams,
  sizeFactor: PD.Numeric(1, { min: 0.1, max: 3, step: 0.05 }),
  detail: PD.Numeric(1, { min: 0, max: 3, step: 1 }),
  strutRadius: PD.Numeric(0.22, { min: 0.01, max: 0.6, step: 0.01 }),
  porosityLow: PD.Numeric(0.1, { min: 0, max: 0.95, step: 0.01 }),
  porosityHigh: PD.Numeric(0.75, { min: 0, max: 0.95, step: 0.01 }),
  segments: PD.Numeric(4, { min: 3, max: 12, step: 1 }),
};

function RadiolariaVisual(materialId: number) {
  return UnitsMeshVisual(
    {
      defaultProps: PD.getDefaultValues(RadiolariaParams),
      createGeometry: createRadiolariaMesh,
      createLocationIterator: ElementIterator.fromGroup,
      getLoci: getElementLoci,
      eachLocation: eachElement,
      setUpdateState: (state: any, next: any, current: any) => {
        state.createGeometry =
          next.detail !== current.detail ||
          next.sizeFactor !== current.sizeFactor ||
          next.strutRadius !== current.strutRadius ||
          next.porosityLow !== current.porosityLow ||
          next.porosityHigh !== current.porosityHigh ||
          next.segments !== current.segments;
      },
    } as any,
    materialId
  );
}

const Visuals = {
  radiolaria: (ctx: any, getParams: any) =>
    UnitsRepresentation('Radiolaria', ctx, getParams, RadiolariaVisual),
};

export const RadiolariaRepresentationProvider = StructureRepresentationProvider({
  name: 'radiolaria',
  label: 'Radiolaria',
  description: 'A porous geodesic lattice per atom; porosity carries a per-atom scalar.',
  factory: (ctx: any, getParams: any) =>
    Representation.createMulti(
      'Radiolaria', ctx, getParams, StructureRepresentationStateBuilder, Visuals as any
    ),
  getParams: () => ({ ...RadiolariaParams, visuals: PD.MultiSelect(['radiolaria'], PD.objectToOptions(Visuals)) }),
  defaultValues: { ...PD.getDefaultValues(RadiolariaParams), visuals: ['radiolaria'] },
  defaultColorTheme: { name: 'element-symbol' },
  defaultSizeTheme: { name: 'physical' },
  isApplicable: (structure: any) => structure.elementCount > 0,
} as any);
