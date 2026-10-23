"use client";

import { useId, type CSSProperties, type ReactNode } from "react";

/**
 * A schematic of what each Generate recipe produces, drawn for the recipe
 * picker. Original line art rather than frames from the posts the recipes
 * were read from: those belong to other shops and creators. Colours come from
 * the theme, so the art follows light and dark mode. The product is always
 * drawn in the accent green, so every card answers "where does my product
 * go?" the same way. Video recipes carry a play mark in the corner.
 */

const PRODUCT: CSSProperties = { fill: "var(--green)", fillOpacity: 0.3, stroke: "var(--green)" };
const SOFT: CSSProperties = { fill: "currentColor", fillOpacity: 0.1 };
const SOLID: CSSProperties = { fill: "currentColor", fillOpacity: 0.55, stroke: "none" };
const MOTION: CSSProperties = { stroke: "var(--link)", strokeDasharray: "3 2" };

/** A top, centred on the origin, about 26 wide and 22 tall. */
const TOP = "M-4 -11 L-10 -8 L-13 -2 L-9 0 L-8 -3 L-8 11 L8 11 L8 -3 L9 0 L13 -2 L10 -8 L4 -11 Q0 -7 -4 -11 Z";
/** Trousers, centred on the origin. */
const PANTS = "M-7 -11 L7 -11 L8 12 L2 12 L0 -2 L-2 12 L-8 12 Z";
/** A headless mannequin torso on a pole, base at the origin. */
const TORSO = "M-7 -40 Q-9 -34 -7 -28 Q-9 -22 -6 -16 L6 -16 Q9 -22 7 -28 Q9 -34 7 -40 Q0 -43 -7 -40 Z";

function Top({ x, y, s = 1, style = PRODUCT }: { x: number; y: number; s?: number; style?: CSSProperties }) {
  return <path d={TOP} transform={`translate(${x} ${y}) scale(${s})`} style={style} />;
}

function Pants({ x, y, s = 1, style = PRODUCT }: { x: number; y: number; s?: number; style?: CSSProperties }) {
  return <path d={PANTS} transform={`translate(${x} ${y}) scale(${s})`} style={style} />;
}

/** A standing person, feet at (x, y). `outfit` paints the body as the product. */
function Person({ x, y, s = 1, outfit = true, hair = false }: {
  x: number; y: number; s?: number; outfit?: boolean; hair?: boolean;
}) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      {hair && <path d="M-5 -37 Q-6.5 -28 -6 -21 L6 -21 Q6.5 -28 5 -37 Q0 -40 -5 -37 Z" style={SOLID} />}
      {/* The face is opaque, so long hair shows only around and below it. */}
      <circle cx={0} cy={-33} r={4} style={{ fill: "var(--bg)" }} />
      <path d="M-6 -27 L6 -27 L7 -12 L-7 -12 Z" style={outfit ? PRODUCT : SOFT} />
      <path d="M-6 -26 L-9 -15 M6 -26 L9 -15" />
      <path d="M-4 -12 L-4 0 M4 -12 L4 0" />
    </g>
  );
}

function Mannequin({ x, y, s = 1, dressed = true }: { x: number; y: number; s?: number; dressed?: boolean }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      <path d={TORSO} style={SOFT} />
      {dressed && <path d="M-7 -38 L7 -38 L8 -26 L-8 -26 Z" style={PRODUCT} />}
      {dressed && <path d="M-6 -24 L6 -24 L7 -17 L-7 -17 Z" style={PRODUCT} />}
      <path d="M0 -16 L0 -3 M-7 0 L7 0" />
    </g>
  );
}

function Hanger({ x, y }: { x: number; y: number }) {
  return <path d={`M${x} ${y - 4} q2 -3 0 -4 M${x} ${y - 4} L${x - 9} ${y + 1} L${x + 9} ${y + 1} Z`} />;
}

/** A play mark, so a video recipe reads as video at a glance. */
function VideoMark() {
  return (
    <g>
      <circle cx={110} cy={10} r={6} style={{ fill: "var(--panel)", stroke: "currentColor" }} />
      <path d="M108 7 L113 10 L108 13 Z" style={{ fill: "currentColor", stroke: "none" }} />
    </g>
  );
}

/** A motion arrow. `m` is the id of the arrowhead marker in this picture. */
function Arrow({ d, m }: { d: string; m: string }) {
  return <path d={d} style={MOTION} markerEnd={`url(#${m})`} />;
}

const SCENES: Record<string, { video: boolean; art: (marker: string) => ReactNode }> = {
  bed_flat_lay: {
    video: false,
    art: () => (
      <>
        <rect x={26} y={10} width={68} height={64} rx={3} style={SOFT} />
        <path d="M26 10 L94 10" strokeWidth={3} />
        <rect x={32} y={14} width={24} height={9} rx={3} />
        <rect x={64} y={14} width={24} height={9} rx={3} />
        <rect x={40} y={25} width={40} height={5} rx={2} style={SOLID} />
        <Top x={60} y={50} s={1.1} />
        <rect x={8} y={14} width={13} height={13} rx={2} />
        <circle cx={14.5} cy={20.5} r={3} />
      </>
    ),
  },
  mannequin_transition: {
    video: true,
    art: (m) => (
      <>
        <path d="M6 74 L114 74" />
        <Mannequin x={22} y={74} s={0.9} />
        <Person x={58} y={74} outfit={false} hair />
        <Person x={94} y={74} hair />
        <Arrow m={m} d="M66 30 Q76 22 86 30" />
      </>
    ),
  },
  mirror_selfie: {
    video: true,
    art: () => (
      <>
        <rect x={34} y={6} width={52} height={70} rx={4} style={SOFT} />
        <Person x={60} y={72} s={1.3} hair />
        <rect x={54} y={21} width={11} height={15} rx={2} style={{ fill: "currentColor", stroke: "none" }} />
        <path d="M65 30 L69 33" />
      </>
    ),
  },
  mannequin_walkaround: {
    video: true,
    art: (m) => (
      <>
        <ellipse cx={60} cy={44} rx={44} ry={30} style={MOTION} />
        <Arrow m={m} d="M101 50 Q98 62 86 68" />
        <ellipse cx={60} cy={70} rx={10} ry={3} style={SOLID} />
        <Mannequin x={60} y={70} s={1.15} />
      </>
    ),
  },
  styled_flat_lay: {
    video: false,
    art: () => (
      <>
        <circle cx={60} cy={40} r={36} style={SOFT} />
        <Top x={54} y={30} s={1.1} />
        <Pants x={66} y={54} s={0.95} />
        <rect x={30} y={44} width={14} height={10} rx={2} style={SOLID} />
        <path d="M32 44 Q37 36 42 44" />
        <circle cx={78} cy={18} r={3} />
        <circle cx={86} cy={18} r={3} />
        <path d="M81 18 L83 18" />
        <ellipse cx={42} cy={64} rx={4} ry={2.5} />
        <ellipse cx={47} cy={68} rx={4} ry={2.5} />
      </>
    ),
  },
  flat_lay_reveal: {
    video: true,
    art: (m) => (
      <>
        <circle cx={60} cy={42} r={24} style={SOFT} />
        <Top x={56} y={36} s={0.8} />
        <Pants x={64} y={52} s={0.65} />
        <path d="M44 26 L44 22 L48 22 M76 22 L80 22 L80 26 M80 58 L80 62 L76 62 M48 62 L44 62 L44 58" style={MOTION} />
        <Arrow m={m} d="M42 20 L22 8" />
        <Arrow m={m} d="M82 64 L100 74" />
        <path d="M10 4 L10 12 M10 4 L18 4 M110 76 L110 68 M110 76 L102 76" />
      </>
    ),
  },
  sleepwear_lookbook: {
    video: false,
    art: () => (
      <>
        <rect x={4} y={8} width={34} height={64} rx={3} style={SOFT} />
        <path d="M8 16 L34 16" />
        <Hanger x={21} y={22} />
        <Top x={21} y={36} s={0.8} />
        <rect x={43} y={8} width={34} height={64} rx={3} style={SOFT} />
        <path d="M48 26 Q54 20 60 26 T72 26 M48 36 Q54 30 60 36 T72 36 M48 46 Q54 40 60 46 T72 46" style={{ stroke: "var(--green)" }} />
        <rect x={82} y={8} width={34} height={64} rx={3} style={SOFT} />
        <Person x={99} y={68} s={1.15} hair />
      </>
    ),
  },
  held_up_garment: {
    video: true,
    art: (m) => (
      <>
        <path d="M14 4 L14 76 M26 4 L26 76 M94 4 L94 76 M106 4 L106 76" style={{ strokeOpacity: 0.35 }} />
        <Top x={60} y={44} s={2.4} />
        <ellipse cx={44} cy={20} rx={4} ry={5} style={SOLID} />
        <ellipse cx={76} cy={20} rx={4} ry={5} style={SOLID} />
        <Arrow m={m} d="M88 58 Q96 46 88 34" />
      </>
    ),
  },
  hikaru_lookbook: {
    video: false,
    art: () => (
      <>
        <rect x={8} y={6} width={30} height={68} style={SOFT} />
        <path d="M14 14 L32 14 M14 24 L32 24 M14 34 L32 34" />
        <circle cx={96} cy={22} r={12} style={SOFT} />
        <path d="M96 34 L96 56" />
        <path d="M4 74 L116 74" />
        <Person x={62} y={74} s={1.6} hair />
      </>
    ),
  },
  character_editorial_carousel: {
    video: false,
    art: () => (
      <>
        <rect x={3} y={8} width={20} height={64} rx={3} style={SOFT} />
        <Person x={13} y={68} s={0.75} hair />
        <rect x={27} y={8} width={20} height={64} rx={3} style={SOFT} />
        <rect x={30} y={29} width={14} height={30} rx={2} />
        <Person x={37} y={68} s={0.64} hair />
        <rect x={51} y={8} width={20} height={64} rx={3} style={SOFT} />
        <Top x={61} y={36} s={0.55} />
        <path d="M55 58 Q61 51 67 58" />
        <rect x={75} y={8} width={20} height={64} rx={3} style={SOFT} />
        <Person x={85} y={68} s={0.68} hair />
        <path d="M81 42 L89 36" />
        <rect x={99} y={8} width={18} height={64} rx={3} style={SOFT} />
        <Person x={108} y={68} s={0.7} hair />
      </>
    ),
  },
  hikaru_walk: {
    video: true,
    art: (m) => (
      <>
        <path d="M52 30 L4 78 M68 30 L116 78" />
        <rect x={10} y={8} width={14} height={8} rx={1} style={{ stroke: "var(--link)" }} />
        <rect x={96} y={14} width={14} height={8} rx={1} style={{ stroke: "var(--link)" }} />
        <Person x={60} y={76} s={1.5} hair />
        <Arrow m={m} d="M84 46 L96 58" />
        <Arrow m={m} d="M36 46 L24 58" />
      </>
    ),
  },
  ceiling_fan_transition: {
    video: true,
    art: (m) => (
      <>
        <rect x={20} y={6} width={80} height={68} rx={2} style={SOFT} />
        <path d="M76 6 L70 74 M84 6 L78 74 M92 6 L86 74" style={{ strokeOpacity: 0.3 }} />
        <g transform="translate(60 40)">
          <circle cx={0} cy={-22} r={4} style={SOFT} />
          <path d="M-6 -16 L6 -16 L7 6 L-7 6 Z" style={PRODUCT} />
          <path d="M-6 -15 L-14 -22 M6 -15 L14 -22 M-4 6 L-4 24 M4 6 L4 24" />
        </g>
        <path d="M0 0 Q30 4 44 26 Q20 26 0 14 Z" style={SOLID} />
        <Arrow m={m} d="M50 30 Q58 40 54 52" />
      </>
    ),
  },
  hanger_rail_reveal: {
    video: true,
    art: (m) => (
      <>
        <path d="M14 12 L106 12" strokeWidth={2.5} />
        <path d="M18 12 L18 76 M102 12 L102 76" />
        <Hanger x={48} y={20} />
        <Top x={48} y={34} />
        <Hanger x={72} y={20} />
        <Pants x={72} y={36} />
        <Arrow m={m} d="M36 46 Q26 34 32 20" />
        <path d="M110 76 Q108 60 112 46 M110 76 Q114 62 116 52" style={{ strokeOpacity: 0.5 }} />
      </>
    ),
  },
};

/** What the generic drawing needs to know about a mined recipe. */
type MinedShape = {
  kind: string;
  card_count: number | null;
  cast: string;
  facets: { layout?: string[]; framing?: string[] };
};

/**
 * One drawing for every mined recipe, composed from its shape rather than
 * drawn per recipe: the library grows a batch at a time, and a picture each
 * would be the one part of a batch nobody could write from the data. The
 * layout decides the arrangement; the cast decides person or garment.
 */
function minedScene(shape: MinedShape): { video: boolean; art: (marker: string) => ReactNode } {
  const layout = shape.facets.layout ?? [];
  const person = shape.cast === "person";
  const flat = (shape.facets.framing ?? []).includes("flat_lay");
  const figure = (x: number, y: number, s: number) => (
    person
      ? <Person x={x} y={y} s={s} hair />
      : flat
        ? <Top x={x} y={y - 22 * s} s={s} />
        : <g><Hanger x={x} y={y - 40 * s} /><Top x={x} y={y - 26 * s} s={s} /></g>
  );
  if (shape.kind === "video") {
    return {
      video: true,
      art: (m) => (
        <>
          <rect x={38} y={4} width={44} height={72} rx={3} style={SOFT} />
          {figure(60, 70, 1.3)}
          <Arrow m={m} d="M88 56 Q98 40 88 24" />
        </>
      ),
    };
  }
  if (shape.kind === "image") {
    return {
      video: false,
      art: () => (
        <>
          <rect x={34} y={4} width={52} height={72} rx={3} style={SOFT} />
          {figure(60, 70, 1.4)}
        </>
      ),
    };
  }
  if (layout.includes("profile_mosaic")) {
    // Six tiles that read as one picture: the figure spans them.
    return {
      video: false,
      art: () => (
        <>
          {[0, 1, 2].map((col) => [0, 1].map((row) => (
            <rect key={`${col}-${row}`} x={24 + col * 24} y={6 + row * 34} width={23} height={33} rx={1} style={SOFT} />
          )))}
          {figure(60, 74, 1.7)}
        </>
      ),
    };
  }
  if (layout.includes("panorama")) {
    return {
      video: false,
      art: () => (
        <>
          <rect x={4} y={10} width={112} height={60} rx={3} style={SOFT} />
          <path d="M41 10 L41 70 M79 10 L79 70" style={{ strokeDasharray: "2 2" }} />
          {figure(30, 66, 1.05)}
          {figure(92, 66, 1.05)}
        </>
      ),
    };
  }
  const cards = Math.min(Math.max(shape.card_count ?? 3, 2), 5);
  const width = 110 / cards;
  return {
    video: false,
    art: () => (
      <>
        {Array.from({ length: cards }, (_, index) => {
          const x = 5 + index * width;
          // A detail-to-full sequence opens on the fabric; every other layout
          // shows the look on each card.
          const detail = layout.includes("detail_to_full") && index === 0;
          return (
            <g key={index}>
              <rect x={x} y={8} width={width - 4} height={64} rx={3} style={SOFT} />
              {detail
                ? <Top x={x + (width - 4) / 2} y={40} s={0.8} />
                : figure(x + (width - 4) / 2, 68, Math.min(0.8, width / 30))}
            </g>
          );
        })}
      </>
    ),
  };
}

export function RecipePreview({ recipe, mined }: { recipe: string; mined?: MinedShape | null }) {
  // Every card draws its own picture, so each needs its own marker id.
  const marker = `recipe-arrow-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const scene = SCENES[recipe] ?? (mined ? minedScene(mined) : undefined);
  if (!scene) return null;
  return (
    <svg
      viewBox="0 0 120 80"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <defs>
        <marker id={marker} viewBox="0 0 6 6" refX={5} refY={3} markerWidth={5} markerHeight={5} orient="auto">
          <path d="M0 0 L6 3 L0 6 Z" style={{ fill: "var(--link)", stroke: "none" }} />
        </marker>
      </defs>
      {scene.art(marker)}
      {scene.video && <VideoMark />}
    </svg>
  );
}
