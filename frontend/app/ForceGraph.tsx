"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export interface FGNode {
  id: string;
  type?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
}
export interface FGEdge {
  source: string;
  target: string;
}
export interface NodeVisual {
  color: string;
  r: number;
  square?: boolean;
  label?: string | null; // text to show under/over the node (null = only on hover)
}

interface Props<N extends FGNode> {
  nodes: N[];
  edges: FGEdge[];
  visual: (n: N) => NodeVisual;
  card?: (n: N) => ReactNode;
  header?: ReactNode;
  legend?: ReactNode;
  charge?: number; // repulsion strength
  linkDist?: number;
  heavyTypes?: string[]; // node types that act as anchors (less movement)
}

const W = 1040;
const H = 720;
type Sim<N> = N & { x: number; y: number; vx: number; vy: number };

export default function ForceGraph<N extends FGNode>({
  nodes,
  edges,
  visual,
  card,
  header,
  legend,
  charge = 1800,
  linkDist = 70,
  heavyTypes = ["site", "company"],
}: Props<N>) {
  const [, force] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const [reset, setReset] = useState(0);
  const simRef = useRef<Sim<N>[]>([]);
  const viewRef = useRef({ tx: 0, ty: 0, k: 1 });
  const dragRef = useRef<{ id: string | null; panning: boolean; lx: number; ly: number; moved: boolean }>({
    id: null,
    panning: false,
    lx: 0,
    ly: 0,
    moved: false,
  });
  const svgRef = useRef<SVGSVGElement | null>(null);

  // Stable across re-renders: callers pass a fresh array literal (or the default
  // one is recreated every render), which would otherwise make the physics effect
  // below re-run on every frame and reset the pan/zoom view + node layout.
  const heavyKey = heavyTypes.join("|");
  const heavySet = useMemo(() => new Set(heavyTypes), [heavyKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    simRef.current = nodes.map((n, i) => {
      const ang = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      const rr = heavySet.has(n.type ?? "") ? 80 : 240;
      return { ...n, x: W / 2 + Math.cos(ang) * rr, y: H / 2 + Math.sin(ang) * rr, vx: 0, vy: 0 };
    });
    viewRef.current = { tx: 0, ty: 0, k: 1 };
    const byId = new Map(simRef.current.map((n) => [n.id, n]));
    let raf = 0;
    const tick = () => {
      const ns = simRef.current;
      const drag = dragRef.current;
      for (let i = 0; i < ns.length; i++) {
        for (let j = i + 1; j < ns.length; j++) {
          const a = ns[i];
          const b = ns[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          const d2 = dx * dx + dy * dy || 0.01;
          const d = Math.sqrt(d2);
          const rep = charge / d2;
          const fx = (dx / d) * rep;
          const fy = (dy / d) * rep;
          if (drag.id !== a.id) {
            a.vx += fx;
            a.vy += fy;
          }
          if (drag.id !== b.id) {
            b.vx -= fx;
            b.vy -= fy;
          }
        }
      }
      for (const e of edges) {
        const a = byId.get(e.source);
        const b = byId.get(e.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const k = 0.02 * (d - linkDist);
        const fx = (dx / d) * k;
        const fy = (dy / d) * k;
        if (drag.id !== a.id) {
          a.vx += fx;
          a.vy += fy;
        }
        if (drag.id !== b.id) {
          b.vx -= fx;
          b.vy -= fy;
        }
      }
      let moved = 0;
      for (const n of ns) {
        if (drag.id === n.id) {
          n.vx = 0;
          n.vy = 0;
          continue;
        }
        n.vx += (W / 2 - n.x) * 0.0015;
        n.vy += (H / 2 - n.y) * 0.0015;
        const damp = heavySet.has(n.type ?? "") ? 0.8 : 0.87;
        n.vx *= damp;
        n.vy *= damp;
        n.x += n.vx;
        n.y += n.vy;
        moved += Math.abs(n.vx) + Math.abs(n.vy);
      }
      // Only re-render when something actually changed (saves idle CPU).
      if (moved > 0.4 || drag.id || drag.panning) force((f) => f + 1);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [nodes, edges, charge, linkDist, reset, heavySet]);

  const neighbors = useMemo(() => {
    const set = new Set<string>();
    if (hover) {
      set.add(hover);
      for (const e of edges) {
        if (e.source === hover) set.add(e.target);
        if (e.target === hover) set.add(e.source);
      }
    }
    return set;
  }, [hover, edges]);

  // screen px -> graph coords (account for viewBox CTM + pan/zoom transform)
  const toGraph = (cx: number, cy: number) => {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = cx;
    pt.y = cy;
    const vb = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    const { tx, ty, k } = viewRef.current;
    return { x: (vb.x - tx) / k, y: (vb.y - ty) / k, vbx: vb.x, vby: vb.y };
  };

  const onPointerDownBg = (e: React.PointerEvent) => {
    dragRef.current.panning = true;
    dragRef.current.lx = e.clientX;
    dragRef.current.ly = e.clientY;
  };
  const onPointerDownNode = (id: string) => (e: React.PointerEvent) => {
    e.stopPropagation();
    // Engage the drag first; pointer capture is a best-effort nicety that can
    // throw (e.g. synthetic events, some devices) and must never block dragging.
    dragRef.current.id = id;
    dragRef.current.moved = false;
    try {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    } catch {
      /* ignore */
    }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (drag.id) {
      const g = toGraph(e.clientX, e.clientY);
      const n = simRef.current.find((x) => x.id === drag.id);
      if (n) {
        n.x = g.x;
        n.y = g.y;
        n.vx = 0;
        n.vy = 0;
        drag.moved = true;
      }
    } else if (drag.panning) {
      const svg = svgRef.current!;
      const m = svg.getScreenCTM()!;
      viewRef.current.tx += (e.clientX - drag.lx) / m.a;
      viewRef.current.ty += (e.clientY - drag.ly) / m.d;
      drag.lx = e.clientX;
      drag.ly = e.clientY;
      force((f) => f + 1);
    }
  };
  const onPointerUp = () => {
    dragRef.current.id = null;
    dragRef.current.panning = false;
  };
  const onWheel = (e: React.WheelEvent) => {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const vb = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    const { tx, ty, k } = viewRef.current;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const nk = Math.max(0.3, Math.min(5, k * factor));
    viewRef.current.tx = vb.x - nk * ((vb.x - tx) / k);
    viewRef.current.ty = vb.y - nk * ((vb.y - ty) / k);
    viewRef.current.k = nk;
    force((f) => f + 1);
  };

  const hovered = simRef.current.find((n) => n.id === hover) || null;
  const byId = new Map(simRef.current.map((n) => [n.id, n]));
  const { tx, ty, k } = viewRef.current;

  return (
    <div className="graph-wrap">
      <div className="graph-head">
        {header}
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          {legend}
          <button className="graph-reset" onClick={() => setReset((r) => r + 1)} title="Reset view">
            ⟳ Reset
          </button>
        </div>
      </div>

      <div className="graph-canvas">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid meet"
          onPointerDown={onPointerDownBg}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
          onWheel={onWheel}
          style={{ cursor: dragRef.current.panning ? "grabbing" : "grab", touchAction: "none" }}
        >
          <g transform={`translate(${tx},${ty}) scale(${k})`}>
            {edges.map((e, i) => {
              const a = byId.get(e.source);
              const b = byId.get(e.target);
              if (!a || !b) return null;
              const on = !hover || (neighbors.has(e.source) && neighbors.has(e.target));
              return (
                <line
                  key={i}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={on ? "var(--accent)" : "var(--line)"}
                  strokeOpacity={hover ? (on ? 0.7 : 0.12) : 0.35}
                  strokeWidth={on && hover ? 1.5 : 1}
                />
              );
            })}
            {simRef.current.map((n) => {
              const v = visual(n);
              const dim = hover && !neighbors.has(n.id);
              const showLabel = v.label != null || hover === n.id;
              const labelText = v.label ?? n.name ?? "";
              return (
                <g
                  key={n.id}
                  opacity={dim ? 0.22 : 1}
                  onPointerDown={onPointerDownNode(n.id)}
                  onMouseEnter={() => setHover(n.id)}
                  onMouseLeave={() => setHover(null)}
                  style={{ cursor: "grab" }}
                >
                  {v.square ? (
                    <rect
                      x={n.x - v.r}
                      y={n.y - v.r}
                      width={v.r * 2}
                      height={v.r * 2}
                      rx={3}
                      fill={v.color}
                      stroke="var(--accent)"
                      strokeWidth={1.4}
                    />
                  ) : (
                    <circle cx={n.x} cy={n.y} r={v.r} fill={v.color} stroke="var(--surface)" strokeWidth={1.4} />
                  )}
                  {showLabel && (
                    <text
                      x={n.x}
                      y={n.y - v.r - 5}
                      textAnchor="middle"
                      className={`g-label ${heavySet.has(n.type ?? "") ? "g-company" : ""}`}
                    >
                      {String(labelText).slice(0, 22)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>

        <div className="graph-hint">drag nodes · drag canvas to pan · scroll to zoom</div>
        {hovered && card && <div className="graph-card">{card(hovered)}</div>}
      </div>
    </div>
  );
}
