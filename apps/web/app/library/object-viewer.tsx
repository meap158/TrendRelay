"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { drawable, nudge, orbitFrom, rotation } from "../../lib/object-orbit";
import type { Mesh, Orbit } from "../../lib/object-orbit";
import { useT } from "../i18n-provider";

/**
 * Turning a solid object to decide how it should be worn.
 *
 * The picker already answers "what is this object" with a thumbnail and "does
 * it sit right on this face" with a real rendered frame. Neither answers the
 * question a prop with depth raises, which is *which way should it face* - and
 * that one cannot be answered by looking, only by turning it.
 *
 * So this is a control, not a preview. It writes the effect's own `turn` and
 * `tilt`, and the authoritative picture stays the server-rendered frame beside
 * it: that is what the picker has always shown, and a viewport that claimed to
 * be the output would be a second renderer to keep in step. What this must get
 * right is narrower - the same geometry and the same light - so that the thing
 * being aimed looks like the thing being placed.
 *
 * Raw WebGL rather than a library. The whole job is one draw call over a few
 * hundred triangles with a fixed light; a scene graph, a loader stack and an
 * animation system are not much use to it, and this app carries seven runtime
 * dependencies in total. The geometry is not duplicated either - it is fetched
 * from the API, which serves the same declaration the renderer draws from.
 */

const VERTEX_SHADER = `
attribute vec3 position;
attribute vec3 normal;
attribute vec3 colour;
uniform mat3 rotation;
varying vec3 shaded;
uniform vec3 light;
uniform float ambient;
void main() {
  vec3 turned = rotation * position;
  vec3 facing = normalize(rotation * normal);
  // Pointed back at the camera rather than trusted to be, exactly as the
  // renderer does: which way a cross product faces depends on how the triangle
  // was wound, and a primitive wound the other way lights from underneath
  // while drawing perfectly.
  if (facing.z < 0.0) facing = -facing;
  float lit = max(dot(facing, normalize(light)), 0.0);
  shaded = colour * (ambient + (1.0 - ambient) * lit);
  // Orthographic, as the renderer is. The unit cube fills the viewport; z is
  // mapped into clip space only so the depth test has something to compare.
  gl_Position = vec4(turned.x * 2.0, turned.y * 2.0, -turned.z, 1.0);
}
`;

const FRAGMENT_SHADER = `
precision mediump float;
varying vec3 shaded;
void main() { gl_FragColor = vec4(shaded, 1.0); }
`;

function compile(gl: WebGLRenderingContext, kind: number, source: string) {
  const shader = gl.createShader(kind);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function program(gl: WebGLRenderingContext) {
  const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
  if (!vertex || !fragment) return null;
  const built = gl.createProgram();
  if (!built) return null;
  gl.attachShader(built, vertex);
  gl.attachShader(built, fragment);
  gl.linkProgram(built);
  if (!gl.getProgramParameter(built, gl.LINK_STATUS)) return null;
  return built;
}

type Loaded = Mesh & { id: string; light: number[]; ambient: number };

export function ObjectViewer({
  base,
  overlayId,
  turn,
  tilt,
  disabled,
  apiFetch,
  onTurn,
  onCommit,
}: {
  base: string;
  overlayId: string;
  turn: number;
  tilt: number;
  disabled?: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  /** While the drag is happening: cheap, local, no render asked for. */
  onTurn: (orbit: Orbit) => void;
  /** On release: worth re-rendering the frame beside it for. */
  onCommit: (orbit: Orbit) => void;
}) {
  const t = useT();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mesh, setMesh] = useState<Loaded | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const drag = useRef({ id: -1, x: 0, y: 0, from: { turn: 0, tilt: 0 } });

  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    apiFetch(`${base}/face-overlay/objects/${overlayId}/mesh`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("no mesh");
        const body = (await response.json()) as Loaded;
        if (live) setMesh(body);
      })
      .catch(() => {
        // A flat object answers 404 here and simply has no viewport. Nothing
        // is reported: the absence is the message, and an error beside a
        // sticker would be reporting that a sticker is not a solid.
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [apiFetch, base, overlayId]);

  // The angle arrives as an argument rather than through a ref, so this is not
  // rebuilt on every degree of a drag and nothing is written during a render.
  const draw = useCallback((at: Orbit) => {
    const canvas = canvasRef.current;
    if (!canvas || !mesh) return;
    const gl = canvas.getContext("webgl", { antialias: true, alpha: true });
    if (!gl) {
      setUnsupported(true);
      return;
    }
    const ready = program(gl);
    if (!ready) {
      setUnsupported(true);
      return;
    }
    // Drawn at the device's own resolution, or the facets come out stepped on
    // exactly the screens most likely to be looking closely.
    const density = Math.min(window.devicePixelRatio || 1, 2);
    const size = Math.max(canvas.clientWidth, 1);
    canvas.width = Math.round(size * density);
    canvas.height = Math.round(size * density);

    const shape = drawable(mesh);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.enable(gl.DEPTH_TEST);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(ready);

    for (const [name, data] of [
      ["position", shape.positions],
      ["normal", shape.normals],
      ["colour", shape.colours],
    ] as const) {
      const buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const slot = gl.getAttribLocation(ready, name);
      gl.enableVertexAttribArray(slot);
      gl.vertexAttribPointer(slot, 3, gl.FLOAT, false, 0, 0);
    }
    gl.uniformMatrix3fv(
      gl.getUniformLocation(ready, "rotation"),
      false,
      new Float32Array(rotation(at.turn, at.tilt)),
    );
    gl.uniform3fv(gl.getUniformLocation(ready, "light"), new Float32Array(mesh.light));
    gl.uniform1f(gl.getUniformLocation(ready, "ambient"), mesh.ambient);
    gl.drawArrays(gl.TRIANGLES, 0, shape.count);
  }, [mesh]);

  useEffect(() => {
    draw({ turn, tilt });
  }, [draw, turn, tilt]);

  useEffect(() => {
    const onResize = () => draw({ turn, tilt });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [draw, turn, tilt]);

  const size = () => {
    const canvas = canvasRef.current;
    return { width: canvas?.clientWidth ?? 1, height: canvas?.clientHeight ?? 1 };
  };

  // Compared rather than cleared when the choice changes: clearing it would be
  // a setState in an effect, and one render showing the previous object is
  // exactly what that would be avoiding.
  if (mesh?.id !== overlayId || unsupported) {
    return unsupported ? (
      <p className="overlay-note">{t("overlayPicker.noWebgl")}</p>
    ) : null;
  }

  return (
    <div className="object-viewer">
      <canvas
        ref={canvasRef}
        role="slider"
        tabIndex={disabled ? -1 : 0}
        aria-label={t("overlayPicker.turnObject")}
        aria-valuenow={Math.round(turn)}
        aria-valuemin={-70}
        aria-valuemax={70}
        aria-valuetext={t("overlayPicker.turnedTo")
          .replace("{turn}", String(Math.round(turn)))
          .replace("{tilt}", String(Math.round(tilt)))}
        aria-disabled={disabled || undefined}
        onPointerDown={(event) => {
          if (disabled || event.button !== 0) return;
          drag.current = {
            id: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            from: { turn, tilt },
          };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (drag.current.id !== event.pointerId) return;
          onTurn(
            orbitFrom(
              drag.current.from,
              event.clientX - drag.current.x,
              event.clientY - drag.current.y,
              size(),
            ),
          );
        }}
        onPointerUp={(event) => {
          if (drag.current.id !== event.pointerId) return;
          drag.current.id = -1;
          // The frame beside it costs a decode and a render, so it is asked for
          // once the angle has been chosen rather than at every degree on the
          // way to it.
          onCommit({ turn, tilt });
        }}
        onPointerCancel={() => { drag.current.id = -1; }}
        onKeyDown={(event) => {
          if (disabled) return;
          const moved = nudge({ turn, tilt }, event.key);
          if (!moved) return;
          event.preventDefault();
          onCommit(moved);
        }}
      />
      <p className="object-viewer-hint">{t("overlayPicker.dragToTurn")}</p>
    </div>
  );
}
