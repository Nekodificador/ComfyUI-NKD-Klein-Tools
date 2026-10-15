const w="nkd-modal-styles",C=`
.nkd-modal-overlay {
  position: fixed; inset: 0; z-index: 100000;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.8); backdrop-filter: blur(3px);
  font: 12px system-ui, sans-serif; color: #c8d0e0;
}
/* Framed panel, never edge-to-edge: the graph staying visible around the border
   is what keeps the editor feeling like part of the workflow. */
.nkd-modal-panel {
  display: flex; flex-direction: column;
  background: #111318; color: #c8d0e0;
  border: 1px solid #3a3d46; border-radius: 10px;
  box-shadow: 0 12px 48px rgba(0,0,0,0.7);
  overflow: hidden;
}
.nkd-modal-head {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: #1a1c22;
  border-bottom: 1px solid rgba(255,255,255,0.07); font-weight: 500;
}
.nkd-modal-hint { color: rgba(255,255,255,0.40); font-size: 11px; font-weight: 400; }
.nkd-modal-spacer { flex: 1 1 auto; }
.nkd-modal-x {
  background: transparent; border: none; color: #c8d0e0;
  font-size: 16px; cursor: pointer; padding: 2px 8px; border-radius: 4px;
}
.nkd-modal-x:hover { background: rgba(255,77,77,0.25); color: #ff6b6b; }
.nkd-modal-body { position: relative; flex: 1 1 auto; min-height: 0; background: #0b0d12; display: flex; }
.nkd-modal-body > canvas { display: block; width: 100%; height: 100%; touch-action: none; }
.nkd-modal-foot {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 14px; background: #1a1c22;
  border-top: 1px solid rgba(255,255,255,0.07);
}
.nkd-modal-foot-left, .nkd-modal-foot-right { display: flex; align-items: center; gap: 12px; }

/* Shared controls. Vue templates use these class names directly instead of
   re-declaring the same rules in <style scoped>. */
.nkd-modal-btn {
  background: #252830; border: 1px solid #3a3d46; border-radius: 4px;
  color: #c8d0e0; padding: 4px 10px; font-size: 12px; cursor: pointer;
}
.nkd-modal-btn:hover { border-color: #4ab4ff; }
.nkd-modal-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.nkd-modal-btn.on { border-color: #4ab4ff; color: #4ab4ff; background: rgba(74,180,255,0.12); }
.nkd-modal-btn.primary { border-color: #4ab4ff; color: #4ab4ff; font-weight: 500; padding: 5px 14px; }
.nkd-modal-btn.primary:hover { background: rgba(74,180,255,0.15); }
.nkd-modal-lbl { color: rgba(255,255,255,0.55); display: flex; align-items: center; gap: 6px; }
.nkd-modal-rng { width: 80px; accent-color: #4ab4ff; cursor: pointer; touch-action: none; }
.nkd-modal-num {
  color: #c8d0e0; font-variant-numeric: tabular-nums;
  min-width: 52px; text-align: right;
}
.nkd-modal-sel {
  background: #252830; border: 1px solid #3a3d46; border-radius: 4px;
  color: #c8d0e0; padding: 3px 8px; font-size: 12px; cursor: pointer;
}
.nkd-modal-status { color: #4ab4ff; font-variant-numeric: tabular-nums; }
.nkd-modal-status.bad { color: #ff6b6b; }
`;function M(){if(document.getElementById(w))return;const d=document.createElement("style");d.id=w,d.textContent=C,document.head.appendChild(d)}function m(d){const t=document.createElement("div");return t.className=d,t}function S(d){M();const t=m("nkd-modal-overlay"),i=m("nkd-modal-panel");i.style.width=d.width??"92vw",i.style.height=d.height??"92vh",i.style.maxWidth=d.maxWidth??"1800px";const l=m("nkd-modal-head"),o=document.createElement("span");o.textContent=d.title;const e=document.createElement("span");e.className="nkd-modal-hint",e.textContent=d.hint??"";const r=document.createElement("button");r.className="nkd-modal-x",r.textContent="\u2715",r.title="Close (Esc)",l.append(o,e,m("nkd-modal-spacer"),r);const x=m("nkd-modal-body"),p=m("nkd-modal-foot"),g=m("nkd-modal-foot-left"),u=m("nkd-modal-foot-right");p.append(g,m("nkd-modal-spacer"),u),i.append(l,x,p),t.append(i),document.body.appendChild(t);let b=!1;function c(a="dismiss"){b||(b=!0,window.removeEventListener("keydown",n,!0),t.remove(),d.onClose?.(a))}function n(a){a.key==="Escape"&&(a.stopPropagation(),a.preventDefault(),c("dismiss"))}return r.onclick=()=>c("dismiss"),d.closeOnEsc!==!1&&window.addEventListener("keydown",n,!0),d.closeOnBackdrop!==!1&&t.addEventListener("pointerdown",a=>{a.target===t&&c("dismiss")}),{overlay:t,panel:i,head:l,body:x,footer:p,footerLeft:g,footerRight:u,setTitle:a=>{o.textContent=a},setHint:a=>{e.textContent=a},addPrimary(a,s){const f=L(a,()=>{s?.(),c("save")});return f.classList.add("primary"),u.appendChild(f),f},close:c}}function L(d,t,i){const l=document.createElement("button");return l.className="nkd-modal-btn",l.textContent=d,i&&(l.title=i),l.onclick=t,l}function T(d,t,i,l){let o=t;const e=L(d,()=>{o=!o,e.classList.toggle("on",o),i(o)},l);return e.classList.toggle("on",o),e}const N=.1;function D(d,t,i,l){const o=document.createElement("label");o.className="nkd-modal-lbl",l&&(o.title=l);const e=document.createElement("input");e.type="range",e.className="nkd-modal-rng",e.min=String(t.min),e.max=String(t.max),e.step="any",e.value=String(t.value),t.width&&(e.style.width=`${t.width}px`);const r=t.format?document.createElement("span"):null;r&&(r.className="nkd-modal-num");const x=t.fine??t.step/10,p=n=>Math.max(t.min,Math.min(t.max,n)),g=(n,a)=>{const s=a?x:t.step;return Math.round(Math.round(p(n)/s)*s*1e6)/1e6},u=n=>{r&&(r.textContent=t.format(n))},b=(n,a)=>{const s=g(n,a);e.value=String(s),u(s),i(s)};e.addEventListener("pointerdown",n=>{if(n.button!==0||e.disabled)return;n.preventDefault(),e.focus();try{e.setPointerCapture(n.pointerId)}catch{}const a=e.getBoundingClientRect(),s=t.max-t.min,f=Math.max(1,a.width);let h=n.shiftKey?parseFloat(e.value):p(t.min+(n.clientX-a.left)/f*s);b(h,n.shiftKey);let y=n.clientX;const E=k=>{h=p(h+(k.clientX-y)/f*s*(k.shiftKey?N:1)),y=k.clientX,b(h,k.shiftKey)},v=k=>{e.removeEventListener("pointermove",E),e.removeEventListener("pointerup",v),e.removeEventListener("pointercancel",v);try{e.releasePointerCapture(k.pointerId)}catch{}};e.addEventListener("pointermove",E),e.addEventListener("pointerup",v),e.addEventListener("pointercancel",v)}),e.addEventListener("keydown",n=>{const a=n.key==="ArrowRight"||n.key==="ArrowUp"?1:n.key==="ArrowLeft"||n.key==="ArrowDown"?-1:0;if(!a)return;n.preventDefault();const s=n.shiftKey?x:t.step;b(parseFloat(e.value)+a*s,n.shiftKey)});const c=document.createElement("span");return c.className="nkd-modal-lbl-txt",c.textContent=d,o.append(c,e),r&&o.appendChild(r),u(t.value),o.sync=n=>{e.value=String(n),u(n)},o.setDisabled=n=>{e.disabled=n,o.style.opacity=n?"0.45":""},o}function H(d,t,i,l){const o=document.createElement("select");o.className="nkd-modal-sel",l&&(o.title=l);for(const e of d){const r=document.createElement("option");r.value=e.value,r.textContent=e.label,o.appendChild(r)}return o.value=t,o.onchange=()=>i(o.value),o.addEventListener("wheel",e=>{e.preventDefault(),e.stopPropagation();const r=o.selectedIndex+(e.deltaY<0?1:-1);r<0||r>=o.options.length||(o.selectedIndex=r,o.dispatchEvent(new Event("change")))},{passive:!1}),o}export{M as ensureNkdModalStyles,L as nkdButton,H as nkdSelect,D as nkdSlider,T as nkdToggle,S as openNkdModal};
