/* Keep chart interactions in the browser so pan/zoom never triggers a Python rerun. */
(() => {
  "use strict";
  const chart = document.getElementById("chart");
  const viewer = document.getElementById("viewer");
  const notice = document.getElementById("notice");
  const toolbar = viewer.querySelector("nav");
  const follow = toolbar.querySelector('[data-action="follow"]');
  const full = toolbar.querySelector('[data-action="full"]');
  const sessionViews = new Map();
  const legend = toolbar.querySelector('[data-action="legend"]');
  let current, incoming, rendered, view, scope, updating = false, bound = false;
  let queue = Promise.resolve();

  function enqueue(fn) {
    queue = queue.then(fn).catch(() => {
      notice.textContent = "The chart could not refresh. Reset zoom or reopen the workspace.";
      notice.hidden = false;
    });
  }
  function post(type, payload) {
    parent.postMessage({isStreamlitMessage:true, type:"streamlit:" + type, ...payload}, "*");
  }
  function frameHeight() {
    post("setFrameHeight", {height:Math.ceil(viewer.getBoundingClientRect().height)});
  }
  function pair(v) {
    return Array.isArray(v) && v.length === 2 && v.every(Number.isFinite) && v[1] > v[0];
  }
  function readState(id) {
    let saved = sessionViews.get(id);
    if (!saved) {
      try { saved = JSON.parse(sessionStorage.getItem("qd:overview:2:" + id)); } catch (_) {}
    }
    return {follow:saved?.follow !== false, x:pair(saved?.x) ? saved.x : null,
      y:pair(saved?.y) ? saved.y : null, last:Number.isFinite(saved?.last) ? saved.last : null,
      legend: typeof saved?.legend === "boolean" ? saved.legend : null,
      series:(saved?.series && typeof saved.series === "object") ? saved.series : {}};
  }
  function persist() {
    sessionViews.set(scope, view);
    try { sessionStorage.setItem("qd:overview:2:" + scope, JSON.stringify(view)); } catch (_) {}
    follow.setAttribute("aria-pressed", String(view.follow));
  }
  function defaults() {
    return current.layout.meta.initial_x.slice();
  }
  function latest() { return defaults()[1]; }
  function latestRange(width) { return [latest()-width, latest()]; }
  function autoscale(x) {
    const t = current.data[0], lows=t.low || t.y, highs=t.high || t.y;
    let low=Infinity, high=-Infinity;
    for (let i=0; i<t.x.length; i++) {
      if (t.x[i]>=x[0] && t.x[i]<=x[1]) {low=Math.min(low,lows[i]);high=Math.max(high,highs[i]);}
    }
    for (const quote of current.data.filter(trace=>trace.name?.endsWith(" quote"))) {
      if (quote.x[0]>=x[0] && quote.x[0]<=x[1] && Number.isFinite(quote.y[0])) {
        low=Math.min(low,quote.y[0]); high=Math.max(high,quote.y[0]);
      }
    }
    if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
    const pad=Math.max((high-low)*.09, Math.abs(high)*.00015, .001);
    return [low-pad, high+pad];
  }
  function chartHeight() {
    return document.fullscreenElement ? Math.max(300, innerHeight-toolbar.offsetHeight-16) :
      current.layout.height + (viewer.clientWidth<530 ? 28 : 0);
  }
  function labelShifts(layout, range) {
    if (!range) return;
    const levels = (layout.annotations || []).filter(a=>a.name === "price-level" && a.y>=range[0] && a.y<=range[1])
      .sort((a,b)=>a.y-b.y);
    const pixels = Math.max(120, (chartHeight()-layout.margin.t-layout.margin.b)*(layout.yaxis.domain[1]-layout.yaxis.domain[0]));
    const loc = y => (y-range[0])/(range[1]-range[0])*pixels;
    const positions=[];
    // Keep every numeric level on its true horizontal line. Move just the text.
    for (const a of levels) {
      const p=loc(a.y);
      positions.push(Math.max(p,positions.length ? positions.at(-1)+14 : 7));
    }
    let ceiling=pixels-7;
    for (let i=levels.length-1;i>=0;i--) {
      positions[i]=Math.min(positions[i],ceiling);
      levels[i].yshift=positions[i]-loc(levels[i].y);
      ceiling=positions[i]-14;
    }
  }
  function ticks(range) {
    const meta=current.layout.meta;
    const indices=meta.bars.map((x,i)=>x>=range[0] && x<=range[1] ? i : -1).filter(i=>i>=0);
    const n=Math.min(indices.length, viewer.clientWidth<520 ? 3 : 6);
    const wanted=Array.from({length:n},(_,i)=>indices[Math.round(i*(indices.length-1)/Math.max(1,n-1))]);
    return {tickvals:wanted.map(i=>meta.bars[i]), ticktext:wanted.map(i=>meta.labels[i])};
  }
  async function draw() {
    if (!incoming) return;
    // Keep the currently visible historical observations when the live API
    // drops them from its rolling window. At most two input windows are held.
    const parked = current && incoming !== current && !view.follow && view.x &&
      incoming.layout.meta.bars[0] > Math.max(view.x[0],current.layout.meta.bars[0]) &&
      view.x[1] >= current.layout.meta.bars[0] && view.x[0] < current.layout.meta.bars.at(-1);
    if (!parked) current=incoming;
    let x=view.x || defaults();
    if (view.follow && view.last !== null && latest() !== view.last) x=latestRange(x[1]-x[0]);
    view.x=x; view.last=latest(); persist();
    const layout=structuredClone(current.layout), y=view.y || autoscale(x), xt=ticks(x);
    const offscreen=x[1] < layout.meta.bars[0] || x[0] > latest();
    notice.hidden=!offscreen && !parked;
    if(parked) notice.textContent="Showing the retained historical chart. Follow latest to load the current feed.";
    if(offscreen) notice.textContent="This time window is outside the available candle history. Reset zoom to see current data.";
    for(const [k,axis] of Object.entries(layout)) {
      if(/^xaxis\d*$/.test(k)) Object.assign(axis, xt, {range:x, autorange:false});
    }
    Object.assign(layout.yaxis,y ? {range:y,autorange:false} : {autorange:true});
    layout.showlegend = view.legend ?? (viewer.clientWidth>=530);
    legend.setAttribute("aria-pressed",String(layout.showlegend));
    if(!layout.showlegend)layout.margin.b=26;
    layout.height=chartHeight(); layout.width=viewer.clientWidth;
    layout.autosize=false; layout.uirevision=scope; layout.dragmode="pan";
    for (const label of layout.annotations || []) {
      if (label.name === "latest-price") label.visible=x[0]<=layout.meta.bars.at(-1) && x[1]>=layout.meta.bars.at(-1);
    }
    labelShifts(layout,y);
    const traces=current.data.map(t=>Object.hasOwn(view.series, t.name) ? {...t,visible:view.series[t.name]} : {...t});
    updating=true;
    try {
      await Plotly.react(chart,traces,layout,{responsive:false,displaylogo:false,scrollZoom:true,
        displayModeBar:false,doubleClick:false});
    } finally {updating=false;}
    if (!bound) {
      chart.on("plotly_relayout", changed);
      chart.on("plotly_restyle", ev=>{
        if(updating || !ev?.[0] || !Object.hasOwn(ev[0],"visible"))return;
        for(const idx of ev[1]) view.series[chart.data[idx].name]=chart.data[idx].visible;
        persist();
      });
      bound=true;
    }
    frameHeight();
  }
  function changed(ev) {
    if(updating || !current || !chart._fullLayout) return;
    let xChanged=false, yChanged=false;
    for(const k of Object.keys(ev)) {
      if(/^xaxis\d*\.(range|autorange)/.test(k)) xChanged=true;
      if(/^yaxis\.(range|autorange)/.test(k)) yChanged=true;
    }
    if(!xChanged && !yChanged) return;
    if(xChanged) {
      const axis=chart._fullLayout.xaxis;
      view.x=axis.range.slice();
      // Dragging into history pins that window. Button and wheel zoom preserve follow near the live edge.
      if(latest()-view.x[1]>Math.max(2,(view.x[1]-view.x[0])*.08)) view.follow=false;
    }
    if(yChanged) view.y=ev["yaxis.autorange"] ? null : chart._fullLayout.yaxis.range.slice();
    persist(); enqueue(draw);
  }
  async function resetOrZoom(action) {
    if (!current) return;
    const x=view.x || defaults(), width=x[1]-x[0];
    if(action==="reset") {current=incoming;view.x=defaults();view.y=null;}
    if(action==="legend") view.legend=!(view.legend ?? (viewer.clientWidth>=530));
    if(action==="follow") {view.follow=!view.follow;if(view.follow){current=incoming;view.x=latestRange(width);}}
    if(action==="in" || action==="out") {
      const newWidth=Math.max(3,Math.min((latest()-current.layout.meta.bars[0]+1)*1.4,width*(action==="in" ? .65 : 1/.65)));
      const mid=(x[0]+x[1])/2;
      view.x=view.follow ? latestRange(newWidth) : [mid-newWidth/2,mid+newWidth/2];
    }
    await draw();
  }
  toolbar.addEventListener("click",event=>{
    const action=event.target.closest("button")?.dataset.action;
    if(action==="full") {
      if(document.fullscreenElement) document.exitFullscreen();
      else viewer.requestFullscreen().catch(()=>{ notice.textContent="Fullscreen is unavailable in this browser."; notice.hidden=false; frameHeight(); });
    } else if(action) enqueue(()=>resetOrZoom(action));
  });
  document.addEventListener("fullscreenchange",()=>{
    full.setAttribute("aria-pressed",String(!!document.fullscreenElement)); enqueue(draw);
  });
  let renderedWidth=0;
  new ResizeObserver(()=>{
    if(viewer.clientWidth && viewer.clientWidth!==renderedWidth) {
      renderedWidth=viewer.clientWidth; if(current) enqueue(draw);
    }
  }).observe(viewer);
  window.addEventListener("message",evt=>{
    if(evt.source!==parent || evt.data?.type!=="streamlit:render") return;
    const args=evt.data.args;
    enqueue(async()=>{
      if(args.scope===scope && args.figure===rendered) return;
      if(args.scope!==scope) {scope=args.scope;view=readState(scope);current=null;}
      rendered=args.figure;
      incoming=JSON.parse(args.figure);
      await draw();
    });
  });
  post("componentReady", {apiVersion:1});
})();
