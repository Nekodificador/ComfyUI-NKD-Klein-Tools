import { app } from "../../scripts/app.js";

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
    },
});
