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

const MASK_DEPENDENT_WIDGETS = [
    "mask_expand",
    "mask_blur",
    "inpaint_blend",
    "use_detailing",
    "detail_padding",
];

function hideWidget(widget) {
    widget.hidden = true;
    widget._nkd_hidden = true;
    widget.computeSize = () => [0, -4];
}

function showWidget(widget) {
    widget.hidden = false;
    widget._nkd_hidden = false;
    delete widget.computeSize;
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

// image_fit / bypass_reference / reference_strength only do anything when a
// reference image is connected — hide them otherwise to keep the node clean.
const REF_DEPENDENT_WIDGETS = [
    "image_fit",
    "bypass_reference",
    "reference_strength",
];

function updateRefDependentWidgets(node) {
    const connected = isRefConnected(node);
    for (const name of REF_DEPENDENT_WIDGETS) {
        const widget = node.widgets?.find(w => w.name === name);
        if (!widget) continue;
        if (connected) showWidget(widget);
        else hideWidget(widget);
    }
    // outpaint_fill / slide depend on image_fit, which is itself ref-dependent.
    if (connected) updateOutpaintFillWidget(node);
    else {
        for (const n of ["outpaint_fill", "slide"]) {
            const w = node.widgets?.find(x => x.name === n);
            if (w) hideWidget(w);
        }
    }
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
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
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
}

function updateDetailingWidgets(node) {
    const useDetailingWidget = node.widgets?.find(w => w.name === "use_detailing");
    const paddingWidget      = node.widgets?.find(w => w.name === "detail_padding");
    if (!useDetailingWidget || !paddingWidget) return;

    const detailingOn = useDetailingWidget.value === true;
    if (detailingOn) showWidget(paddingWidget);
    else hideWidget(paddingWidget);
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
}

function updateCustomSizeWidgets(node) {
    const aspectWidget = node.widgets?.find(w => w.name === "aspect_ratio");
    const widthWidget  = node.widgets?.find(w => w.name === "custom_width");
    const heightWidget = node.widgets?.find(w => w.name === "custom_height");
    if (!aspectWidget || !widthWidget || !heightWidget) return;

    const visible = aspectWidget.value === "Custom";
    if (visible) {
        showWidget(widthWidget);
        showWidget(heightWidget);
    } else {
        hideWidget(widthWidget);
        hideWidget(heightWidget);
    }
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
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
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
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
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "nkd.klein_tools",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === "NKDKleinPresampling") {
            const origOnCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnCreated?.apply(this, arguments);
                requestAnimationFrame(() => {
                    updateCustomSizeWidgets(this);
                    updateRefDependentWidgets(this);
                    updateMaskWidgets(this);
                });
            };

            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                origOnConfigure?.apply(this, arguments);
                // Run after the widget values have been restored from the workflow.
                requestAnimationFrame(() => {
                    migrateLegacyMegapixels(this);
                    updateRefDependentWidgets(this);
                });
            };

            const origOnWidgetChanged = nodeType.prototype.onWidgetChanged;
            nodeType.prototype.onWidgetChanged = function (name, value) {
                origOnWidgetChanged?.apply(this, arguments);
                if (name === "aspect_ratio")  updateCustomSizeWidgets(this);
                if (name === "use_detailing") updateDetailingWidgets(this);
                if (name === "image_fit")     updateOutpaintFillWidget(this);
            };

            const origOnConnectionsChange = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function (type, index, connected, link_info) {
                origOnConnectionsChange?.apply(this, arguments);
                // type 1 = input connection change. Refresh both the mask-driven
                // widgets and the ref-driven ones (ref_* slots are Autogrow inputs).
                if (type === 1) {
                    updateMaskWidgets(this);
                    updateRefDependentWidgets(this);
                }
            };
            return;
        }

        if (nodeData.name === "NKDKleinPostsampling") {
            const origOnCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnCreated?.apply(this, arguments);
                requestAnimationFrame(() => updateAutoDetectWidgets(this));
            };

            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                origOnConfigure?.apply(this, arguments);
                requestAnimationFrame(() => updateAutoDetectWidgets(this));
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
