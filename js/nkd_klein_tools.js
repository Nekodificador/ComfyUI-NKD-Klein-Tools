import { app } from "../../scripts/app.js";

// Classic (LiteGraph) DOM widgets mis-size laterally on selection / DOM
// interaction — ballooning to canvas width or collapsing — while node.size[0]
// stays correct. Clamp the container back to the node width. WIDTH only.
function keepDomWidgetSized(node, container) {
  const MAX_MARGIN = 40;
  let enforcingW = false;
  let goodMargin = 15;
  const vueMode = () => !!(window.LiteGraph && window.LiteGraph.vueNodesMode);
  const clamp = () => {
    if (enforcingW) return;
    if (vueMode()) { if (container.style.width) container.style.width = ""; return; }
    const nodeW = node.size && node.size[0]; if (!nodeW) return;
    const host = container.parentElement;
    const hostW = host ? host.clientWidth : 0;
    const broken = hostW > 0 && (hostW > nodeW * 1.2 || hostW < nodeW * 0.7);
    if (!broken) {
      if (container.style.width) { enforcingW = true; container.style.width = ""; requestAnimationFrame(() => { enforcingW = false; }); }
      const cw = container.clientWidth;
      if (cw > 0 && cw <= nodeW && cw >= nodeW - MAX_MARGIN) goodMargin = nodeW - cw;
      return;
    }
    const ref = Math.round(nodeW - goodMargin);
    if (ref > 0 && Math.abs(container.clientWidth - ref) > 2) {
      enforcingW = true; container.style.boxSizing = "border-box"; container.style.width = ref + "px";
      requestAnimationFrame(() => { enforcingW = false; });
    }
  };
  clamp();
  const ro = new ResizeObserver(clamp);
  ro.observe(container);
  const origResize = node.onResize;
  node.onResize = function () { if (origResize) origResize.apply(this, arguments); clamp(); };
  const iv = setInterval(clamp, 250);
  return () => { ro.disconnect(); clearInterval(iv); };
}

const LEGACY_MEGAPIXEL_MAP = {
    "1 MP": 1.0,
    "2 MP": 2.0,
    "3 MP": 3.0,
    "4 MP": 4.0,
};

function migrateLegacyMegapixels(node) {
    const widget = node.widgets?.find(w => w.name === "megapixels");
    if (!widget) return;
    const v = widget.value;
    if (typeof v === "string" && LEGACY_MEGAPIXEL_MAP[v] !== undefined) {
        widget.value = LEGACY_MEGAPIXEL_MAP[v];
        widget.callback?.(widget.value);
        app.extensionManager?.toast?.add?.({
            severity: "info",
            summary: "NKD Klein Presampling",
            detail:
                "Megapixels is now a decimal value (0.1 – 4.0). Your saved " +
                "value was migrated automatically — please review the node " +
                "and adjust if needed.",
            life: 8000,
        });
    }
}

// A node saved before the "resize" input existed loads with its widgets/inputs
// out of sync with the new definition (positions shifted). Detect that — the
// telltale is a Presampling node with no "resize" widget — and tell the user to
// refresh it. Shown once per stale node.
function warnIfStalePresampling(node) {
    if (node._nkd_stale_warned) return;
    const hasResize = node.widgets?.some(w => w.name === "resize");
    if (hasResize) return;
    node._nkd_stale_warned = true;
    app.extensionManager?.toast?.add?.({
        severity: "warn",
        summary: "NKD Klein Presampling — node out of date",
        detail:
            "This node was saved with an older version and its inputs/widgets " +
            "are out of sync. Right-click it and choose 'Fix node (recreate)', " +
            "or delete it and add it again, to refresh the layout.",
        life: 12000,
    });
}

const MASK_DEPENDENT_WIDGETS = [
    "mask_expand",
    "mask_blur",
    "inpaint_blend",
    "use_detailing",
    "detail_padding",
];

function hideWidget(widget) {
    widget.hidden = true;                              // classic canvas render (1.0)
    if (widget.options) widget.options.hidden = true;  // Vue layout render (2.0)
    widget._nkd_hidden = true;
    widget.computeSize = () => [0, -4];                // collapse the gap on canvas (1.0)
}

function showWidget(widget) {
    widget.hidden = false;
    if (widget.options) widget.options.hidden = false;
    widget._nkd_hidden = false;
    delete widget.computeSize;
}

// Push visibility changes to BOTH renderers.
//  - Classic canvas (1.0): recompute the node size so hidden widgets (whose
//    computeSize returns [0,-4]) collapse their row.
//  - Vue nodes (2.0): the node's widget list is a cached snapshot behind a
//    shallowReactive array, and that snapshot CLONES options.hidden — so a plain
//    `options.hidden` mutation is never observed. Re-assigning node.widgets
//    through the reactive setter forces a re-extract (the same trick the
//    frontend itself uses for inputs/outputs on deep changes). On 1.0 this is a
//    harmless reassignment with identical widget refs.
function refreshNode(node) {
    if (Array.isArray(node.widgets)) {
        node.widgets = [...node.widgets];           // invalidate the 2.0 widget snapshot
    }
    // Force the Vue (2.0) node to re-render its widget area. Editing a widget
    // does this for free (its store value changes), but a CONNECTION change does
    // not — the visibility we just set would only appear after the next unrelated
    // edit. Re-emit a no-op property change (bgcolor → itself): the frontend's
    // useGraphNodeManager re-sets this node's reactive data, which re-reads the
    // freshly-invalidated widget snapshot. This is the same trigger the frontend
    // uses internally (useNodeErrorFlagSync). No-op on the classic canvas (1.0).
    node.graph?.trigger?.("node:property:changed", {
        type: "node:property:changed",
        nodeId: node.id,
        property: "bgcolor",
        oldValue: node.bgcolor,
        newValue: node.bgcolor,
    });
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
}

// Drive a visibility refresh from a widget's own callback. This is the only
// hook that fires in BOTH renderers: the classic canvas calls widget.callback
// on edit, and Vue nodes route edits through createWidgetUpdateHandler →
// widget.callback (they do NOT call node.onWidgetChanged). Idempotent per widget.
function wrapWidgetCallback(node, name, handler) {
    const w = node.widgets?.find(x => x.name === name);
    if (!w || w._nkd_cb_wrapped) return;
    const orig = w.callback;
    w.callback = function () {
        const r = orig?.apply(this, arguments);
        handler(node);
        return r;
    };
    w._nkd_cb_wrapped = true;
}

function setupPresamplingTriggers(node) {
    wrapWidgetCallback(node, "resize", updateResolutionWidgets);
    wrapWidgetCallback(node, "aspect_ratio", updateResolutionWidgets);
    wrapWidgetCallback(node, "image_fit", updateResolutionWidgets);
    wrapWidgetCallback(node, "use_detailing", updateDetailingWidgets);
}

function setupPostsamplingTriggers(node) {
    wrapWidgetCallback(node, "auto_detect_edit_region", updateAutoDetectWidgets);
}

function isMaskConnected(node) {
    const maskInput = node.inputs?.find(i => i.name === "mask");
    return maskInput ? maskInput.link !== null : false;
}

// True when any ref_* (Autogrow) input slot has a link.
function isRefConnected(node) {
    if (!node.inputs) return false;
    return node.inputs.some(
        i => i.name && i.name.startsWith("ref_") && i.link !== null
    );
}

function getWidgetValue(node, name) {
    return node.widgets?.find(w => w.name === name)?.value;
}

function setWidgetVisible(node, name, visible) {
    const w = node.widgets?.find(x => x.name === name);
    if (!w) return;
    if (visible) showWidget(w); else hideWidget(w);
}

// Master visibility pass for the resolution / reference area. Driven by:
//  - resize: when off, the node makes no sizing decision, so Aspect Ratio,
//    Megapixels, Custom W/H and Image Fit (and its dependents) are all hidden.
//  - reference connected: bypass_reference / reference_strength only matter then.
//  - aspect_ratio: Custom W/H only for "Custom"; Image Fit only when the ratio
//    can differ from the source (i.e. NOT "As Reference").
function updateResolutionWidgets(node) {
    const resize    = getWidgetValue(node, "resize") !== false;   // default on
    const refOn     = isRefConnected(node);
    const aspect    = getWidgetValue(node, "aspect_ratio");

    // Sizing controls — only when Resize is on.
    setWidgetVisible(node, "aspect_ratio", resize);
    setWidgetVisible(node, "megapixels", resize);
    setWidgetVisible(node, "custom_width",  resize && aspect === "Custom");
    setWidgetVisible(node, "custom_height", resize && aspect === "Custom");

    // Reference-only controls.
    setWidgetVisible(node, "bypass_reference", refOn);
    setWidgetVisible(node, "reference_strength", refOn);

    // Image Fit only does something when the canvas ratio can differ from the
    // source: needs Resize on, a reference connected, and a non-"As Reference"
    // ratio. Its dependents (outpaint_fill / slide) follow the chosen fit.
    const fitVisible = resize && refOn && aspect !== "As Reference";
    setWidgetVisible(node, "image_fit", fitVisible);
    if (fitVisible) {
        updateOutpaintFillWidget(node);
    } else {
        setWidgetVisible(node, "outpaint_fill", false);
        setWidgetVisible(node, "slide", false);
    }

    refreshNode(node);
}

function updateMaskWidgets(node) {
    const connected = isMaskConnected(node);
    for (const name of MASK_DEPENDENT_WIDGETS) {
        const widget = node.widgets?.find(w => w.name === name);
        if (!widget) continue;
        if (connected) showWidget(widget);
        else hideWidget(widget);
    }
    // When the mask is disconnected, force-disable use_detailing so its
    // stored value can't trigger a crop on the next run with no mask.
    if (!connected) {
        const useDetailingWidget = node.widgets?.find(w => w.name === "use_detailing");
        if (useDetailingWidget && useDetailingWidget.value === true) {
            useDetailingWidget.value = false;
            useDetailingWidget.callback?.(false);
        }
    }
    // detail_padding visibility also depends on use_detailing
    if (connected) updateDetailingWidgets(node);
    refreshNode(node);
}

function updateDetailingWidgets(node) {
    const useDetailingWidget = node.widgets?.find(w => w.name === "use_detailing");
    const paddingWidget      = node.widgets?.find(w => w.name === "detail_padding");
    if (!useDetailingWidget || !paddingWidget) return;

    const detailingOn = useDetailingWidget.value === true;
    if (detailingOn) showWidget(paddingWidget);
    else hideWidget(paddingWidget);
    refreshNode(node);
}

function updateOutpaintFillWidget(node) {
    const fitWidget   = node.widgets?.find(w => w.name === "image_fit");
    const fillWidget  = node.widgets?.find(w => w.name === "outpaint_fill");
    const slideWidget = node.widgets?.find(w => w.name === "slide");
    if (!fitWidget) return;

    const fit = fitWidget.value;
    // outpaint_fill only matters for Outpaint.
    if (fillWidget) {
        if (fit === "Outpaint") showWidget(fillWidget);
        else hideWidget(fillWidget);
    }
    // slide matters for both Outpaint and Center Crop.
    if (slideWidget) {
        if (fit === "Outpaint" || fit === "Center Crop") showWidget(slideWidget);
        else hideWidget(slideWidget);
    }
    refreshNode(node);
}

// Postsampling — auto-detect advanced widgets only matter when the toggle is on.
const AUTO_DETECT_DEPENDENT_WIDGETS = [
    "edge_softness",
    "region_padding",
    "fill_inner_gaps",
    "extend_to_borders",
];

function updateAutoDetectWidgets(node) {
    const toggle = node.widgets?.find(w => w.name === "auto_detect_edit_region");
    if (!toggle) return;
    const on = toggle.value === true;
    for (const name of AUTO_DETECT_DEPENDENT_WIDGETS) {
        const widget = node.widgets?.find(w => w.name === name);
        if (!widget) continue;
        if (on) showWidget(widget);
        else hideWidget(widget);
    }
    refreshNode(node);
}

// ---------------------------------------------------------------------------
// NKDKleinPromptBuilder — live preview of the assembled prompt.
// Mirrors prompt_assembly.py exactly (combo value = the phrase itself, "—" =
// skip), so the preview shows precisely what the node will output. Python stays
// the source of truth for execution; this is display-only.
// ---------------------------------------------------------------------------
const NKD_PB_SKIP = "—";
const NKD_PB_ORDER = [
    "medium", "style", "lighting", "camera_angle",
    "lens_shot", "composition", "mood", "color_grade",
];
const NKD_PB_WIDGETS = ["user_prompt", "format", ...NKD_PB_ORDER, "extra"];

function nkdPbClean(v) {
    const s = (v == null ? "" : String(v)).trim();
    return s === NKD_PB_SKIP ? "" : s;
}

function nkdPbJson(subject, chosen, extra) {
    const obj = {};
    const description = [subject, extra].filter(Boolean).join(", ");
    if (description) obj.subjects = [{ description }];
    const style = {};
    if (chosen.medium) style.medium = chosen.medium;
    if (chosen.style) style.technique = chosen.style;
    if (Object.keys(style).length) obj.style = style;
    const technical = {};
    const camera = [chosen.camera_angle, chosen.lens_shot].filter(Boolean).join(", ");
    if (camera) technical.camera = camera;
    if (chosen.lighting) technical.lighting = chosen.lighting;
    if (chosen.composition) technical.composition = chosen.composition;
    if (Object.keys(technical).length) obj.technical = technical;
    if (chosen.mood) obj.scene = { mood: chosen.mood };
    if (chosen.color_grade) obj.color_grade = chosen.color_grade;
    if (!Object.keys(obj).length) return "";
    return JSON.stringify(obj, null, 2);
}

function nkdPbAssemble(node) {
    const get = (n) => nkdPbClean(node.widgets?.find(w => w.name === n)?.value);
    const fmt = node.widgets?.find(w => w.name === "format")?.value || "natural";
    const subject = get("user_prompt");
    const extra = get("extra");
    const chosen = {};
    for (const c of NKD_PB_ORDER) chosen[c] = get(c);
    if (fmt === "json") return nkdPbJson(subject, chosen, extra);
    const parts = [];
    if (subject) parts.push(subject);
    for (const c of NKD_PB_ORDER) if (chosen[c]) parts.push(chosen[c]);
    if (extra) parts.push(extra);
    return parts.join(", ");
}

function nkdPbEnsurePreview(node) {
    if (node._nkdPbEl) return node._nkdPbEl;
    const el = document.createElement("div");
    el.className = "nkd-pb-preview";
    Object.assign(el.style, {
        whiteSpace: "pre-wrap", wordBreak: "break-word",
        fontFamily: "monospace", fontSize: "11px", lineHeight: "1.35",
        color: "#c8d0e0", background: "#1a1c22",
        border: "1px solid #3a3d46", borderRadius: "4px",
        padding: "6px 8px", boxSizing: "border-box",
        width: "100%", height: "100%", overflowY: "auto", margin: "0",
    });
    // getMin/Max/Height (not computeSize) is how v1 DOM widgets size — see nkd-node.
    node.addDOMWidget("nkd_preview", "NKD_PROMPT_PREVIEW", el, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 70,
        getMaxHeight: () => 300,
        getHeight: () => 100,
    });
    node._nkdPbEl = el;
    node._nkdW = keepDomWidgetSized(node, el);
    return el;
}

function nkdPbUpdatePreview(node) {
    const el = nkdPbEnsurePreview(node);
    const text = nkdPbAssemble(node);
    el.textContent = text || "(empty — type a prompt or pick presets above)";
}

function setupPromptBuilder(node) {
    for (const name of NKD_PB_WIDGETS) {
        wrapWidgetCallback(node, name, nkdPbUpdatePreview);
    }
}

// NKDKleinReferenceControl — show the regional controls only when a mask is
// connected (mask is optional → pure strength node without it).
const CONTROL_REGIONAL_WIDGETS = ["region_weight", "outside_suppression", "region_hardness"];

function updateControlWidgets(node) {
    const on = isMaskConnected(node);
    for (const name of CONTROL_REGIONAL_WIDGETS) {
        const w = node.widgets?.find(x => x.name === name);
        if (!w) continue;
        if (on) showWidget(w); else hideWidget(w);
    }
    refreshNode(node);
}

app.registerExtension({
    name: "nkd.klein_tools",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === "NKDKleinPresampling") {
            const origOnCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnCreated?.apply(this, arguments);
                requestAnimationFrame(() => {
                    setupPresamplingTriggers(this);
                    updateResolutionWidgets(this);
                    updateMaskWidgets(this);
                });
            };

            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                origOnConfigure?.apply(this, arguments);
                // Run after the widget values have been restored from the workflow.
                requestAnimationFrame(() => {
                    warnIfStalePresampling(this);
                    migrateLegacyMegapixels(this);
                    setupPresamplingTriggers(this);
                    updateResolutionWidgets(this);
                });
            };

            const origOnWidgetChanged = nodeType.prototype.onWidgetChanged;
            nodeType.prototype.onWidgetChanged = function (name, value) {
                origOnWidgetChanged?.apply(this, arguments);
                if (name === "resize" || name === "aspect_ratio" || name === "image_fit") {
                    updateResolutionWidgets(this);
                }
                if (name === "use_detailing") updateDetailingWidgets(this);
            };

            const origOnConnectionsChange = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function (type, index, connected, link_info) {
                origOnConnectionsChange?.apply(this, arguments);
                // type 1 = input connection change. Refresh both the mask-driven
                // widgets and the resolution/ref-driven ones (ref_* slots are
                // Autogrow inputs).
                if (type === 1) {
                    updateMaskWidgets(this);
                    updateResolutionWidgets(this);
                }
            };
            return;
        }

        if (nodeData.name === "NKDKleinPostsampling") {
            const origOnCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnCreated?.apply(this, arguments);
                requestAnimationFrame(() => {
                    setupPostsamplingTriggers(this);
                    updateAutoDetectWidgets(this);
                });
            };

            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                origOnConfigure?.apply(this, arguments);
                requestAnimationFrame(() => {
                    setupPostsamplingTriggers(this);
                    updateAutoDetectWidgets(this);
                });
            };

            const origOnWidgetChanged = nodeType.prototype.onWidgetChanged;
            nodeType.prototype.onWidgetChanged = function (name, value) {
                origOnWidgetChanged?.apply(this, arguments);
                if (name === "auto_detect_edit_region") updateAutoDetectWidgets(this);
            };
            return;
        }

        if (nodeData.name === "NKDKleinPromptBuilder") {
            const origOnCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnCreated?.apply(this, arguments);
                requestAnimationFrame(() => {
                    nkdPbEnsurePreview(this);
                    setupPromptBuilder(this);
                    nkdPbUpdatePreview(this);
                    this.setSize(this.computeSize());
                    this.setDirtyCanvas(true, true);
                });
            };

            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                origOnConfigure?.apply(this, arguments);
                requestAnimationFrame(() => {
                    setupPromptBuilder(this);
                    nkdPbUpdatePreview(this);
                });
            };

            const origOnWidgetChanged = nodeType.prototype.onWidgetChanged;
            nodeType.prototype.onWidgetChanged = function (name, value) {
                origOnWidgetChanged?.apply(this, arguments);
                nkdPbUpdatePreview(this);
            };

            const origOnRemoved = nodeType.prototype.onRemoved;
            nodeType.prototype.onRemoved = function () {
                this._nkdW?.();  // stop the width clamp's observer + poll
                origOnRemoved?.apply(this, arguments);
            };
            return;
        }

        if (nodeData.name === "NKDKleinReferenceControl") {
            const origOnCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnCreated?.apply(this, arguments);
                requestAnimationFrame(() => updateControlWidgets(this));
            };

            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                origOnConfigure?.apply(this, arguments);
                requestAnimationFrame(() => updateControlWidgets(this));
            };

            const origOnConnectionsChange = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function (type, index, connected, link_info) {
                origOnConnectionsChange?.apply(this, arguments);
                if (type === 1) updateControlWidgets(this);
            };
            return;
        }
    },
});
