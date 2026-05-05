import { app } from "../../scripts/app.js";

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

function updateMaskWidgets(node) {
    const connected = isMaskConnected(node);
    for (const name of MASK_DEPENDENT_WIDGETS) {
        const widget = node.widgets?.find(w => w.name === name);
        if (!widget) continue;
        if (connected) showWidget(widget);
        else hideWidget(widget);
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

app.registerExtension({
    name: "nkd.klein_tools",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "NKDKleinPresampling") return;

        const origOnCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnCreated?.apply(this, arguments);
            requestAnimationFrame(() => {
                updateCustomSizeWidgets(this);
                updateMaskWidgets(this);
            });
        };

        const origOnWidgetChanged = nodeType.prototype.onWidgetChanged;
        nodeType.prototype.onWidgetChanged = function (name, value) {
            origOnWidgetChanged?.apply(this, arguments);
            if (name === "aspect_ratio")  updateCustomSizeWidgets(this);
            if (name === "use_detailing") updateDetailingWidgets(this);
        };

        const origOnConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (type, index, connected, link_info) {
            origOnConnectionsChange?.apply(this, arguments);
            if (type === 1) updateMaskWidgets(this);
        };
    },
});
