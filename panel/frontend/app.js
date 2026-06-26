// SIGNAL//LAYER — read-only dashboard. Polls the backend and renders.
const C = { canvas:"#0D0E12", surface:"#14161D", lift:"#1E2330",
  chartreuse:"#C8FF00", claude:"#FF8A1E", nemo:"#76B900", deepseek:"#4D6BFE", human:"#F5A623",
  consensus:"#FF5CA8", long:"#2ECC71", short:"#FF4D4D", flat:"#3A3F50", ink:"#E8EAF0", muted:"#6B7080" };
const PLOT_BG = { paper_bgcolor:C.surface, plot_bgcolor:C.surface,
  font:{color:C.ink, family:"JetBrains Mono"}, margin:{l:40,r:12,t:8,b:28} };

// Header model selector — drives the currency-strength RIBBON ONLY.
// Values: claude|nemotron|deepseek|combined. (The consensus heatmap has its own
// independent layer buttons -> _heatLayer; the instrument cones show all models.)
let _ribbonModel = "claude";
let _heatLayer = "claude";

async function getJSON(u){
  try { const r = await fetch(u); if(!r.ok) return null; return await r.json(); }
  catch(e){ return null; }
}
const fmt = (n,d=0)=> n==null? "—" : Number(n).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});

// currency-strength ribbon: aggregate each currency across ALL its pairs
// (base +, quote -, weighted by confidence) into one net up/down — using the
// per-symbol view of the selector's model (_ribbonModel). Symbols where the model
// has no view are skipped; "combined" uses the row's top-level direction/confidence.
function renderRibbon(consensus){
  const _sgn = d => d==="long"?1 : d==="short"?-1 : 0;
  const _view = r => _ribbonModel === "combined"
    ? {direction:r.direction, confidence:r.confidence}
    : r[_ribbonModel];   // claude / nemotron / deepseek — may be null/undefined
  const _acc = {};
  for(const r of (consensus || [])){
    if(!r.symbol || r.symbol.length < 6) continue;
    const v = _view(r);
    if(!v || !v.direction) continue;   // selected model has no view for this symbol
    const _b = r.symbol.slice(0,3), _q = r.symbol.slice(3,6);
    const _val = _sgn(v.direction) * (v.confidence || 0);
    (_acc[_b] = _acc[_b] || {s:0,n:0}); _acc[_b].s += _val; _acc[_b].n++;
    (_acc[_q] = _acc[_q] || {s:0,n:0}); _acc[_q].s -= _val; _acc[_q].n++;
  }
  const _ccys = Object.keys(_acc).map(c=>({c, str:_acc[c].s/Math.max(_acc[c].n,1)})).sort((a,b)=>b.str-a.str);
  const _chips = _ccys.map(({c,str})=>{
    const cls = str > 0.02 ? "long" : str < -0.02 ? "short" : "flat";
    const ar = str > 0.02 ? "▲" : str < -0.02 ? "▼" : "·";
    return `<span class="rchip ${cls}" title="net strength ${str.toFixed(2)} across its pairs">${c} ${ar}</span>`;
  }).join("");
  const _rib = document.getElementById("ribbon");
  if(_rib) _rib.innerHTML = _chips || '<span class="rchip flat">awaiting market read…</span>';
}

const AI_REFRESH_SECS = 900;   // engine ai_refresh_seconds (15 min) — for the countdown

// KPI tiles — split out so the fast 1s state poll can refresh them every snapshot.
function renderKpis(a){
  const kpis = [
    ["EQUITY", fmt(a.equity)], ["RET", a.return_pct!=null? (a.return_pct*100).toFixed(2)+"%":"—"],
    ["LEV", a.gross_leverage!=null? a.gross_leverage.toFixed(1)+"x":"—"],
    ["ML", a.margin_level!=null? Math.round(a.margin_level)+"%":"—"],
    ["DD", a.drawdown!=null? (a.drawdown*100).toFixed(1)+"%":"—"],
    ["SHARPE", a.sharpe!=null? a.sharpe.toFixed(2):"—"],
  ];
  const el = document.getElementById("kpis"); if(!el) return;
  el.innerHTML = kpis.map(([k,v])=> `<div class="kpi"><label>${k}</label><b>${v}</b></div>`).join("");
}

// "refreshing AI views in M:SS" countdown next to the refresh icon + spinner stop.
function renderViewsAge(s){
  const rb = document.getElementById("refresh-btn"), va = document.getElementById("views-age");
  const spinning = rb && rb.classList.contains("spin");
  if(spinning && s.ts && s.ts !== _refreshAtTs){      // a new decision landed -> stop the spinner
    rb.classList.remove("spin"); clearTimeout(_refreshTimer); setStatus("Consensus refreshed");
  }
  if(!va) return;
  const age = s.views_age_secs;
  if(age == null){ va.textContent = ""; return; }
  if(rb && rb.classList.contains("spin")){ va.textContent = "refreshing AI views…"; va.classList.remove("stale"); return; }
  const rem = Math.max(0, AI_REFRESH_SECS - age);
  const m = Math.floor(rem/60), sec = Math.floor(rem%60);
  va.textContent = rem <= 0 ? "refreshing AI views…" : `refreshing AI views in ${m}:${String(sec).padStart(2,"0")}`;
  va.classList.toggle("stale", age > AI_REFRESH_SECS + 300);
}

function renderStrip(s){
  renderRibbon(s.consensus || []);   // currency-strength ribbon follows the selected model
  renderKpis(s.account || {});
  renderViewsAge(s);
}

function plotEquity(series){
  const xs = series.map(p=>p.ts), ys = series.map(p=>p.equity);
  const start = ys.length? ys[0] : 1000000;
  const killLine = start * (1 - 0.20);   // 20% drawdown kill level
  const traces = [
    {x:xs, y:ys, type:"scatter", mode:"lines", name:"equity",
     line:{color:C.long, width:2}, fill:"tozeroy", fillcolor:"rgba(46,204,113,0.08)",
     hovertemplate:"%{x|%b %d %H:%M}<br>$%{y:,.0f}<extra></extra>"},
    {x:xs, y:xs.map(()=>killLine), type:"scatter", mode:"lines", name:"kill (-20%)",
     line:{color:C.short, dash:"dot", width:1}, hoverinfo:"skip"},
  ];
  // frame tightly around the EQUITY data (not the far-off kill line) so the
  // curve fills the chart and its real movement is visible. The kill line stays
  // a clipped trace that only enters view if equity actually falls toward it.
  const lo = ys.length? Math.min(...ys) : start;
  const hi = ys.length? Math.max(...ys) : start;
  const pad = (hi - lo) * 0.18 || 2000;
  Plotly.react("equity-chart", traces, {...PLOT_BG, height:210, showlegend:false,
    uirevision:"equity",
    xaxis:{gridcolor:"rgba(255,255,255,0.04)", showgrid:true, color:C.muted},
    yaxis:{side:"right", tickprefix:"$", tickformat:",.0f", color:C.muted,
      gridcolor:"rgba(255,255,255,0.06)", range:[lo - pad, hi + pad]},
    margin:{l:6, r:74, t:8, b:26}}, {displayModeBar:false});
}

let _lastGauges = [], _stagedPreset = null, _presets = {};
// implied target gross-leverage for an aggression level (survival ~2x .. finals ~6x)
const _impliedLev = a => 2 + Math.max(0, Math.min(1, a)) * 4;

function plotGauges(gauges){        // horizontal risk bars (replaces the doughnut gauges)
  _lastGauges = gauges || [];
  const el = document.getElementById("gauges");
  if(!el) return;
  el.innerHTML = _lastGauges.map(g=>{
    const max = g.max || 1;
    const clamp = v => Math.max(0, Math.min(100, (v / max) * 100));
    let from = 0; const stops = [];
    (g.bands || []).forEach(b=>{ const to = clamp(b.to);
      stops.push(`${b.color} ${from}%`, `${b.color} ${to}%`); from = to; });
    if(g.bands && g.bands.length && from < 100){
      const last = g.bands[g.bands.length - 1].color;
      stops.push(`${last} ${from}%`, `${last} 100%`);
    }
    const rev = g.key === "margin_level";   // low margin = danger -> red on the RIGHT, like the others
    const bg = stops.length ? `linear-gradient(${rev?270:90}deg,${stops.join(",")})` : "var(--lift)";
    const thr = (g.hard && g.hard[0] && g.hard[0].at != null) ? clamp(g.hard[0].at) : null;
    const v = g.value;
    const valTxt = (v == null ? "—"
      : (Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(v < 10 ? 2 : 1))) + (g.unit || "");
    let preset = "";
    if(_stagedPreset && /leverage/i.test(g.label)){
      preset = `<div class="rbar-preset" style="left:${clamp(_impliedLev(_stagedPreset.aggression))}%" `+
        `title="${_stagedPreset.name} preset target"></div>`;
    }
    // tick row: 0 (start), each band threshold, max (end) — mirrored when reversed
    const fmtT = x => (Math.abs(x) >= 1000 ? (x/1000)+"k" : Math.round(x)) + (g.unit || "");
    const tickVals = [0, ...(g.bands||[]).slice(0,-1).map(b=>b.to), max];
    const axisHTML = tickVals.map(val=>{
      let p = clamp(val); if(rev) p = 100 - p;
      const st = p <= 1 ? "left:0" : p >= 99 ? "right:0" : `left:${p}%;transform:translateX(-50%)`;
      return `<span class="rbar-tick" style="${st}">${fmtT(val)}</span>`;
    }).join("");
    return `<div class="rbar">`+
      `<div class="rbar-head"><span class="rbar-label">${g.label}</span><span class="rbar-val">${valTxt}</span></div>`+
      `<div class="rbar-track" style="background:${bg}">`+
        `<div class="rbar-needle" style="left:${rev ? 100-clamp(v) : clamp(v)}%"></div>`+
        (thr != null ? `<div class="rbar-thr" style="left:${rev ? 100-thr : thr}%"></div>` : "")+
        preset+
      `</div>`+
      `<div class="rbar-axis">${axisHTML}</div></div>`;
  }).join("");
}

function renderTripwires(tw){
  document.getElementById("tripwires").innerHTML = tw.map(t=>
    `<div class="tw ${t.active?"active":"ok"}"><span>${t.label}</span>`+
    `<span>${t.active? "⚠ ACTIVE (penalty "+t.penalty+")":"ok"}</span></div>`).join("");
}

function renderOrders(list){
  const el = document.getElementById("orders");
  if(!list || !list.length){ el.innerHTML = '<div style="color:var(--muted)">(no orders yet)</div>'; return; }
  el.innerHTML = list.map(o=>{
    const t = o.ts ? o.ts.slice(11,16) : "";
    const ts = `<span style="color:${C.muted}">${t}</span> `;
    if(o.side==="close") return `<div>${ts}<span style="color:${C.muted}">CLOSE</span> ${o.symbol}</div>`;
    const col = o.side==="buy"? C.long : C.short;
    return `<div>${ts}<span style="color:${col}">${o.side.toUpperCase()}</span> ${o.lots} ${o.symbol}</div>`;
  }).join("");
}

let _posBySym = {};   // {symbol: {lots, entry, profit, ...}} — for the instrument chart
function renderPositions(pos){
  const el = document.getElementById("positions");
  if(!el) return;
  if(!pos || !pos.available){ el.innerHTML = '<div style="color:var(--muted)">(MT5 unavailable)</div>'; return; }
  const list = pos.positions || [];
  _posBySym = {}; list.forEach(p => { _posBySym[p.symbol] = p; });   // for plotSymbol (1s pollSymbol picks it up)
  if(!list.length){ el.innerHTML = '<div style="color:var(--muted)">(flat)</div>'; return; }
  const fmtPL = v => (v>=0?"+$":"-$")+Math.abs(Math.round(v)).toLocaleString();
  const row = p=>{
    const long = p.lots > 0, col = long ? C.long : C.short;
    const prof = (p.profit == null) ? null : p.profit;
    const pl = prof==null ? "" : fmtPL(prof);
    const plcol = (prof||0) >= 0 ? C.long : C.short;
    // scalped symbols get a hook marker + an inline status sub-line (spec §6.3)
    const hook = p.scalped ? `<span class="scalp-hook" title="scalper">·s</span>` : "";
    let sub = "";
    if(p.scalped){
      const st = _scalpState[p.symbol];
      if(st){ const sig = (st.last_signal && st.last_signal.reason) || "";
        sub = `<div class="scalp-sub">⤷ ${st.state}${sig?" · "+sig:""}</div>`; }
    }
    return `<div class="pos-row" data-sym="${p.symbol}" style="display:flex;justify-content:space-between;gap:6px">`+
      `<span><span style="color:${col}">${long?"L":"S"}</span> ${Math.abs(p.lots).toFixed(2)} ${p.symbol}${hook}</span>`+
      `<span style="color:${plcol}">${pl}</span></div>${sub}`;
  };
  // engine positions first, scalped pinned at the bottom (spec §9.6)
  const eng = list.filter(p=>!p.scalped), scalp = list.filter(p=>p.scalped);
  const rows = eng.map(row).join("") + scalp.map(row).join("");
  const total = list.reduce((s,p)=> s + (p.profit || 0), 0);
  const tcol = total >= 0 ? C.long : C.short;
  const totalRow = `<div class="pos-total">`+
    `<span style="color:${C.muted}">TOTAL P/L</span>`+
    `<span style="color:${tcol}">${fmtPL(total)}</span></div>`;
  el.innerHTML = rows + totalRow;
  if(!el.dataset.hoverWired){ el.dataset.hoverWired = "1"; wirePositionHover(el); }
}

// Hover a position -> preview that symbol in the instrument chart; restore the
// selected symbol on leave. Delegated, so it survives the 1s re-render.
function wirePositionHover(el){
  // Hover only HIGHLIGHTS the row (via CSS .pos-row:hover) — it no longer
  // previews on the chart. CLICK selects the symbol on the Instrument &
  // Projection panel (chart + list + overrides stay in sync, accurate state).
  el.addEventListener("click", (e)=>{
    const r = e.target.closest(".pos-row");
    if(r && r.dataset.sym) selectSymbol(r.dataset.sym);
  });
}

function renderHistPL(realized){
  const el = document.getElementById("hist-pl");
  if(!el) return;
  if(realized == null){ el.textContent = ""; return; }
  const v = Math.round(realized);
  el.textContent = (v>=0?"+$":"-$")+Math.abs(v).toLocaleString();
  el.style.color = v >= 0 ? C.long : C.short;
}

window.renderCharts = async function(s){
  const eq = await getJSON("/api/equity");
  plotEquity((eq && eq.series) || []);
  plotGauges(s.gauges || []);
  renderTripwires(s.tripwires || []);
  renderOrders(s.recent_orders || s.orders || []);
  renderHistPL(s.realized);
  renderPositions(await getJSON("/api/positions"));
  if (window.renderConsensus) await window.renderConsensus(s);  // Task 9
};

async function tick(){
  const s = await getJSON("/api/state");
  if(!s){ return; }
  renderStrip(s);
  if (window.renderCharts) await window.renderCharts(s);  // added in Tasks 8-9
}
tick();
setInterval(() => tick(), 15000);   // late-bound: runs the fully-wrapped tick (controls + overrides refresh)

const dirColor = d => d==="long"? C.long : d==="short"? C.short : C.flat;

let _dumbTab = "fx", _lastConsensus = [], _dtabWired = false;

function wireDumbTabs(){
  if(_dtabWired) return;
  const tabs = document.querySelectorAll(".dumb-tabs .dtab");
  if(!tabs.length) return;
  tabs.forEach(btn => btn.onclick = ()=>{
    _dumbTab = btn.dataset.grp;
    tabs.forEach(b => b.classList.toggle("active", b === btn));
    plotDumbbell(_lastConsensus);
  });
  _dtabWired = true;
}

function plotDumbbell(allRows){
  // one row per instrument: up to THREE markers — Claude (orange), Nemotron (teal),
  // DeepSeek (violet) — at x = sign(direction)*confidence, models that are null are
  // skipped. A connector spans each row's present markers. Filtered to the active
  // asset-class tab (FX / Crypto / Metals). Legend names the three model colors.
  _lastConsensus = allRows || [];
  const rows = _lastConsensus.filter(r => _grpOf(r.symbol) === _dumbTab);
  const ys = rows.map(r=>r.symbol);
  const sgn = d => d==="long"?1 : d==="short"?-1 : 0;
  const mx = (r,m)=> r[m] && r[m].direction ? sgn(r[m].direction)*r[m].confidence : null;
  const cx = rows.map(r=> mx(r,"claude"));
  const nx = rows.map(r=> mx(r,"nemotron"));
  const dx = rows.map(r=> mx(r,"deepseek"));
  const lines = rows.map((r,i)=>{
    const present = [cx[i], nx[i], dx[i]].filter(v=>v!=null);
    const lo = present.length ? Math.min(...present) : 0;
    const hi = present.length ? Math.max(...present) : 0;
    // connector: dotted GREEN when the models agree (all same direction),
    // dotted RED when they disagree (mixed directions); muted if <2 models voted.
    const signs = new Set(present.filter(v=>v!==0).map(v=> v>0?1:-1));
    const col = present.length < 2 ? C.muted : (signs.size > 1 ? C.short : C.long);
    return {x:[lo, hi], y:[r.symbol,r.symbol], type:"scatter", mode:"lines",
      line:{color: col, width:2, dash:"dot"}, showlegend:false, hoverinfo:"skip"};
  });
  // Markers: when two models land on the same point, SPLIT the ball into colored
  // halves (left/right half-circle glyphs) so neither is hidden; three coinciding
  // models fall back to concentric rings. Single dots are normal circles.
  const TOL = 0.045;   // x-distance counted as "overlapping" (≈ one marker width)
  const M = [["Claude", C.claude, cx], ["Nemotron", C.nemo, nx], ["DeepSeek", C.deepseek, dx]];
  const GLYPHFONT = "Segoe UI Symbol, Arial, sans-serif";
  // single-dot tooltip: "<Model> <SYMBOL>: <dir> <conf>"
  const lbl = (m, sym, x)=> `${m} ${sym}: ${x>0?"long":x<0?"short":"flat"} ${Math.abs(x).toFixed(2)}<extra></extra>`;
  // overlap (split ball / rings) tooltip: list ALL clustered models, e.g.
  // "Claude/Nemotron XAUUSD: long 0.50" (or per-model lines if they disagree)
  const glbl = (grp, sym)=>{
    const names = grp.map(p=>p.m).join("/");
    const dirs = new Set(grp.map(p=> p.x>0?"long":p.x<0?"short":"flat"));
    if(dirs.size === 1){
      const confs = [...new Set(grp.map(p=> Math.abs(p.x).toFixed(2)))].join("/");
      return `${names} ${sym}: ${[...dirs][0]} ${confs}<extra></extra>`;
    }
    return grp.map(p=> `${p.m}: ${p.x>0?"long":p.x<0?"short":"flat"} ${Math.abs(p.x).toFixed(2)}`).join("<br>") + "<extra></extra>";
  };
  const fx = [], fy = [], fc = [], fd = [], overlap = [];   // single dots (+ customdata) + overlap traces
  rows.forEach((r, i) => {
    const pts = M.map(([m, col, arr]) => ({m, col, x: arr[i]})).filter(p => p.x != null);
    const used = pts.map(() => false);
    for(let a = 0; a < pts.length; a++){
      if(used[a]) continue;
      const grp = [pts[a]]; used[a] = true;
      for(let b = a + 1; b < pts.length; b++)
        if(!used[b] && Math.abs(pts[b].x - pts[a].x) <= TOL){ grp.push(pts[b]); used[b] = true; }
      const mxv = grp.reduce((s, p) => s + p.x, 0) / grp.length;   // shared draw position (visual merge)
      if(grp.length === 1){ fx.push(grp[0].x); fy.push(r.symbol); fc.push(grp[0].col); fd.push(lbl(grp[0].m, r.symbol, grp[0].x)); continue; }
      if(grp.length === 2){   // split the ball: both halves share the combined tooltip
        const tip = glbl(grp, r.symbol);
        ["◖", "◗"].forEach((g, gi) => overlap.push({x:[mxv], y:[r.symbol], type:"scatter", mode:"text",
          text:[g], textposition:"middle center", textfont:{color:grp[gi].col, size:19, family:GLYPHFONT},
          hovertemplate:tip, showlegend:false}));
      } else {                // 3-way: concentric rings, all show the combined tooltip
        const tip = glbl(grp, r.symbol);
        grp.forEach((p, gi) => overlap.push({x:[mxv], y:[r.symbol], type:"scatter", mode:"markers",
          marker:{color:p.col, size:[15,10,6][gi] || 6},
          hovertemplate:tip, showlegend:false}));
      }
    }
  });
  const fullDots = {x:fx, y:fy, type:"scatter", mode:"markers", marker:{color:fc, size:11},
    customdata:fd, hovertemplate:"%{customdata}", showlegend:false};
  // Legend lives in the card-head (renderHitRates) — no Plotly legend overlapping row 1.
  Plotly.react("dumbbell", [...lines, fullDots, ...overlap],
    {...PLOT_BG, height: Math.max(120, ys.length*30+36), dragmode:false,   // not pannable/zoomable
     margin:{l:66, r:14, t:10, b:30}, yaxis:{automargin:true, color:C.muted, fixedrange:true},
     xaxis:{range:[-1.05,1.05], fixedrange:true, zeroline:true, zerolinecolor:C.muted, title:{text:"short ◀  confidence  ▶ long", font:{size:9}}},
     showlegend:false},
    {displayModeBar:false, scrollZoom:false, staticPlot:false});
}

let _lastHm = null, _conWired = false;
// pick which view a heatmap cell shows, driven by its own layer buttons (_heatLayer):
// combined (used), Claude-only, Nemotron-only, or DeepSeek-only.
function _cellLayer(c){
  if(c==null) return null;
  if(_heatLayer === "claude")   return c.claude   || {direction:c.direction, confidence:c.confidence};
  if(_heatLayer === "nemotron") return c.nemotron || null;   // null when no 2nd opinion that refresh
  if(_heatLayer === "deepseek") return c.deepseek || null;   // null when DeepSeek absent that refresh
  return c.combined || {direction:c.direction, confidence:c.confidence};
}
function plotHeatmap(hm){
  _lastHm = hm;
  const lum = c => c==null? 0 : c.confidence;             // 0..1
  const sign = c => c==null? 0 : (c.direction==="long"?1 : c.direction==="short"?-1 : 0);
  // z = signed confidence (-1..1): red short, green long, dark flat — per selected layer
  const z = hm.cells.map(row=> row.map(raw=>{ const c=_cellLayer(raw);
    return c==null? null : sign(c)*Math.max(lum(c),0.05); }));
  const text = hm.cells.map(row=> row.map(raw=>{ const c=_cellLayer(raw);
    if(c==null || c.confidence==null) return "";
    return `${c.direction} ${c.confidence.toFixed(2)}${raw&&raw.boosted&&_heatLayer==="combined"?" ★":""}`; }));
  // Each refresh is ONE evenly-spaced column (a heatmap must be categorical) —
  // a continuous date axis distorts widths/gaps when refreshes are irregular
  // (15-min cadence + manual "Refresh views"). x = column index; the real
  // timestamp is shown via subset ticks + per-cell hover.
  const ts = hm.timestamps || [];
  const n = ts.length;
  const fmtT = iso => { const d = new Date(iso);   // naive ISO parsed as local -> logged time
    const p = x => String(x).padStart(2,"0");
    return `${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };
  const xi = ts.map((_, i) => i);
  const step = Math.max(1, Math.ceil(n / 10));      // ~10 readable ticks
  const tickvals = [], ticktext = [];
  for(let i = 0; i < n; i += step){ tickvals.push(i); ticktext.push(fmtT(ts[i])); }
  const ctime = hm.symbols.map(()=> ts.map(fmtT));  // real time per cell (hover)
  Plotly.react("heatmap", [{z, x:xi, y:hm.symbols, customdata:ctime,
    type:"heatmap", text, xgap:(n>60?0:1), ygap:1,
    hovertemplate:"%{y} · %{customdata}: %{text}<extra></extra>",
    colorscale:[[0,C.short],[0.5,C.canvas],[1,C.long]], zmid:0, showscale:false}],
    {...PLOT_BG, height: Math.max(160, hm.symbols.length*22+40), margin:{l:64,r:8,t:8,b:30},
     xaxis:{tickmode:"array", tickvals, ticktext, tickangle:-45, color:C.muted,
       automargin:true, range:[-0.5, Math.max(0.5, n-0.5)]},
     dragmode:"pan", uirevision:"heatmap"},
    {displayModeBar:true, displaylogo:false, scrollZoom:true,
     modeBarButtonsToRemove:["select2d","lasso2d"]});
  wireHeatmapHover(); wirePresent();
}

// Hovering a heatmap cell marks WHEN that decision was + WHICH direction on the
// price chart (when the cell's instrument is the one shown — or, in Present mode,
// the chart follows the hovered instrument).
function wireHeatmapHover(){
  const el = document.getElementById("heatmap");
  if(!el || el._hmWired || typeof el.on !== "function") return;
  el._hmWired = true;
  const clear = ()=>{ if(_hoverMark){ _hoverMark = null; plotSymbol(curSymbol, curTF, true); } };
  el.on("plotly_hover", (ev)=>{
    const p = ev.points && ev.points[0]; if(!p || !_lastHm) return;
    const col = Math.round(p.x), sym = p.y;
    const iso = (_lastHm.timestamps || [])[col];
    const ri = (_lastHm.symbols || []).indexOf(sym);
    const view = _cellLayer(ri >= 0 && _lastHm.cells[ri] ? _lastHm.cells[ri][col] : null);
    if(!iso || !view || !view.direction){ clear(); return; }
    _hoverMark = { sym, ms: new Date(iso).getTime() + _serverOffMs, dir: view.direction };
    if(_followMode && sym !== curSymbol) selectSymbol(sym);   // chart follows the hovered instrument
    else plotSymbol(curSymbol, curTF, true);                  // instant redraw from cached bars
  });
  el.on("plotly_unhover", clear);
  el.addEventListener("mouseleave", clear);
}

function wirePresent(){
  const btn = document.getElementById("ot-present");
  if(!btn || btn.dataset.w) return;
  btn.dataset.w = "1";
  btn.onclick = ()=>{
    _followMode = !_followMode;
    btn.classList.toggle("active", _followMode);
    btn.textContent = _followMode ? "Stop" : "Present";
    if(_followMode){ _followPrev = curSymbol; }
    else { _hoverMark = null; if(_followPrev) selectSymbol(_followPrev); else plotSymbol(curSymbol, curTF, true); }
  };
}

let curSymbol = null, curTF = "15m", _tfWired = false, _chartType = "line";
let _hoverMark = null, _serverOffMs = 0;       // heatmap-hover marker on the price chart
let _followMode = false, _followPrev = null;   // "Present": chart follows the hovered cell
const _barsCache = {};                          // {sym|tf: bars} for instant hover redraws (no refetch)
let _pendingTP = {}, _pendingSL = {};   // {sym: price} STAGED TP/SL (dragged, not yet Applied)
let _editLine = null;                   // "sl" | "tp" while a line edit awaits Apply/✕
let _focusSL = false, _focusTP = false; // include the SL/TP line in the y-range (locate it)
let _chartRev = 0;                      // bump to force the chart view back to default (reset zoom)
let _dragMode = "pan";                  // chart left-drag mode: "pan" (default) or "zoom"
let _refreshAtTs = null, _refreshTimer = null;   // header refresh-icon progress spinner

async function plotSymbol(symbol, tf, reuse){
  if(symbol) curSymbol = symbol;
  if(tf) curTF = tf;
  if(!curSymbol) return;
  const _bkey = curSymbol + "|" + curTF;        // reuse cached bars for instant hover redraws
  const b = (reuse && _barsCache[_bkey]) ? _barsCache[_bkey] : await getJSON(`/api/bars/${curSymbol}?tf=${curTF}`);
  if(b && b.available) _barsCache[_bkey] = b;
  const el = document.getElementById("symbol-chart");
  if(!b || !b.available){ el.innerHTML = '<div style="color:var(--muted);padding:24px">live MT5 unavailable — projection needs a running terminal</div>'; return; }
  const n = b.closes.length;
  // real timestamps for the x-axis (epoch seconds -> ms). The cones project
  // forward in BAR units, so convert their offset to time via the bar interval dt.
  const tms = (b.times || []).map(t => t * 1000);
  const lastT = tms.length ? tms[tms.length - 1] : 0;
  // bars carry BROKER-server time; heatmap decision ts is engine-local -> detect the
  // broker UTC offset empirically (newest bar ≈ now) so the hover line lands right.
  if(lastT) _serverOffMs = Math.round((lastT - Date.now()) / 3600000) * 3600000;
  const dt = tms.length > 1 ? (tms[tms.length - 1] - tms[0]) / (tms.length - 1) : 60000;
  const price = (_chartType === "candle" && b.opens && b.highs && b.lows)
    ? [{x:tms, open:b.opens, high:b.highs, low:b.lows, close:b.closes, type:"candlestick",
        name:curSymbol, showlegend:false,
        increasing:{line:{color:C.long}}, decreasing:{line:{color:C.short}}}]
    : [{x:tms, y:b.closes, type:"scatter", mode:"lines", line:{color:C.ink,width:1.5},
        name:curSymbol, hovertemplate:"%{y:,.6g}<extra></extra>"}];
  // just the projected direction LINE per model — no filled cone band (the wide
  // beam read as confusing). Mid line only.
  const coneTrace=(cone,color,name,dash)=> cone? [
    {x:cone.mid.map(p=>lastT+p[0]*dt), y:cone.mid.map(p=>p[1]), mode:"lines", line:{color:color,width:2,dash:dash||"solid"}, name:name, hovertemplate:"%{y:,.6g}<extra></extra>"},
  ] : [];
  // Consensus is drawn last (on top) and DASHED so that when no Nemotron is
  // running — consensus == Claude, perfectly overlapping — the solid Claude
  // line shows through the gaps instead of being hidden under it.
  // OPEN POSITION: the ENTRY is a fixed TRACE (not draggable). Only the SL and TP
  // are draggable SHAPES (shapes don't drive autorange, so a far SL never squashes
  // the price; the explicit y-range below keeps the view on price).
  window._lastBars = b;   // genLevel() reads recent bars to place a generated SL/TP
  const _shapes = [], posTraces = [], _annos = [];
  window._tpIdx = -1; window._slIdx = -1;
  let _slY = null, _tpY = null;
  const _p = _posBySym[curSymbol];
  if(_p && _p.entry){
    const long = _p.lots > 0, pcol = long ? C.long : C.short;
    const prof = _p.profit != null ? ((_p.profit>=0?"+$":"-$")+Math.abs(Math.round(_p.profit)).toLocaleString()) : "";
    const _px = v => (+v).toLocaleString(undefined, {maximumFractionDigits: Math.abs(v)>=100?2:5});
    const hline = (y, color, dash, text, pos) => ({type:"line", xref:"paper", x0:0, x1:1, yref:"y",
      y0:y, y1:y, line:{color, width:1.4, dash},
      label:{text, textposition:pos, font:{size:9, color, family:"JetBrains Mono"}}});
    // ENTRY: fixed line as a TRACE (not editable) + label annotation
    const coneLens = [b.cones.claude, b.cones.nemotron, b.cones.deepseek, b.cones.consensus]
      .filter(Boolean).map(c=>c.mid.length);
    const rx1 = lastT + (coneLens.length ? Math.max(...coneLens) : 1) * dt;
    posTraces.push({x:[tms[0], rx1], y:[_p.entry, _p.entry], mode:"lines", hoverinfo:"skip",
      line:{color:pcol, width:1.4, dash:"dashdot"}, showlegend:false});
    _annos.push({xref:"paper", x:0.005, y:_p.entry, yref:"y", xanchor:"left", yanchor:"bottom",
      text:`${long?"▲ LONG":"▼ SHORT"} ${Math.abs(_p.lots).toFixed(2)} @ ${_px(_p.entry)}  ${prof}`,
      showarrow:false, font:{size:9, color:pcol, family:"JetBrains Mono"}, bgcolor:"rgba(13,14,18,0.7)"});
    // STOP-LOSS line (DRAGGABLE): staged drag > manual override > broker SL
    const ovsl = _ov[curSymbol] && _ov[curSymbol].stop_loss;
    _slY = (_pendingSL[curSymbol] != null) ? _pendingSL[curSymbol]
         : (ovsl != null && ovsl > 0) ? ovsl : (_p.sl && _p.sl > 0 ? _p.sl : null);
    if(_slY != null){
      _shapes.push(hline(_slY, C.short, "dot", `SL ${_px(_slY)}${_pendingSL[curSymbol]!=null?" · Apply":""}`, "start"));
      window._slIdx = _shapes.length - 1;
    }
    // TAKE-PROFIT line (DRAGGABLE): staged drag > manual override (created via button)
    const ovtp = _ov[curSymbol] && _ov[curSymbol].take_profit;
    _tpY = (_pendingTP[curSymbol] != null) ? _pendingTP[curSymbol]
         : (ovtp != null && ovtp > 0) ? ovtp : null;
    if(_tpY != null){
      _shapes.push(hline(_tpY, C.chartreuse, "dot", `🏁 TP ${_px(_tpY)}${_pendingTP[curSymbol]!=null?" · Apply":""}`, "end"));
      window._tpIdx = _shapes.length - 1;
    }
  }
  // HEATMAP-HOVER marker: a vertical line at the hovered decision's time + a colored
  // direction arrow (long=green ▲ / short=red ▼ / flat=grey ■) at the price then.
  if(_hoverMark && _hoverMark.sym === curSymbol && lastT){
    const hms = _hoverMark.ms;
    if(hms >= tms[0] && hms <= lastT){
      let bi = 0, bd = Infinity;
      for(let i = 0; i < tms.length; i++){ const dd = Math.abs(tms[i] - hms); if(dd < bd){ bd = dd; bi = i; } }
      const hc = _hoverMark.dir === "long" ? C.long : _hoverMark.dir === "short" ? C.short : C.muted;
      const up = _hoverMark.dir === "long";
      _shapes.push({type:"line", xref:"x", x0:hms, x1:hms, yref:"paper", y0:0, y1:1,
        line:{color:hc, width:1.2, dash:"dot"}, editable:false});
      _annos.push({x:hms, xref:"x", y:b.closes[bi], yref:"y", ax:0, ay: up ? 26 : -26,
        text: up ? "▲" : (_hoverMark.dir === "short" ? "▼" : "■"), showarrow:true, arrowhead:2,
        arrowcolor:hc, arrowwidth:1.4, font:{color:hc, size:13, family:"JetBrains Mono"}});
    }
  }
  // CURRENT-PRICE line (always shown) + a price tag on the right axis
  const _lastPx = b.closes[b.closes.length - 1];
  if(_lastPx != null){
    const _coneMax = Math.max(0, ...[b.cones.claude, b.cones.nemotron, b.cones.deepseek, b.cones.consensus]
      .filter(Boolean).map(c=>c.mid.length));
    const _fmtPx = v => (+v).toLocaleString(undefined, {maximumFractionDigits: Math.abs(v)>=100?2:5});
    posTraces.push({x:[tms[0], lastT + _coneMax * dt], y:[_lastPx, _lastPx], mode:"lines",
      hoverinfo:"skip", line:{color:C.ink, width:1, dash:"dot"}, showlegend:false});
    _annos.push({xref:"paper", x:1, xanchor:"left", xshift:1, y:_lastPx, yref:"y", yanchor:"middle",
      text:` ${_fmtPx(_lastPx)} `, showarrow:false, font:{size:9, color:"#0c0d11", family:"JetBrains Mono"},
      bgcolor:C.ink, bordercolor:C.ink, borderpad:2});
  }
  // CONSENSUS line = live average of Claude + whichever 2nd opinions are SELECTED
  // (Nemotron via "Nemotron input", DeepSeek via "DeepSeek input"). Toggling either
  // recomputes it on the next ~1s render, so the line moves. Pointwise price average.
  const avgCone = (cones)=>{
    const valid = cones.filter(c=> c && c.mid && c.mid.length);
    if(!valid.length) return null;
    const m = Math.min(...valid.map(c=>c.mid.length)), mid = [];
    for(let i=0;i<m;i++) mid.push([valid[0].mid[i][0], valid.reduce((s,c)=>s+c.mid[i][1],0)/valid.length]);
    return {mid};
  };
  const _selCones = [b.cones.claude].filter(Boolean);
  if(_ctl && _ctl.second_opinion !== false && b.cones.nemotron) _selCones.push(b.cones.nemotron);
  if(_ctl && _ctl.ensemble_input && b.cones.deepseek) _selCones.push(b.cones.deepseek);
  const _consCone = avgCone(_selCones) || b.cones.consensus;
  const traces = [...price,
    ...coneTrace(b.cones.claude, C.claude, "Claude"),
    ...coneTrace(b.cones.nemotron, C.nemo, "Nemotron"),
    ...coneTrace(b.cones.deepseek, C.deepseek, "DeepSeek"),
    ...coneTrace(_consCone, C.consensus, "Consensus", "dash"),
    ...posTraces];
  // size the chart to the symbol list so the panel has no dead space below it
  const _lst = document.getElementById("symbol-list");
  const chartH = Math.max(340, _lst ? _lst.scrollHeight : 340);
  // Frame the y-axis on the PRICE (+ projection cones) only. Entry/SL/TP are
  // shapes, but we also pin the range here so a far stop-loss can never pull the
  // view out — it's still rendered, but the chart stays focused on price.
  let _lo = Math.min(...b.closes), _hi = Math.max(...b.closes);
  if(_chartType === "candle" && b.lows && b.highs){ _lo = Math.min(...b.lows); _hi = Math.max(...b.highs); }
  [b.cones.claude, b.cones.nemotron, b.cones.deepseek, b.cones.consensus].forEach(c=>{
    if(c && c.mid) c.mid.forEach(p=>{ _lo = Math.min(_lo, p[1]); _hi = Math.max(_hi, p[1]); }); });
  // pull a SL/TP line into view only when located (button) or being dragged — so a
  // far stop never expands the default frame
  if((_focusSL || _pendingSL[curSymbol] != null) && _slY != null){ _lo = Math.min(_lo, _slY); _hi = Math.max(_hi, _slY); }
  if((_focusTP || _pendingTP[curSymbol] != null) && _tpY != null){ _lo = Math.min(_lo, _tpY); _hi = Math.max(_hi, _tpY); }
  const _pad = (_hi - _lo) * 0.08 || Math.abs(_hi) * 0.001 || 0.0001;
  Plotly.react("symbol-chart", traces, {...PLOT_BG, height:chartH, showlegend:true,
    shapes:_shapes, annotations:_annos,
    hovermode:"x unified",   // one box tracking the cursor, all series' prices at that x
    hoverlabel:{bgcolor:C.surface, bordercolor:C.lift, font:{family:"JetBrains Mono", size:11, color:C.ink}},
    legend:{font:{size:9}, orientation:"h", x:0, y:1.08, bgcolor:"rgba(0,0,0,0)"},
    dragmode: _dragMode,                   // left-drag pans (default) or box-zooms
    uirevision: curSymbol + "|" + curTF + "|" + _chartRev,   // keep zoom across refreshes; reset on switch / reset-button
    margin:{l:10, r:58, t:22, b:26},       // fill the card edge-to-edge; price axis on the right
    xaxis:{type:"date", tickformat:"%H:%M", hoverformat:"%b %d  %H:%M", rangeslider:{visible:false},
      gridcolor:"rgba(255,255,255,0.04)", showgrid:true, color:C.muted, zeroline:false,
      showspikes:true, spikemode:"across", spikethickness:1, spikecolor:C.muted, spikedash:"dot", spikesnap:"cursor",
      title:{text:`${curSymbol} · ${curTF}`, font:{size:9, color:C.muted}}},
    yaxis:{side:"right", gridcolor:"rgba(255,255,255,0.06)", color:C.muted, zeroline:false,
      autorange:false, range:[_lo - _pad, _hi + _pad]}},
    {displayModeBar:false, displaylogo:false, scrollZoom:true,
     edits:{shapePosition:true}});   // scroll to zoom, drag to pan, double-click to reset;
                                      // modebar hidden so the top-right toggles own the corner
  wireLineDrag(); wireLineButtons(); wireEditConfirm();
  // keep the confirm bar glued to its line across live re-renders
  if(_editLine){
    const ec = document.getElementById("edit-confirm");
    const yy = _editLine === "tp" ? _pendingTP[curSymbol] : _pendingSL[curSymbol];
    if(ec && !ec.hidden && yy != null) positionEditConfirm(yy);
  }
}

// Dragging the SL or TP line only STAGES it; the confirm bar's ✓ commits, ✕ reverts.
// plotly_relayout gives shapes[i].y* — we match i to the SL/TP shape index. Wired once.
function wireLineDrag(){
  const el = document.getElementById("symbol-chart");
  if(!el || el._lineWired || typeof el.on !== "function") return;
  el._lineWired = true;
  el.on("plotly_relayout", (ev)=>{
    let y = null, which = null;
    for(const k in ev){ const m = k.match(/^shapes\[(\d+)\]\.y[01]$/); if(!m) continue;
      const i = +m[1];
      if(i === window._tpIdx){ which = "tp"; y = ev[k]; } else if(i === window._slIdx){ which = "sl"; y = ev[k]; } }
    if(y != null && which && curSymbol && _posBySym[curSymbol]){
      if(which === "tp") _pendingTP[curSymbol] = +y; else _pendingSL[curSymbol] = +y;
      _editLine = which; showEditConfirm(which, +y);
    }
  });
}

function showEditConfirm(which, y){
  const box = document.getElementById("edit-confirm"); if(!box) return;
  const lbl = document.getElementById("edit-confirm-label");
  if(lbl) lbl.textContent = (which === "sl" ? "Stop loss → " : "Take profit → ") + (+y).toPrecision(6);
  box.hidden = false;
  positionEditConfirm(y);
}
// Anchor the confirm bar vertically to the line it edits (data y -> chart pixel).
function positionEditConfirm(y){
  const box = document.getElementById("edit-confirm"); if(!box || box.hidden) return;
  const gd = document.getElementById("symbol-chart");
  try {
    const ya = gd && gd._fullLayout && gd._fullLayout.yaxis;
    if(ya && ya.c2l){
      const ypix = ya._offset + ya.l2p(ya.c2l(+y));
      box.style.top = Math.round(ypix) + "px";
      box.style.transform = "translate(-50%, -50%)";   // center the bar on the line
    }
  } catch(e){}
}
function hideEditConfirm(){ const b = document.getElementById("edit-confirm"); if(b) b.hidden = true; }

// ✓ saves the staged line as an override; ✕ discards it (line reverts / disappears).
function wireEditConfirm(){
  const ap = document.getElementById("edit-apply");
  if(ap && !ap.dataset.w){ ap.dataset.w = "1"; ap.onclick = ()=>{
    const sym = curSymbol; if(!sym || !_editLine) return;
    if(_editLine === "tp"){ const v = _pendingTP[sym]; postOverride(sym, {take_profit: v}).then(()=> delete _pendingTP[sym]); _focusTP = true; }
    else { const v = _pendingSL[sym]; postOverride(sym, {stop_loss: v}).then(()=> delete _pendingSL[sym]); _focusSL = true; }
    setStatus(`${sym} ${_editLine === "tp" ? "take-profit" : "stop-loss"} saved`);
    _editLine = null; hideEditConfirm(); plotSymbol(sym, curTF);
  }; }
  const cx = document.getElementById("edit-cancel");
  if(cx && !cx.dataset.w){ cx.dataset.w = "1"; cx.onclick = ()=>{
    const sym = curSymbol;
    if(_editLine === "tp") delete _pendingTP[sym]; else if(_editLine === "sl") delete _pendingSL[sym];
    _editLine = null; hideEditConfirm(); if(sym) plotSymbol(sym, curTF);
    setStatus("edit discarded");
  }; }
}

// Bottom-right buttons: create a SL/TP if none, else LOCATE it (bring into view to drag).
function wireLineButtons(){
  const slb = document.getElementById("sl-btn");
  if(slb && !slb.dataset.w){ slb.dataset.w = "1"; slb.onclick = ()=> lineButton("sl"); }
  const tpb = document.getElementById("tp-btn");
  if(tpb && !tpb.dataset.w){ tpb.dataset.w = "1"; tpb.onclick = ()=> lineButton("tp"); }
}
function lineButton(which){
  const sym = curSymbol, p = sym && _posBySym[sym];
  if(!p){ setStatus("open a position on this symbol first"); return; }
  const o = _ov[sym] || {};
  const exists = which === "tp" ? (o.take_profit != null || _pendingTP[sym] != null)
                                : (o.stop_loss != null || _pendingSL[sym] != null || (p.sl && p.sl > 0));
  const noun = which === "tp" ? "take-profit" : "stop-loss";
  if(exists){
    if(which === "tp") _focusTP = true; else _focusSL = true;        // locate
    setStatus(`${sym} ${noun} shown — drag it to move, then ✓ to save`);
  } else {
    const lvl = genLevel(which, p);
    if(which === "tp"){ _pendingTP[sym] = lvl; _focusTP = true; } else { _pendingSL[sym] = lvl; _focusSL = true; }
    _editLine = which; showEditConfirm(which, lvl);
    setStatus(`${sym} ${noun} generated — drag to adjust, ✓ to save`);
  }
  plotSymbol(sym, curTF);
}
// Generate a sensible level from the graph: SL at the recent swing extreme; TP at
// the same distance from entry (1:1), falling back to a small % when there's no SL.
function genLevel(which, p){
  const b = window._lastBars, long = p.lots > 0;
  if(which === "sl"){
    if(b){ const lows = (b.lows || b.closes).slice(-30), highs = (b.highs || b.closes).slice(-30);
      return long ? Math.min(...lows) : Math.max(...highs); }
    return p.entry * (1 + (long ? -1 : 1) * 0.01);
  }
  const slv = (p.symbol && _ov[p.symbol] && _ov[p.symbol].stop_loss) || (p.sl && p.sl > 0 ? p.sl : null);
  const dist = (slv != null ? Math.abs(p.entry - slv) : 0) || p.entry * 0.005;
  return long ? p.entry + dist : p.entry - dist;
}

// Single entry point for "show this symbol on the Instrument & Projection panel":
// redraws the chart AND syncs the panel's state (symbol-list highlight + the
// override/AI controls) so everything reflects the selected symbol. Used by both
// the instrument list and a click on a position row.
function selectSymbol(sym){
  if(!sym) return;
  _focusSL = false; _focusTP = false; _editLine = null; hideEditConfirm();   // fresh chart edit state
  plotSymbol(sym, curTF);
  const list = document.getElementById("symbol-list");
  if(list) list.querySelectorAll(".sym").forEach(x=>x.classList.toggle("active", x.dataset.sym===sym));
  renderOverride();   // Pin / conf / take-profit / AI rationale for the new symbol
  renderPinnedBar();  // highlight the active pinned chip
}

// Pinned instruments as a chip bar across the TOP of the panel; ✕ unpins.
function renderPinnedBar(){
  const el = document.getElementById("pinned-bar"); if(!el) return;
  const pins = Object.keys(_ov).filter(k => _ov[k] && _ov[k].pinned);
  el.innerHTML = pins.map(s=>`<span class="pin-chip${s===curSymbol?" active":""}" data-sym="${s}">${s}`+
    `<button class="pin-x" data-unpin="${s}" title="unpin">&times;</button></span>`).join("");
  el.querySelectorAll(".pin-chip").forEach(c=> c.onclick = (ev)=>{
    if(ev.target.classList.contains("pin-x")){ postOverride(ev.target.dataset.unpin, {pinned:false}); return; }
    selectSymbol(c.dataset.sym);
  });
}

// asset-class grouping for the symbol list (crypto kept separate from FX/metals)
const _CRYPTO = new Set(["BTCUSD","ETHUSD","SOLUSD","XRPUSD","BARUSD"]);
const _METALS = new Set(["XAGUSD","XAUUSD"]);
const _grpOf = s => _CRYPTO.has(s) ? "crypto" : _METALS.has(s) ? "metals" : "fx";
const _GRP_ORDER = {fx:0, metals:1, crypto:2};

let _symList = [];
function buildSymbolList(symbols){
  _symList = symbols || _symList;            // remembered so pin toggles can rebuild now
  const el = document.getElementById("symbol-list");
  const sig = symbols.join(",");
  const row = s => `<div class="sym${s===curSymbol?" active":""}" data-sym="${s}">`+
      `<span class="sym-name">${s}</span></div>`;
  if(el && el.dataset.sig !== sig){
    el.dataset.sig = sig;
    const sorted = [...symbols].sort((a,b)=> _GRP_ORDER[_grpOf(a)] - _GRP_ORDER[_grpOf(b)]); // stable within group
    let html = "", lastG = null;
    for(const s of sorted){
      const g = _grpOf(s);
      if(g !== lastG){ html += `<div class="sym-group">${g.toUpperCase()}</div>`; lastG = g; }
      html += row(s);
    }
    el.innerHTML = html;
    el.querySelectorAll(".sym").forEach(node=> node.onclick = ()=> selectSymbol(node.dataset.sym));
  } else if(el){
    el.querySelectorAll(".sym").forEach(x=>x.classList.toggle("active", x.dataset.sym===curSymbol));
  }
  if(!_tfWired){
    document.querySelectorAll(".tf-toggle .tf").forEach(btn=> btn.onclick = ()=>{
      document.querySelectorAll(".tf-toggle .tf").forEach(b=>b.classList.toggle("active", b===btn));
      plotSymbol(curSymbol, btn.dataset.tf);
    });
    document.querySelectorAll(".chart-type .ct").forEach(btn=> btn.onclick = ()=>{
      _chartType = btn.dataset.ct;
      document.querySelectorAll(".chart-type .ct").forEach(b=>b.classList.toggle("active", b===btn));
      plotSymbol(curSymbol, curTF);
    });
    const rst = document.getElementById("chart-reset");
    if(rst) rst.onclick = ()=>{ _chartRev++; _focusSL = false; _focusTP = false; plotSymbol(curSymbol, curTF); };
    document.querySelectorAll(".drag-mode .dm").forEach(btn=> btn.onclick = ()=>{
      _dragMode = btn.dataset.dm;
      document.querySelectorAll(".drag-mode .dm").forEach(b=>b.classList.toggle("active", b===btn));
      plotSymbol(curSymbol, curTF);
    });
    _tfWired = true;
  }
}

function wireConsensusTabs(){
  if(_conWired) return;
  const tabs = document.querySelectorAll(".con-tabs .contab[data-con]");   // exclude the Present toggle
  if(!tabs.length) return;
  const pres = document.getElementById("ot-present");
  tabs.forEach(btn=> btn.onclick = ()=>{
    const which = btn.dataset.con;
    tabs.forEach(b=>b.classList.toggle("active", b===btn));
    const lt = document.getElementById("con-latest"), ot = document.getElementById("con-overtime");
    if(lt) lt.hidden = which !== "latest";
    if(ot) ot.hidden = which !== "overtime";
    if(pres) pres.style.display = which === "overtime" ? "" : "none";   // Present only on Over time
    if(which !== "overtime" && _followMode && pres) pres.click();        // leaving -> stop Present
    if(which === "overtime"){          // render now that the pane is visible (was 0-width while hidden)
      if(_lastHm) plotHeatmap(_lastHm);
      else getJSON("/api/heatmap").then(hm => hm && plotHeatmap(hm));   // recover from a failed first fetch
      try { Plotly.Plots.resize("heatmap"); } catch(e){}
    }
  });
  if(pres) pres.style.display = "none";   // hidden until Over time is shown (default tab is Latest)
  // the consensus heatmap's OWN layer buttons (independent of the header selector)
  const layers = document.querySelectorAll(".ot-layers .otlayer");
  layers.forEach(btn=> btn.onclick = ()=> setHeatLayer(btn.dataset.layer));
  layers.forEach(b=>b.classList.toggle("active", b.dataset.layer===_heatLayer));   // init
  _conWired = true;
}

// Header selector -> RIBBON ONLY, with a smooth fade on toggle.
function setRibbonModel(layer){
  _ribbonModel = layer;
  document.querySelectorAll("#model-sel .mbtn").forEach(b=>b.classList.toggle("active", b.dataset.layer===layer));
  renderRibbon((window._lastState && window._lastState.consensus) || []);
  const rib = document.getElementById("ribbon");
  if(rib){ rib.classList.remove("ribbon-anim"); void rib.offsetWidth; rib.classList.add("ribbon-anim"); }
}

// Consensus-heatmap layer buttons -> HEATMAP ONLY.
function setHeatLayer(layer){
  _heatLayer = layer;
  document.querySelectorAll(".ot-layers .otlayer").forEach(b=>b.classList.toggle("active", b.dataset.layer===layer));
  if(_lastHm) plotHeatmap(_lastHm);
}

let _modelSelWired = false;
function wireModelSelector(){
  if(_modelSelWired) return;
  const btns = document.querySelectorAll("#model-sel .mbtn");
  if(!btns.length) return;
  btns.forEach(b=> b.onclick = ()=> setRibbonModel(b.dataset.layer));
  btns.forEach(b=>b.classList.toggle("active", b.dataset.layer===_ribbonModel));   // init -> claude
  _modelSelWired = true;
}

// per-model directional hit-rates from /api/model_scores (rate is a 0..1 fraction
// or null; the long DeepSeek model-id key is mapped to "DeepSeek").
function renderHitRates(ms, s){
  // Doubles as the dumbbell LEGEND (colored dot + model name) AND the model
  // self-grading readout (directional accuracy % once scored). A model that did
  // NOT contribute to the latest market read is dimmed + shown "off" (its
  // historical % isn't shown, so it never looks "present" when it's unavailable).
  const el = document.getElementById("hitrates"); if(!el) return;
  const rates = (ms && ms.rates) || {};
  const n = (ms && ms.n != null) ? ms.n : 0;
  const cons = (s && s.consensus) || [];
  const avail = key => key === "claude" ? true : cons.some(r => r[key] && r[key].direction);
  el.title = n
    ? `Directional accuracy over the last ${n} scored market reads — how often each model's long/short call was right (price moved its way). Dimmed = didn't vote this read.`
    : "Model color key. Directional accuracy appears after 2 market reads.";
  const dsKey = Object.keys(rates).find(k => /deepseek/i.test(k));
  const item = (cls, label, key, r) => {
    const on = avail(key);
    const tail = on ? ((r && r.rate != null) ? ` ${Math.round(r.rate * 100)}%` : "") : " off";
    return `<span class="lg${on?'':' off'}"><span class="dot ${cls}"></span>${label}<span class="acc">${tail}</span></span>`;
  };
  el.innerHTML = item("claude", "Claude", "claude", rates.claude)
    + item("nemo", "Nemotron", "nemotron", rates.nemotron)
    + item("deepseek", "DeepSeek", "deepseek", dsKey ? rates[dsKey] : null);
}
window.renderConsensus = async function(s){
  plotDumbbell(s.consensus || []);
  wireDumbTabs();
  wireConsensusTabs();
  wireModelSelector();
  renderHitRates(await getJSON("/api/model_scores"), s);
  const hm = await getJSON("/api/heatmap");
  if(hm) plotHeatmap(hm);
  const symbols = (hm && hm.symbols && hm.symbols.length) ? hm.symbols
                 : (s.consensus || []).map(r=>r.symbol);
  if(symbols.length){
    if(!curSymbol) curSymbol = symbols[0];
    buildSymbolList(symbols);
    plotSymbol(curSymbol, curTF);
  }
};

let _ctl = null;   // last-known controls

function setStatus(msg){ const e=document.getElementById("ctl-status"); if(e) e.textContent = msg; }

async function loadControls(){
  _ctl = await getJSON("/api/controls");
  if(!_ctl) return;
  const aggr = document.getElementById("aggr");
  const a = _ctl.aggression == null ? 0.30 : _ctl.aggression;
  if(aggr && document.activeElement !== aggr){ aggr.value = a; }
  const av = document.getElementById("aggr-val"); if(av) av.textContent = Number(a).toFixed(2);
  const so = document.getElementById("so-btn");
  if(so){ const on = _ctl.second_opinion !== false;   // null/true = used (default on)
    so.classList.toggle("on", on); so.textContent = on ? "Nemotron input: ON" : "Nemotron input: OFF"; }
  const ens = document.getElementById("ens-btn");
  if(ens){ const on = !!_ctl.ensemble_input;   // null/false = off (default off)
    ens.classList.toggle("on", on); ens.textContent = on ? "DeepSeek input: ON" : "DeepSeek input: OFF"; }
  renderScalpAssigned();
}

// gate verdict per symbol comes from controls.scalper_gate (persisted by the
// /api/scalper/gate backtest step); _gateRunning tracks in-flight runs.
let _gateRunning = {};
function _scalpGate(sym){ return ((_ctl && _ctl.scalper_gate) || {})[sym] || null; }

// Arm/disarm a SINGLE symbol (per-symbol arming: toggling one never touches the
// others). Force-confirm if it hasn't cleared the gate.
function toggleArm(sym){
  const armed = new Set((_ctl && _ctl.scalper_armed) || []);
  const forced = new Set((_ctl && _ctl.scalper_armed_force) || []);
  if(armed.has(sym)){                       // disarm -> back to dry-run
    armed.delete(sym); forced.delete(sym);
    postControls({scalper_armed:[...armed], scalper_armed_force:[...forced]})
      .then(()=> setStatus(`${sym} disarmed (dry-run)`));
    return;
  }
  if((_scalpGate(sym) || {}).cleared){
    armed.add(sym);
    postControls({scalper_armed:[...armed]}).then(()=> setStatus(`${sym} ARMED (live) — gate-cleared`));
  } else if(confirm(`${sym} has NOT cleared the backtest gate. Force-arm anyway (unvalidated)?`)){
    armed.add(sym); forced.add(sym);
    postControls({scalper_armed:[...armed], scalper_armed_force:[...forced]})
      .then(()=> setStatus(`${sym} FORCE-ARMED (unvalidated)`));
  }
}

async function runGate(sym){     // the "backtest as a step" when a symbol is assigned
  _gateRunning[sym] = true; renderScalpAssigned();
  try {
    const r = await getJSON("/api/scalper/gate?symbol=" + encodeURIComponent(sym));
    if(r && r.arm) setStatus(`${sym} backtest gate: clears ✓ — ready to arm`);
    else setStatus(`${sym} backtest gate: no edge — ${(r && r.reason) || "stays dry-run"}`);
  } catch(e){
    setStatus(`${sym} backtest gate failed — ${e}`);
  } finally {
    _gateRunning[sym] = false; await loadControls();   // loadControls re-renders the chip
  }
}

function renderScalpAssigned(){
  const el = document.getElementById("scalp-assigned"); if(!el) return;
  const syms = (_ctl && _ctl.scalper_symbols) || [];
  if(!syms.length){ el.innerHTML = '<span style="color:var(--muted);font-size:10px">none assigned (engine trades all)</span>'; return; }
  const armed = new Set((_ctl && _ctl.scalper_armed) || []);
  const forced = new Set((_ctl && _ctl.scalper_armed_force) || []);
  el.innerHTML = syms.map(s=>{
    const g = _scalpGate(s);
    let verdict, cls;
    if(_gateRunning[s]){ verdict = "backtesting…"; cls = "g-run"; }
    else if(!g){ verdict = "no backtest yet"; cls = "g-none"; }
    else if(g.cleared){ verdict = `clears ✓ (Sh ${(g.test&&g.test.sharpe!=null)?g.test.sharpe:"?"})`; cls = "g-ok"; }
    else { verdict = "no edge — " + (g.reason || "dormant"); cls = "g-no"; }
    // per-symbol Arm button
    const isArmed = armed.has(s);
    const armLabel = isArmed ? (forced.has(s) ? "Armed (forced)" : "Armed (live)")
                             : ((g && g.cleared) ? "Arm" : "Arm (force)");
    const armCls = isArmed ? " on" : "";
    const dis = _gateRunning[s] ? " disabled" : "";
    return `<div class="scalp-row">`+
      `<div class="scalp-row-top">`+
        `<span class="scalp-chip">${s}<button class="scalp-x" data-sym="${s}">&times;</button></span>`+
        `<span class="scalp-gate ${cls}">${verdict}</span>`+
        `<button class="scalp-arm1${armCls}" data-arm="${s}"${dis}>${armLabel}</button></div>`+
      `<div class="scalp-live" data-live="${s}"></div></div>`;
  }).join("");
  el.querySelectorAll(".scalp-x").forEach(b=> b.onclick = ()=>{
    const cur = ((_ctl && _ctl.scalper_symbols) || []).filter(x=>x!==b.dataset.sym);
    postControls({scalper_symbols: cur}); });
  el.querySelectorAll(".scalp-arm1").forEach(b=> b.onclick = ()=> toggleArm(b.dataset.arm));
  updateScalpLive();
}

// Live per-symbol scalper state (watching / in_position / cooldown · reason),
// refreshed on the 1s positions poll WITHOUT rebuilding the buttons.
function updateScalpLive(){
  document.querySelectorAll("[data-live]").forEach(node=>{
    const st = _scalpState[node.dataset.live];
    if(!st || !st.state){ node.textContent = ""; return; }
    const reason = (st.last_signal && st.last_signal.reason) || "";
    node.textContent = `⤷ ${st.state}${reason ? " · " + reason : ""}`;
  });
}

async function postControls(patch){
  _ctl = await getJSON("/api/controls");  // refresh base first
  const r = await fetch("/api/controls", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(patch)});
  _ctl = await r.json(); await loadControls(); setStatus("applied · "+new Date().toLocaleTimeString());
}

async function loadPresets(){
  const p = await getJSON("/api/presets"); if(!p) return;
  _presets = p.presets || {};
  const sel = document.getElementById("preset-select"); if(!sel) return;
  const names = Object.keys(_presets);
  const sig = names.join(",");
  if(sel.dataset.sig !== sig){            // (re)build options only when the set changes
    sel.dataset.sig = sig;
    sel.innerHTML = names.map(n=>`<option>${n}</option>`).join("");
  }
  // reflect the APPLIED preset, but never clobber a selection the user is staging
  if(!_stagedPreset && document.activeElement !== sel) sel.value = p.active;
}

function wireControls(){
  const aggr = document.getElementById("aggr");
  if(aggr && !aggr.dataset.w){ aggr.dataset.w="1";
    aggr.oninput = ()=>{ document.getElementById("aggr-val").textContent = Number(aggr.value).toFixed(2); };
    aggr.onchange = ()=> postControls({aggression: parseFloat(aggr.value)});
  }
  const so = document.getElementById("so-btn");
  if(so && !so.dataset.w){ so.dataset.w="1"; so.onclick = ()=>{
    const on = _ctl && _ctl.second_opinion !== false;   // currently on -> turn off, and vice versa
    postControls({second_opinion: !on}).then(()=> setStatus(
      "Nemotron input "+(!on ? "ON" : "OFF")+" — applies at next AI refresh (still recorded)")); }; }
  const ens = document.getElementById("ens-btn");
  if(ens && !ens.dataset.w){ ens.dataset.w="1"; ens.onclick = ()=>{
    const on = !!(_ctl && _ctl.ensemble_input);   // currently on -> turn off, and vice versa
    postControls({ensemble_input: !on}).then(()=> setStatus(
      "DeepSeek input "+(!on ? "ON" : "OFF")+" — applies at next AI refresh (still recorded)")); }; }
  const ssel = document.getElementById("scalp-sym-sel");
  if(ssel && !ssel.dataset.w){ ssel.dataset.w="1";
    getJSON("/api/symbols").then(r=>{ if(r && r.symbols)
      ssel.innerHTML = r.symbols.map(s=>`<option>${s}</option>`).join(""); }); }
  const sasg = document.getElementById("scalp-assign");
  if(sasg && !sasg.dataset.w){ sasg.dataset.w="1"; sasg.onclick = async ()=>{
    const sel = document.getElementById("scalp-sym-sel"); if(!sel || !sel.value) return;
    const sym = sel.value;
    const cur = new Set((_ctl && _ctl.scalper_symbols) || []);
    if(cur.size >= 2 && !cur.has(sym)){ setStatus("max 2 scalped symbols"); return; }
    cur.add(sym);
    await postControls({scalper_symbols: [...cur]});
    setStatus(sym+" -> scalper (ceded from engine) — running backtest gate…");
    runGate(sym);   // backtest-as-a-step: gate the newly assigned symbol
  }; }
  // arming is per-symbol now (buttons rendered in #scalp-assigned, wired in
  // renderScalpAssigned -> toggleArm) — no global Arm button.
  const rb = document.getElementById("refresh-btn");
  if(rb && !rb.dataset.w){ rb.dataset.w="1"; rb.onclick = async ()=>{
    await fetch("/api/refresh", {method:"POST"});
    _refreshAtTs = (window._lastState && window._lastState.ts) || "";   // baseline; spinner stops on a newer ts
    rb.classList.add("spin");
    clearTimeout(_refreshTimer); _refreshTimer = setTimeout(()=> rb.classList.remove("spin"), 200000);
    setStatus("Consensus refresh requested — re-querying AI…"); }; }
  const fb = document.getElementById("flatten-btn");
  if(fb && !fb.dataset.w){ fb.dataset.w="1"; fb.onclick = async ()=>{
    if(!confirm("Flatten ALL positions now?")) return;
    await fetch("/api/flatten", {method:"POST"}); setStatus("FLATTEN sent · "+new Date().toLocaleTimeString());
  }; }
  const ps = document.getElementById("preset-select");
  if(ps && !ps.dataset.w){ ps.dataset.w="1"; ps.onchange = ()=>{
    const pr = _presets[ps.value] || {};
    const a = pr.aggression == null ? 0.30 : pr.aggression;
    _stagedPreset = {name: ps.value, aggression: a};
    const posture = a < 0.34 ? "diversified · low risk" : a < 0.67 ? "balanced" : "concentrated · high risk";
    setStatus(`${ps.value} staged — aggr ${a.toFixed(2)} · ~${_impliedLev(a).toFixed(1)}x target lev · ${posture} — press Apply`);
    plotGauges(_lastGauges);   // re-render the risk bars with the staged preset's marker
  }; }
  const pa = document.getElementById("preset-apply");
  if(pa && !pa.dataset.w){ pa.dataset.w="1"; pa.onclick = async ()=>{
    const ps2 = document.getElementById("preset-select");
    await fetch("/api/presets/apply", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name: ps2.value})});
    await loadControls(); _stagedPreset = null; plotGauges(_lastGauges);
    setStatus("preset "+ps2.value+" applied"); }; }
  const sv = document.getElementById("preset-save");
  if(sv && !sv.dataset.w){ sv.dataset.w="1"; sv.onclick = async ()=>{
    const name = prompt("Save current knobs as preset:"); if(!name) return;
    await fetch("/api/presets/save", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({name})}); await loadPresets(); setStatus("saved preset "+name); }; }
}

// hook controls into the existing 15s loop
const _origTick = tick;
tick = async function(){ await _origTick(); wireControls(); await loadControls(); await loadPresets(); };
function openRegimeModal(){
  const r = (window._lastState && window._lastState.regime) || {};
  const c = document.getElementById("rm-claude"); if(c) c.textContent = r.claude || "(no market read logged yet)";
  const nm = document.getElementById("rm-nemo"); if(nm) nm.textContent = r.nemotron || "(Nemotron unavailable — Claude-only)";
  const ds = document.getElementById("rm-deepseek"); if(ds) ds.textContent = r.deepseek || "(DeepSeek unavailable)";
  const m = document.getElementById("regime-modal"); if(m) m.hidden = false;
}
function wireRegimeModal(){
  const reg = document.getElementById("market-read-btn");
  if(reg && !reg.dataset.w){ reg.dataset.w="1"; reg.title="click for full reasoning"; reg.onclick = openRegimeModal; }
  const m = document.getElementById("regime-modal");
  if(m && !m.dataset.w){ m.dataset.w="1";
    m.onclick = (e)=>{ if(e.target===m || e.target.id==="regime-close") m.hidden=true; };
    document.addEventListener("keydown", (e)=>{ if(e.key==="Escape") m.hidden=true; });
  }
}
let _histSym = null, _navWired = false;
function showView(name){
  ["dashboard","history","context"].forEach(v=>{
    const el = document.getElementById("view-"+v); if(el) el.hidden = v !== name; });
  const nm = document.getElementById("navmenu"); if(nm) nm.hidden = true;
  document.querySelectorAll(".navitem").forEach(b=>b.classList.toggle("active", b.dataset.view===name));
  if(name === "history") initHistory();
  if(name === "context") initContext();
}

function wireNav(){
  if(_navWired) return;
  const h = document.getElementById("hamburger");
  if(h) h.onclick = (e)=>{ e.stopPropagation(); const nm=document.getElementById("navmenu"); if(nm) nm.hidden = !nm.hidden; };
  document.querySelectorAll(".navitem").forEach(b=> b.onclick = ()=> showView(b.dataset.view));
  const brand = document.querySelector("#strip .brand");
  if(brand){ brand.title = "back to dashboard"; brand.onclick = ()=> showView("dashboard"); }
  const more = document.getElementById("orders-more"); if(more) more.onclick = ()=> showView("history");
  const back = document.getElementById("hist-back"); if(back) back.onclick = ()=> showView("dashboard");
  const sel = document.getElementById("hist-symbol"); if(sel) sel.onchange = ()=>{ _histSym = sel.value; renderHistory(_histSym); };
  document.addEventListener("click", (e)=>{ const nm=document.getElementById("navmenu");
    if(nm && !nm.hidden && e.target.id!=="hamburger" && !nm.contains(e.target)) nm.hidden = true; });
  _navWired = true;
}

// Context Control — informational page: the EXACT LLM input (SYSTEM prompt +
// per-instrument snapshot) the engine feeds every AI-views refresh. Read-only.
let _ctxTimer = null, _ctxWired = false;
function _esc(s){ return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function renderContext(d){
  if(!d) return;
  const set = (id, t)=>{ const e = document.getElementById(id); if(e) e.textContent = t; };
  set("ctx-system", d.system || "");
  set("ctx-user", (d.user_template || "").replace("{N}", (d.symbols || []).length));
  const cad = document.getElementById("ctx-cadence"); if(cad && d.cadence_secs) cad.textContent = Math.round(d.cadence_secs/60) + " min";
  const meta = document.getElementById("ctx-meta");
  if(meta) meta.innerHTML = d.available
    ? `model <b>${_esc(d.model)}</b> · ${(d.symbols||[]).length} instruments · snapshot ${_esc(d.ts||"")}`
    : `<span class="ctx-warn">${_esc(d.note||"live snapshot unavailable")}</span>`;
  const raw = document.getElementById("ctx-raw");
  if(raw){
    if(d.available && d.raw){
      raw.innerHTML = d.raw.split("\n").filter(Boolean).map(l=>{
        const sym = (l.split(":",1)[0]||"").trim();
        return `<div class="ctx-line ${_grpOf(sym)}">${_esc(l)}</div>`; }).join("");
    } else {
      raw.innerHTML = `<div class="ctx-line">${_esc(d.example_line||"")}</div>`
        + `<div class="ctx-note">(example — ${_esc(d.note||"live snapshot needs the running engine")})</div>`;
    }
  }
  const tbl = document.getElementById("ctx-table");
  if(tbl){
    const rows = d.rows || [];
    if(rows.length){
      const tr = r => `<tr><td>${_esc(r.symbol)}</td><td>${_esc(r.px)}</td>`
        + ["1h","4h","1d","3d"].map(k=>`<td>${_esc((r.ret||{})[k])}</td>`).join("")
        + ["M15","H1","H4"].map(k=>`<td class="${(r.trend||{})[k]==='up'?'dir-long':'dir-short'}">${_esc((r.trend||{})[k])}</td>`).join("")
        + `<td>${_esc(r.mom_z)}</td><td>${_esc(r.vol_bar)}</td><td>${_esc(r.range_5d)}</td></tr>`;
      tbl.innerHTML = `<table class="ctx-tbl"><thead><tr><th>Sym</th><th>px</th><th>1h</th><th>4h</th><th>1d</th><th>3d</th>`
        + `<th>M15</th><th>H1</th><th>H4</th><th>mom_z</th><th>vol/bar</th><th>5d-rng</th></tr></thead><tbody>`
        + rows.map(tr).join("") + `</tbody></table>`;
    } else { tbl.innerHTML = `<div class="ctx-note">the field table needs a live snapshot (engine running)</div>`; }
  }
  const fr = document.getElementById("ctx-fields");
  if(fr) fr.innerHTML = (d.field_reference||[]).map(f=>`<div class="ctx-fr"><code>${_esc(f.field)}</code> <span>${_esc(f.desc)}</span></div>`).join("");
  const nf = document.getElementById("ctx-notfed");
  if(nf) nf.innerHTML = (d.not_fed||[]).map(x=>`<div class="ctx-nf">✕ ${_esc(x)}</div>`).join("");
}
function _ctxSetView(v){
  document.querySelectorAll(".ctx-tab").forEach(b=>b.classList.toggle("active", b.dataset.ctxv===v));
  const raw = document.getElementById("ctx-raw"), tbl = document.getElementById("ctx-table");
  if(raw) raw.hidden = v !== "raw";
  if(tbl) tbl.hidden = v !== "table";
}
async function initContext(){
  if(!_ctxWired){
    _ctxWired = true;
    document.querySelectorAll(".ctx-tab").forEach(b=> b.onclick = ()=> _ctxSetView(b.dataset.ctxv));
    const back = document.getElementById("ctx-back"); if(back) back.onclick = ()=> showView("dashboard");
  }
  renderContext(await getJSON("/api/context"));
  clearInterval(_ctxTimer);
  _ctxTimer = setInterval(async ()=>{           // live-ish refresh only while the page is open
    const v = document.getElementById("view-context");
    if(v && !v.hidden) renderContext(await getJSON("/api/context"));
    else { clearInterval(_ctxTimer); _ctxTimer = null; }
  }, 30000);
}
async function initHistory(){
  const d = await getJSON("/api/decisions");
  const recs = (d && d.records) || [];
  const syms = [];
  for(const rec of recs) for(const v of (rec.views||[])) if(!syms.includes(v.symbol)) syms.push(v.symbol);
  const sel = document.getElementById("hist-symbol");
  if(sel){ const cur = _histSym || syms[0] || "";
    sel.innerHTML = syms.map(s=>`<option${s===cur?" selected":""}>${s}</option>`).join("");
    _histSym = cur; }
  if(_histSym) renderHistory(_histSym);
}
async function renderHistory(sym){
  if(!sym) return;
  const d = await getJSON("/api/decisions");
  const recs = ((d && d.records) || []).filter(r=>r.event!=="refresh_failed");
  const sgn = dir => dir==="long"?1:dir==="short"?-1:0;
  // entries split by direction: green diamond = LONG (buy), red diamond = SHORT
  // (sell); closes are a neutral grey x so they don't read as a red sell.
  const xs=[], ys=[], lX=[], lY=[], lT=[], sX=[], sY=[], sT=[], cX=[], cY=[], cT=[];
  for(const rec of recs){
    const v = (rec.views||[]).find(x=>x.symbol===sym);
    if(v){ xs.push(rec.ts); ys.push(sgn(v.direction)*(v.confidence||0)); }
    for(const o of (rec.orders||[])) if(o.symbol===sym){
      const lots = o.lots||"";
      if(o.side==="close"){ cX.push(rec.ts); cY.push(0); cT.push("CLOSE "+lots); }
      else if(o.side==="buy"){ lX.push(rec.ts); lY.push(0); lT.push("LONG · BUY "+lots); }
      else { sX.push(rec.ts); sY.push(0); sT.push("SHORT · SELL "+lots); }
    }
  }
  const traces=[
    {x:xs, y:ys, type:"scatter", mode:"lines+markers", line:{color:C.chartreuse,width:2}, marker:{size:5},
     name:"stance", hovertemplate:"%{x|%b %d %H:%M}<br>%{y:.2f}<extra></extra>"},
    {x:lX, y:lY, type:"scatter", mode:"markers", marker:{color:C.long,size:11,symbol:"diamond",line:{color:C.ink,width:1}},
     text:lT, name:"long (buy)", hovertemplate:"%{x|%b %d %H:%M}<br>%{text}<extra></extra>"},
    {x:sX, y:sY, type:"scatter", mode:"markers", marker:{color:C.short,size:11,symbol:"diamond",line:{color:C.ink,width:1}},
     text:sT, name:"short (sell)", hovertemplate:"%{x|%b %d %H:%M}<br>%{text}<extra></extra>"},
    {x:cX, y:cY, type:"scatter", mode:"markers",
     marker:{color:C.muted,size:11,symbol:"x",line:{color:C.ink,width:1}},
     text:cT, name:"closes", hovertemplate:"%{x|%b %d %H:%M}<br>%{text}<extra></extra>"},
  ];
  Plotly.react("hist-chart", traces, {...PLOT_BG, height:440, hovermode:"x unified", showlegend:true,
    legend:{orientation:"h", x:0, y:1.08, font:{size:10,color:C.muted}, bgcolor:"rgba(0,0,0,0)"},
    yaxis:{range:[-1.05,1.05], color:C.muted, gridcolor:"rgba(255,255,255,0.06)", zeroline:true, zerolinecolor:C.muted,
      title:{text:"short ◀  confidence  ▶ long", font:{size:9}}},
    xaxis:{type:"date", color:C.muted, gridcolor:"rgba(255,255,255,0.04)", tickangle:-30, automargin:true}},
    {displayModeBar:true, displaylogo:false, scrollZoom:true, modeBarButtonsToRemove:["select2d","lasso2d"]});
  const el = document.getElementById("hist-orders"); if(!el) return;
  const items=[];
  for(const rec of [...recs].reverse()){
    for(const o of (rec.orders||[])) if(o.symbol===sym){
      const t = rec.ts ? rec.ts.slice(5,16).replace("T"," ") : "";
      const side=(o.side||"").toUpperCase();
      const col = o.side==="close"?C.muted:(o.side==="buy"?C.long:C.short);
      const dir = o.side==="close" ? "" : (o.side==="buy"?" · LONG":" · SHORT");
      items.push(`<div><span style="color:var(--muted)">${t}</span> <span style="color:${col}">${side}</span> `+
        `${o.lots||""} ${o.symbol}<span style="color:${col};font-size:9px;opacity:.8">${dir}</span></div>`);
    }
    if(items.length>=40) break;
  }
  el.innerHTML = items.length ? items.join("") : '<div style="color:var(--muted)">(no orders for '+sym+' yet)</div>';
}
// Risk popover: hover the status bar to reveal it, click to pin it open, ✕ or a
// click outside to close.
let _riskWired = false;
function wireRiskPop(){
  if(_riskWired) return;
  const trig = document.getElementById("kpis"), pop = document.getElementById("gauges-card");
  if(!trig || !pop) return;
  _riskWired = true;
  let pinned = false;
  const show = ()=> pop.classList.add("show");
  const maybeHide = ()=>{ if(!pinned) pop.classList.remove("show"); };
  const close = ()=>{ pinned = false; pop.classList.remove("show"); };
  trig.addEventListener("mouseenter", show);
  trig.addEventListener("mouseleave", maybeHide);
  pop.addEventListener("mouseenter", show);
  pop.addEventListener("mouseleave", maybeHide);
  trig.addEventListener("click", (e)=>{ e.stopPropagation(); pinned = !pinned; show(); });
  const x = document.getElementById("risk-close"); if(x) x.onclick = (e)=>{ e.stopPropagation(); close(); };
  document.addEventListener("click", (e)=>{ if(pinned && !pop.contains(e.target) && !trig.contains(e.target)) close(); });
}

loadControls(); loadPresets(); wireControls(); wireRegimeModal(); wireNav(); wireRiskPop();

let _ov = {};   // last-known overrides {sym:{pinned,view,take_profit,note}}

async function loadOverrides(){ _ov = (await getJSON("/api/overrides")) || {}; renderOverride(); renderPinnedBar(); }

function renderOverride(){
  const sym = curSymbol; if(!sym) return;
  const o = _ov[sym] || {};
  const pin = document.getElementById("ov-pin");
  if(pin){ const on = !!o.pinned; pin.classList.toggle("on", on); pin.innerHTML = on ? "📌 Pinned" : "📌 Pin"; }
  const dir = document.getElementById("ov-dir");
  if(dir && document.activeElement !== dir) dir.value = (o.view && o.view.direction) || "";
  const conf = document.getElementById("ov-conf");
  if(conf && document.activeElement !== conf) conf.value = (o.view && o.view.confidence != null) ? o.view.confidence : "";
  // AI rationale for this symbol from the latest state
  // Cancel only SHOWS when a human override is actively overriding
  const cancel = document.getElementById("ov-cancel");
  if(cancel) cancel.style.display = (o.view && o.view.direction) ? "" : "none";
  // readout: each model's DIRECTION + confidence; + DeepSeek; + Human when overridden
  const ai = document.getElementById("ov-ai");
  if(ai && window._lastState){
    const v = (window._lastState.consensus || []).find(r=>r.symbol===sym);
    if(!v){ ai.innerHTML = ""; }
    else {
      const dir = d => `<span class="dir-${d}">${d}</span>`;   // long=green, short=red, flat=grey
      const m = (label, cls, mv) => mv && mv.direction
        ? `<span class="${cls}">${label}:</span> ${dir(mv.direction)} ${mv.confidence != null ? Number(mv.confidence).toFixed(2) : "—"}`
        : `<span class="${cls}">${label}:</span> —`;
      // only surface a clean agree/disagree; hide raw internal tags (nemotron_flat,
      // second_opinion_unavailable, human_override — the per-model rows already tell the story)
      const agree = v.agreement === "agree" ? `<span class="dir-long">agree</span>`
                  : v.agreement === "disagree" ? `<span class="dir-short">disagree</span>` : "";
      const models = [m("Claude", "claude", v.claude), m("Nemotron", "nemo", v.nemotron),
                      m("DeepSeek", "deepseek", v.deepseek)].concat(agree ? [agree] : []).join(" · ");
      const hv = (o.view && o.view.direction) ? o.view : null;
      const forced = hv && (hv.mode || "override") === "override";   // override strikes the models; additive just adds
      let html = forced ? `<span class="ov-struck">${models}</span>` : models;
      if(hv) html += ` · <span class="human">${forced ? "Human (force):" : "+ Human:"}</span> ${dir(hv.direction)}`
        + `${hv.confidence != null ? " " + Number(hv.confidence).toFixed(2) : ""}`;
      ai.innerHTML = html;
    }
  }
}

async function postOverride(sym, patch){
  await fetch(`/api/overrides/${sym}`, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(patch)});
  await loadOverrides();
}

function wireOverride(){
  const pin = document.getElementById("ov-pin");
  if(pin && !pin.dataset.w){ pin.dataset.w="1"; pin.onclick=()=>{ const sym = curSymbol; if(!sym) return;
    postOverride(sym, {pinned: !(_ov[sym] && _ov[sym].pinned)}); }; }   // loadOverrides re-renders the bar
  // Human input — Apply = ADDITIVE (boosts AI confidence when it agrees, never flips);
  // Override = forces the direction. Both sized + risk-capped like an AI trade.
  // Cancel clears back to the AI view. No auto-apply on dropdown change.
  const postHuman = (mode)=>{
    const sym = curSymbol; if(!sym) return;
    const dir = document.getElementById("ov-dir").value;
    const conf = parseFloat(document.getElementById("ov-conf").value);
    if(!dir){ setStatus("pick a direction (Force LONG/SHORT) first"); return; }
    const cc = isNaN(conf) ? 1.0 : conf;   // full conviction unless a value is given
    postOverride(sym, {view: {direction: dir, confidence: cc, mode}}).then(()=> setStatus(
      mode === "additive"
        ? `${sym} human APPLIED (additive boost): ${dir.toUpperCase()} @ ${cc}`
        : `${sym} HUMAN OVERRIDE (forced): ${dir.toUpperCase()} @ ${cc} — within the preset's risk caps`));
  };
  const add = document.getElementById("ov-add");
  if(add && !add.dataset.w){ add.dataset.w="1"; add.onclick=()=> postHuman("additive"); }
  const ov = document.getElementById("ov-override");
  if(ov && !ov.dataset.w){ ov.dataset.w="1"; ov.onclick=()=> postHuman("override"); }
  const cancel = document.getElementById("ov-cancel");
  if(cancel && !cancel.dataset.w){ cancel.dataset.w="1"; cancel.onclick=()=>{
    const sym = curSymbol; if(!sym) return;
    postOverride(sym, {view: null}).then(()=> setStatus(`${sym} human input cleared — back to AI view`));
  }; }
}

// capture last state for the AI-rationale line, and hook into the loop
const _origRenderStrip = renderStrip;
renderStrip = function(s){ window._lastState = s; _origRenderStrip(s); };
const _tick2 = tick;
tick = async function(){ await _tick2(); await loadOverrides(); wireOverride(); };
loadOverrides(); wireOverride();

// AI refresh log — the engine's "AI views refreshed" output (regime + per-symbol
// views + orders with close reasons) surfaced in the page as a scrollable feed.
function _aiLogEntry(r){
  const t = r.ts ? r.ts.slice(5, 16).replace("T", " ") : "";
  const reg = (r.regime && r.regime.claude) || "";
  // total $ cost of this refresh (sum of each model's captured usd)
  let usd = 0; const cost = r.cost || {};
  for(const k in cost){ if(cost[k] && typeof cost[k].usd === "number") usd += cost[k].usd; }
  const costTxt = usd > 0 ? ` · <span class="al-cost">$${usd.toFixed(4)}</span>` : "";
  const bySym = {}; (r.views || []).forEach(v => bySym[v.symbol] = v);
  const views = (r.views || []).filter(v => v.direction && v.direction !== "flat")
    .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
    .map(v => `<span class="al-v ${v.direction}">${v.symbol} ${v.direction} ${Number(v.confidence || 0).toFixed(2)}</span>`)
    .join("  ");
  const orders = (r.orders || []).map(o => {
    const cls = o.side === "close" ? "muted" : (o.side === "buy" ? "long" : "short");
    let reason = "";
    if(o.side === "close"){ const v = bySym[o.symbol]; const rat = v && v.claude && v.claude.rationale;
      reason = rat ? ` · ${rat}` : ""; }
    return `<div class="al-o ${cls}">${(o.side || "").toUpperCase()} ${o.lots || ""} ${o.symbol}${reason}</div>`;
  }).join("");
  return `<div class="al-entry"><div class="al-head"><span class="al-ts">${t}</span>${costTxt} · <span class="al-reg">${reg}</span></div>`
    + (views ? `<div class="al-views">${views}</div>` : "")
    + (orders ? `<div class="al-orders">${orders}</div>` : "")
    + `</div>`;
}
function renderAiLog(records){
  const recs = (records || []).filter(r => r.event !== "refresh_failed" && (r.regime || (r.views && r.views.length)));
  const bar = document.getElementById("ai-log-bar");
  if(bar){ const last = recs[recs.length - 1];
    bar.textContent = last ? ("AI · " + ((last.regime && last.regime.claude) || "views refreshed")) : "AI log"; }
  const feed = document.getElementById("ai-log-feed");
  if(feed) feed.innerHTML = recs.length ? [...recs].reverse().slice(0, 80).map(_aiLogEntry).join("")
                                        : '<div style="color:var(--muted)">no AI refreshes logged yet</div>';
}
async function loadAiLog(){ try { renderAiLog(((await getJSON("/api/decisions")) || {}).records); } catch(e){} }
let _aiLogWired = false;
function wireAiLog(){
  if(_aiLogWired) return;
  const bar = document.getElementById("ai-log-bar"), pop = document.getElementById("ai-log-pop");
  if(!bar || !pop) return;
  _aiLogWired = true;
  bar.addEventListener("click", (e)=>{ e.stopPropagation(); pop.hidden = !pop.hidden; });
  const x = document.getElementById("ai-log-close"); if(x) x.onclick = (e)=>{ e.stopPropagation(); pop.hidden = true; };
  document.addEventListener("click", (e)=>{ if(!pop.hidden && !pop.contains(e.target) && e.target !== bar) pop.hidden = true; });
}
loadAiLog(); wireAiLog();
const _tickAi = tick;
tick = async function(){ await _tickAi(); await loadAiLog(); };

// Positions refresh on a fast ~1s cadence (live P&L), independent of the 15s
// tick. Self-scheduling (waits for each fetch) so requests never pile up.
// Also pulls per-symbol scalper status for the inline ·s sub-lines (spec §6.3).
let _scalpState = {};
(async function pollPositions(){
  // Only poll the scalper status when a symbol is actually assigned — otherwise
  // it's pointless traffic (the scalper isn't running with nothing assigned).
  try {
    if(_ctl && (_ctl.scalper_symbols || []).length){
      const sc = await getJSON("/api/scalper");
      _scalpState = {}; ((sc && sc.symbols) || []).forEach(s => { _scalpState[s.symbol] = s; });
    } else {
      _scalpState = {};
    }
    updateScalpLive();   // refresh the per-symbol live state under each assigned chip
  } catch(e){}
  try { renderPositions(await getJSON("/api/positions")); } catch(e){}
  setTimeout(pollPositions, 1000);
})();

// Instrument chart refreshes live on the same ~1s cadence (price keeps moving;
// zoom is preserved via uirevision). Tracks whatever symbol is selected/previewed.
(async function pollSymbol(){
  try { if(curSymbol) await plotSymbol(curSymbol, curTF); } catch(e){}
  setTimeout(pollSymbol, 1000);
})();

// Status bar (KPIs) + the "refreshing in M:SS" countdown update every snapshot (~1s),
// not just on the 15s tick. Lightweight: no ribbon/consensus re-render here.
(async function pollState(){
  try { const s = await getJSON("/api/state");
    if(s){ window._lastState = s; renderKpis(s.account || {}); renderViewsAge(s); } } catch(e){}
  setTimeout(pollState, 1000);
})();

// Volatility card (informational, panel-only): live realized-vol index from MT5
// bars. ~14 bar reads per fetch, so poll slowly (vol changes slowly anyway).
function _volColor(z){ return z == null ? C.muted : z >= 1.5 ? C.short : z >= 0.5 ? C.human : C.muted; }
function renderVolatility(d){
  const idx = document.getElementById("vol-index");
  if(idx){
    if(!d || !d.available){ idx.innerHTML = `<span class="vol-warn">${_esc((d && d.note) || "unavailable")}</span>`; }
    else {
      const chip = (lbl, z)=> `<span class="vol-chip"><label>${lbl}</label>`
        + `<b style="color:${_volColor(z)}">${z == null ? "—" : (z > 0 ? "+" : "") + z}</b></span>`;
      idx.innerHTML = chip("Combined", d.combined) + chip("FX", d.fx) + chip("Metals", d.metals) + chip("Crypto", d.crypto);
    }
  }
  const bars = document.getElementById("vol-bars"); if(!bars) return;
  const per = (d && d.per_symbol) || {};
  const rows = Object.keys(per).map(s => ({ s, ...per[s] })).sort((a, b) => b.raw - a.raw);
  if(!rows.length){ bars.innerHTML = `<div class="ctx-note">${_esc((d && d.note) || "live volatility needs the running engine")}</div>`; return; }
  const max = Math.max(...rows.map(r => r.raw)) || 1;
  const cls = c => c === "CRYPTO" ? "crypto" : c === "METAL" ? "metals" : "fx";
  bars.innerHTML = rows.map(r => `<div class="vol-row"><span class="vol-sym">${_esc(r.s)}</span>`
    + `<span class="vol-track"><span class="vol-fill ${cls(r.cls)}" style="width:${Math.round(r.raw / max * 100)}%"></span></span>`
    + `<span class="vol-val">${r.raw.toFixed(3)}% <span class="vol-z" style="color:${_volColor(r.z)}">z${r.z > 0 ? "+" : ""}${r.z}</span></span></div>`).join("");
}
async function loadVolatility(){ try { renderVolatility(await getJSON("/api/volatility")); } catch(e){} }
loadVolatility();
setInterval(loadVolatility, 20000);   // realized vol drifts slowly; 14 bar reads/fetch
