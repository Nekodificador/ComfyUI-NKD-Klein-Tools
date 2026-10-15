// NKD Klein Box Editor — draw FLUX.3 Klein bounding boxes (modal editor).
// Vanilla JS (this pack has no build step). State lives in the node's hidden `boxes_json`:
//   { elements: [{ id, kind: "new"|"keep"|"move"|"remove", ref: int|null, desc,
//                  src: [top,left,bottom,right]|null, tgt: [...]|null }] }
// Boxes are on FLUX.3's 0..1000 grid, each measured on ITS OWN image. `src` is drawn on
// the reference it comes from (ref view), `tgt` on the output canvas (target view).
// Python (box_prompt.py) turns the state into the prompt; nothing is computed here, so the
// editor and the typed Box Prompt cannot drift apart.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { openNkdModal, nkdButton, nkdSelect } from "./nkd_modal.js";

const NODE_NAME = "NKDKleinBoxEditor";
const MIN_SIZE = 8;      // smaller drags are treated as a click, not a box
const HANDLE = 7;        // px

const KINDS = [
    { value: "new", label: "New — add here" },
    { value: "move", label: "Move" },
    { value: "keep", label: "Keep in place" },
    { value: "remove", label: "Remove" },
];
const color = (i, a = 1) => `hsla(${(i * 137.5 + 300) % 360}, 80%, 62%, ${a})`;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const ready = (im) => im?.complete && im.naturalWidth > 0;

// Which box an element draws in which view: tgt on the canvas, src on its reference.
const usesTgt = (el) => el.kind === "new" || el.kind === "move";
const usesSrc = (el) => el.kind !== "new";
const complete = (el) => {
    if (el.kind === "new") return !!el.tgt;
    if (el.ref == null || !el.src) return false;
    return el.kind !== "move" || !!el.tgt;
};

function setup(node) {
    const widget = (name) => node.widgets?.find((w) => w.name === name);
    const cfg = widget("boxes_json");
    if (!cfg) return;
    cfg.type = "hidden";
    cfg.computeSize = () => [0, -4];
    cfg.hidden = true;
    if (cfg.options) cfg.options.hidden = true;

    const read = () => {
        let s = {};
        try { s = JSON.parse(cfg.value || "{}"); } catch { /* fresh */ }
        const els = Array.isArray(s.elements) ? s.elements : [];
        return {
            elements: els.map((e) => ({
                id: String(e.id || ""), kind: e.kind || "new", ref: e.ref ?? null,
                desc: e.desc || "", src: e.src || null, tgt: e.tgt || null,
            })),
        };
    };
    const write = (state) => {
        cfg.value = JSON.stringify(state);
        cfg.callback?.(cfg.value);
        refreshBtn();
    };
    const summary = () => {
        const n = read().elements.length;
        return n ? `✎ Edit boxes (${n})` : "✎ Draw boxes";
    };
    const btn = node.addWidget("button", summary(), null, () => openEditor());
    btn.serialize = false;
    btn.options = { ...(btn.options || {}), serialize: false };
    const refreshBtn = () => { btn.name = btn.label = summary(); node.setDirtyCanvas(true, true); };

    // ── Image behind the boxes: the upstream node's preview, best first ─────────────
    const fileCache = new Map();
    function widgetFile(n) {                       // Load Image-style: its file is exact
        const v = n?.widgets?.find((w) => w.name === "image")?.value;
        if (typeof v !== "string" || !v || v.includes("://")) return null;
        const clean = v.replace(/ \[(input|output|temp)\]$/, "");
        const type = (v.match(/ \[(input|output|temp)\]$/) || [])[1] || "input";
        const cut = clean.lastIndexOf("/");
        const url = api.apiURL(`/view?filename=${encodeURIComponent(clean.slice(cut + 1))}&type=${type}&subfolder=${encodeURIComponent(cut > 0 ? clean.slice(0, cut) : "")}`);
        if (!fileCache.has(url)) {
            const im = new Image();
            im.onload = () => editor?.redraw();
            im.src = url;
            fileCache.set(url, im);
        }
        return fileCache.get(url);
    }
    const slotOf = (ref) => node.inputs?.findIndex((i) => i.name === `ref_images.ref_${ref}`) ?? -1;
    const linkedRef = (ref) => { const s = slotOf(ref); return s >= 0 && node.inputs[s].link != null; };
    function refImage(ref) {
        const slot = slotOf(ref);
        if (slot < 0 || node.inputs[slot].link == null) return null;
        const up = node.getInputNode?.(slot);
        if (ready(up?.imgs?.[0])) return up.imgs[0];
        const file = widgetFile(up);
        if (ready(file)) return file;
        const seen = new Set([node.id]);                     // nearest ancestor with a preview
        let frontier = [up].filter(Boolean);
        for (let depth = 0; depth < 20 && frontier.length; depth++) {
            const next = [];
            for (const n of frontier) {
                if (seen.has(n.id)) continue;
                seen.add(n.id);
                if (ready(n.imgs?.[0])) return n.imgs[0];
                (n.inputs || []).forEach((inp, i) => {
                    if (inp.link != null && inp.type === "IMAGE") { const u = n.getInputNode?.(i); if (u) next.push(u); }
                });
            }
            frontier = next;
        }
        return null;
    }

    let editor = null;
    const origConfigure = node.onConfigure;
    node.onConfigure = function () { origConfigure?.apply(this, arguments); refreshBtn(); };
    const origRemoved = node.onRemoved;
    node.onRemoved = function () { editor?.close(); origRemoved?.apply(this, arguments); };

    function openEditor() {
        if (editor) return;
        const state = read();
        let sel = state.elements.length ? 0 : -1;       // selected element index
        let view = "target";                           // "target" | ref number
        let drag = null;                               // active pointer gesture

        const modal = openNkdModal({
            title: "😺 Klein Box Editor",
            hint: "drag on empty space to add a box · drag a box to move · handles resize · Del removes",
            onClose: () => { window.removeEventListener("keydown", onKey, true); resizeObs.disconnect(); editor = null; commit(); },
        });
        // Layout: stage (canvas + view tabs) | element panel.
        const stage = Object.assign(document.createElement("div"), {});
        stage.style.cssText = "position:relative;flex:1 1 auto;min-width:0;display:flex;flex-direction:column;";
        const tabs = document.createElement("div");
        tabs.style.cssText = "display:flex;gap:6px;padding:8px 10px;background:#13161c;border-bottom:1px solid #2a2e38;";
        const canvasWrap = document.createElement("div");
        canvasWrap.style.cssText = "position:relative;flex:1 1 auto;min-height:0;";
        const canvas = document.createElement("canvas");
        canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;touch-action:none;cursor:crosshair;";
        canvasWrap.appendChild(canvas);
        stage.append(tabs, canvasWrap);
        const panel = document.createElement("div");
        panel.style.cssText = "width:340px;flex:0 0 340px;display:flex;flex-direction:column;background:#13161c;border-left:1px solid #2a2e38;";
        const panelHead = document.createElement("div");
        panelHead.style.cssText = "display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid #2a2e38;";
        const list = document.createElement("div");
        list.style.cssText = "flex:1 1 auto;overflow:auto;padding:8px;display:flex;flex-direction:column;gap:8px;";
        panel.append(panelHead, list);
        modal.body.append(stage, panel);
        const status = modal.footerLeft;
        const ctx = canvas.getContext("2d");

        // ── model helpers ───────────────────────────────────────────────────────────
        const els = state.elements;
        const nextId = () => { let n = 1; const ids = new Set(els.map((e) => e.id)); while (ids.has(`obj_${n}`)) n++; return `obj_${n}`; };
        const defaultRef = () => (typeof view === "number" ? view : (linkedRef(0) ? 0 : 0));
        const addElement = (kind) => {
            const el = { id: nextId(), kind, ref: kind === "new" ? null : defaultRef(), desc: "", src: null, tgt: null };
            els.push(el);
            sel = els.length - 1;
            return el;
        };
        const commit = () => write({ elements: els });
        const viewRefs = () => {                       // reference views worth offering
            const set = new Set();
            for (let r = 0; r < 8; r++) if (linkedRef(r)) set.add(r);
            els.forEach((e) => { if (e.ref != null) set.add(e.ref); });
            return [...set].sort((a, b) => a - b);
        };
        // The box an element shows (and edits) in the current view, or null.
        const slotKey = (el) => {
            if (view === "target") return usesTgt(el) ? "tgt" : null;
            return usesSrc(el) && el.ref === view ? "src" : null;
        };
        // "keep" mirrors its source on the canvas (dashed, read-only there).
        const ghostKey = (el) => (view === "target" && el.kind === "keep" && el.src ? "src" : null);

        // ── geometry ────────────────────────────────────────────────────────────────
        const dims = () => {
            const im = view === "target" ? refImage(0) : refImage(view);
            if (ready(im)) return { im, a: im.naturalWidth / im.naturalHeight };
            const w = widget("width")?.value || 1024, h = widget("height")?.value || 1024;
            return { im: null, a: w / h };
        };
        let R = { x: 0, y: 0, w: 1, h: 1 }, dpr = 1;
        const layout = () => {
            dpr = window.devicePixelRatio || 1;
            const cw = canvasWrap.clientWidth, ch = canvasWrap.clientHeight;
            canvas.width = Math.max(1, Math.round(cw * dpr));
            canvas.height = Math.max(1, Math.round(ch * dpr));
            const m = 24, { a } = dims();
            let w = cw - 2 * m, h = w / a;
            if (h > ch - 2 * m) { h = ch - 2 * m; w = h * a; }
            R = { x: (cw - w) / 2, y: (ch - h) / 2, w: Math.max(1, w), h: Math.max(1, h) };
        };
        // grid units (0..1000) <-> canvas CSS px
        const gx = (px) => clamp(Math.round(((px - R.x) / R.w) * 1000), 0, 1000);
        const gy = (py) => clamp(Math.round(((py - R.y) / R.h) * 1000), 0, 1000);
        const boxPx = (b) => ({ x: R.x + (b[1] / 1000) * R.w, y: R.y + (b[0] / 1000) * R.h,
                                w: ((b[3] - b[1]) / 1000) * R.w, h: ((b[2] - b[0]) / 1000) * R.h });
        const handlePts = (b) => {
            const p = boxPx(b), xs = [p.x, p.x + p.w / 2, p.x + p.w], ys = [p.y, p.y + p.h / 2, p.y + p.h];
            return [[0, 0], [1, 0], [2, 0], [2, 1], [2, 2], [1, 2], [0, 2], [0, 1]]
                .map(([cx, cy]) => ({ cx, cy, x: xs[cx], y: ys[cy] }));
        };

        // ── drawing ─────────────────────────────────────────────────────────────────
        const draw = () => {
            layout();
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            const { im } = dims();
            ctx.fillStyle = "#14171d";
            ctx.fillRect(R.x, R.y, R.w, R.h);
            if (im) { ctx.globalAlpha = view === "target" ? 0.7 : 1; ctx.drawImage(im, R.x, R.y, R.w, R.h); ctx.globalAlpha = 1; }
            else {
                ctx.fillStyle = "rgba(255,255,255,0.28)"; ctx.font = "12px system-ui"; ctx.textAlign = "center";
                ctx.fillText(view === "target" ? "Connect an image to ref_0 to see it here" : `Connect an image to ref_${view}`, R.x + R.w / 2, R.y + R.h / 2);
                ctx.textAlign = "start";
            }
            ctx.strokeStyle = "rgba(255,255,255,0.25)"; ctx.lineWidth = 1; ctx.strokeRect(R.x + 0.5, R.y + 0.5, R.w - 1, R.h - 1);

            els.forEach((el, i) => {
                const key = slotKey(el) || ghostKey(el);
                const b = key && el[key];
                if (!b) return;
                const p = boxPx(b), on = i === sel, ghost = !slotKey(el);
                ctx.save();
                ctx.lineWidth = on ? 2.5 : 1.5;
                ctx.strokeStyle = color(i);
                ctx.fillStyle = color(i, on ? 0.18 : 0.08);
                if (ghost || el.kind === "remove") ctx.setLineDash([6, 4]);
                ctx.fillRect(p.x, p.y, p.w, p.h);
                ctx.strokeRect(p.x, p.y, p.w, p.h);
                ctx.setLineDash([]);
                if (el.kind === "remove" && view !== "target") {
                    ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + p.w, p.y + p.h);
                    ctx.moveTo(p.x + p.w, p.y); ctx.lineTo(p.x, p.y + p.h); ctx.stroke();
                }
                ctx.font = "11px system-ui";
                const label = el.id + (ghost ? " (kept)" : "");
                const tw = ctx.measureText(label).width + 8;
                ctx.fillStyle = color(i, 0.92);
                ctx.fillRect(p.x, Math.max(0, p.y - 16), tw, 16);
                ctx.fillStyle = "#0b0d12";
                ctx.fillText(label, p.x + 4, Math.max(0, p.y - 16) + 12);
                ctx.restore();
            });
            // selected element's handles
            const cur = els[sel];
            const ck = cur && slotKey(cur);
            if (ck && cur[ck]) {
                ctx.fillStyle = "#fff"; ctx.strokeStyle = color(sel); ctx.lineWidth = 1.5;
                for (const h of handlePts(cur[ck])) {
                    ctx.beginPath(); ctx.rect(h.x - 4, h.y - 4, 8, 8); ctx.fill(); ctx.stroke();
                }
            }
            if (drag?.mode === "draw" && drag.box) {
                const p = boxPx(drag.box);
                ctx.strokeStyle = "#fff"; ctx.setLineDash([4, 3]); ctx.lineWidth = 1.5;
                ctx.strokeRect(p.x, p.y, p.w, p.h); ctx.setLineDash([]);
            }
        };
        const redraw = () => draw();

        // ── interaction ─────────────────────────────────────────────────────────────
        const local = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
        const hitHandle = (px, py) => {
            const cur = els[sel], k = cur && slotKey(cur);
            if (!k || !cur[k]) return null;
            return handlePts(cur[k]).find((h) => Math.abs(h.x - px) <= HANDLE && Math.abs(h.y - py) <= HANDLE) || null;
        };
        const hitBox = (px, py) => {
            const order = els.map((_, i) => i).reverse();
            if (sel >= 0) order.unshift(sel);                                  // selected wins ties
            for (const i of order) {
                const k = slotKey(els[i]);
                const b = k && els[i][k];
                if (!b) continue;
                const p = boxPx(b);
                if (px >= p.x && px <= p.x + p.w && py >= p.y && py <= p.y + p.h) return i;
            }
            return -1;
        };
        canvas.addEventListener("pointerdown", (e) => {
            if (e.button !== 0) return;
            canvas.setPointerCapture(e.pointerId);
            const [px, py] = local(e);
            const h = hitHandle(px, py);
            if (h) {
                const k = slotKey(els[sel]);
                drag = { mode: "resize", h, k, start: els[sel][k].slice() };
            } else {
                const i = hitBox(px, py);
                if (i >= 0) {
                    sel = i;
                    const k = slotKey(els[i]);
                    drag = { mode: "move", k, start: els[i][k].slice(), gx: gx(px), gy: gy(py) };
                    styleCards();
                } else {
                    drag = { mode: "draw", x0: gx(px), y0: gy(py), box: null };
                }
            }
            draw();
        });
        canvas.addEventListener("pointermove", (e) => {
            if (!drag) return;
            const [px, py] = local(e), x = gx(px), y = gy(py);
            if (drag.mode === "draw") {
                drag.box = [Math.min(drag.y0, y), Math.min(drag.x0, x), Math.max(drag.y0, y), Math.max(drag.x0, x)];
            } else if (drag.mode === "move") {
                const [t, l, b, r] = drag.start, dx = x - drag.gx, dy = y - drag.gy;
                const ddx = clamp(dx, -l, 1000 - r), ddy = clamp(dy, -t, 1000 - b);
                els[sel][drag.k] = [t + ddy, l + ddx, b + ddy, r + ddx];
            } else {                                                           // resize
                const b = els[sel][drag.k].slice(), { cx, cy } = drag.h;
                if (cy === 0) b[0] = Math.min(y, b[2] - MIN_SIZE);
                if (cy === 2) b[2] = Math.max(y, b[0] + MIN_SIZE);
                if (cx === 0) b[1] = Math.min(x, b[3] - MIN_SIZE);
                if (cx === 2) b[3] = Math.max(x, b[1] + MIN_SIZE);
                els[sel][drag.k] = b;
            }
            draw(); refreshStatus();
        });
        const endDrag = () => {
            if (!drag) return;
            if (drag.mode === "draw" && drag.box) {
                const [t, l, b, r] = drag.box;
                if (b - t >= MIN_SIZE && r - l >= MIN_SIZE) placeDrawn(drag.box);
            }
            drag = null;
            commit(); renderList(); draw();
        };
        canvas.addEventListener("pointerup", endDrag);
        canvas.addEventListener("pointercancel", endDrag);

        // A drawn box fills the selected element's empty slot here, else becomes a new element.
        function placeDrawn(box) {
            const cur = els[sel];
            const slot = view === "target" ? "tgt" : "src";
            const fits = cur && (slot === "tgt" ? usesTgt(cur) : usesSrc(cur) && cur.ref === view) && !cur[slot];
            const el = fits ? cur : addElement(view === "target" ? "new" : "move");
            if (!fits && view !== "target") el.ref = view;
            el[slot] = box;
        }

        function onKey(e) {
            if (e.key !== "Delete" && e.key !== "Backspace") return;
            if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) return;
            if (sel < 0) return;
            e.preventDefault(); e.stopPropagation();
            els.splice(sel, 1);
            sel = Math.min(sel, els.length - 1);
            commit(); renderList(); draw();
        }
        window.addEventListener("keydown", onKey, true);

        // ── tabs / list / status ────────────────────────────────────────────────────
        function renderTabs() {
            tabs.replaceChildren();
            const mk = (label, v) => {
                const b = nkdButton(label, () => { view = v; renderTabs(); draw(); });
                b.classList.toggle("on", view === v);
                tabs.appendChild(b);
            };
            mk("Target (canvas)", "target");
            viewRefs().forEach((r) => mk(`Ref ${r}`, r));
        }
        const fmt = (b) => `[${b.join(", ")}]`;
        const statusOf = (el) => {
            if (complete(el)) {
                const parts = [];
                if (el.src && el.kind !== "new") parts.push(`from ref ${el.ref} ${fmt(el.src)}`);
                if (el.tgt && el.kind !== "remove") parts.push(`→ ${fmt(el.tgt)}`);
                return { text: parts.join("  ") || "ok", bad: false };
            }
            const need = [];
            if (el.kind !== "new" && !el.src) need.push(`source on Ref ${el.ref ?? "?"}`);
            if (usesTgt(el) && !el.tgt) need.push("target on Target");
            return { text: "⚠ draw " + need.join(" and "), bad: true };
        };
        const statusEls = [];
        function refreshStatus() {
            statusEls.forEach(({ el, node: n }) => { const s = statusOf(el); n.textContent = s.text; n.style.color = s.bad ? "#ff6b6b" : "rgba(255,255,255,0.45)"; });
            const bad = els.filter((e) => !complete(e)).length;
            status.textContent = bad ? `${els.length} elements · ${bad} incomplete` : `${els.length} element${els.length === 1 ? "" : "s"}`;
            status.classList.toggle("bad", bad > 0);
        }
        panelHead.replaceChildren(
            Object.assign(document.createElement("span"), { textContent: "Elements", style: "flex:1;font-weight:500;" }),
            nkdButton("+ Add", () => { addElement(typeof view === "number" ? "move" : "new"); commit(); renderTabs(); renderList(); draw(); }, "Add an element, then draw its box"),
        );

        const cards = [];
        function styleCards() {
            cards.forEach((c, i) => {
                c.style.borderColor = i === sel ? color(i) : "#2a2e38";
                c.style.borderLeftColor = color(i);
                c.style.background = i === sel ? "#1a1c22" : "#15181e";
            });
        }
        function renderList() {
            list.replaceChildren();
            statusEls.length = 0;
            cards.length = 0;
            if (!els.length) {
                list.appendChild(Object.assign(document.createElement("div"), {
                    textContent: "Drag on the image to add your first box.",
                    style: "color:rgba(255,255,255,0.4);padding:12px;text-align:center;" }));
            }
            els.forEach((el, i) => {
                const card = document.createElement("div");
                card.style.cssText = `border:1px solid ${i === sel ? color(i) : "#2a2e38"};border-left:4px solid ${color(i)};border-radius:6px;padding:8px;display:flex;flex-direction:column;gap:6px;background:${i === sel ? "#1a1c22" : "#15181e"};`;
                cards.push(card);
                // Select without re-rendering, or the click would steal focus from the field it landed on.
                card.addEventListener("pointerdown", () => { if (sel !== i) { sel = i; styleCards(); draw(); } });
                const row = document.createElement("div");
                row.style.cssText = "display:flex;gap:6px;align-items:center;";
                const idIn = Object.assign(document.createElement("input"), { value: el.id });
                idIn.style.cssText = "flex:1;min-width:0;background:#252830;border:1px solid #3a3d46;border-radius:4px;color:#c8d0e0;padding:3px 6px;font-size:12px;";
                idIn.oninput = () => { el.id = idIn.value.trim(); commit(); draw(); };
                const del = nkdButton("✕", () => { els.splice(i, 1); sel = Math.min(sel, els.length - 1); commit(); renderTabs(); renderList(); draw(); }, "Delete element");
                row.append(idIn, del);
                const row2 = document.createElement("div");
                row2.style.cssText = "display:flex;gap:6px;";
                const kindSel = nkdSelect(KINDS, el.kind, (v) => {
                    el.kind = v;
                    el.ref = v === "new" ? null : (el.ref ?? defaultRef());
                    commit(); renderTabs(); renderList(); draw();
                });
                row2.appendChild(kindSel);
                if (el.kind !== "new") {
                    const refs = []; for (let r = 0; r < 8; r++) refs.push({ value: String(r), label: `from ref ${r}` });
                    row2.appendChild(nkdSelect(refs, String(el.ref ?? 0), (v) => { el.ref = Number(v); commit(); renderTabs(); renderList(); draw(); }));
                }
                const desc = Object.assign(document.createElement("textarea"), { value: el.desc, rows: 2, placeholder: "Describe it (material, colour, pose…)" });
                desc.style.cssText = "resize:vertical;background:#252830;border:1px solid #3a3d46;border-radius:4px;color:#c8d0e0;padding:4px 6px;font:12px system-ui;";
                desc.oninput = () => { el.desc = desc.value; commit(); };
                const st = document.createElement("div");
                st.style.cssText = "font-size:11px;font-variant-numeric:tabular-nums;";
                statusEls.push({ el, node: st });
                card.append(row, row2, desc, st);
                list.appendChild(card);
            });
            refreshStatus();
        }

        modal.addPrimary("Save & close");
        const resizeObs = new ResizeObserver(() => draw());
        resizeObs.observe(canvasWrap);
        editor = { redraw, close: (r) => modal.close(r) };
        renderTabs(); renderList(); draw();
    }
}

app.registerExtension({
    name: "NKD.Klein.BoxEditor",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        if (nodeType.prototype.__nkdBoxWrapped) return;
        nodeType.prototype.__nkdBoxWrapped = true;
        const orig = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = orig?.apply(this, arguments);
            setup(this);
            return r;
        };
    },
});
