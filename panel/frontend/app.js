// SIGNAL//LAYER — read-only dashboard. Polls the backend and renders.
const C = { canvas:"#0D0E12", surface:"#14161D", lift:"#1E2330",
  chartreuse:"#C8FF00", claude:"#7B61FF", nemo:"#00C8A0", human:"#F5A623",
  long:"#2ECC71", short:"#FF4D4D", flat:"#3A3F50", ink:"#E8EAF0", muted:"#6B7080" };
const PLOT_BG = { paper_bgcolor:C.surface, plot_bgcolor:C.surface,
  font:{color:C.ink, family:"JetBrains Mono"}, margin:{l:40,r:12,t:8,b:28} };

async function getJSON(u){
  try { const r = await fetch(u); if(!r.ok) return null; return await r.json(); }
  catch(e){ return null; }
}
const fmt = (n,d=0)=> n==null? "—" : Number(n).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});

function renderStrip(s){
  // currency-strength ribbon: aggregate each currency across ALL its pairs
  // (base +, quote -, weighted by confidence) into one net up/down. Click for reasoning.
  const _sgn = d => d==="long"?1 : d==="short"?-1 : 0;
  const _acc = {};
  for(const r of (s.consensus || [])){
    if(!r.symbol || r.symbol.length < 6) continue;
    const _b = r.symbol.slice(0,3), _q = r.symbol.slice(3,6);
    const _v = _sgn(r.direction) * (r.confidence || 0);
    (_acc[_b] = _acc[_b] || {s:0,n:0}); _acc[_b].s += _v; _acc[_b].n++;
    (_acc[_q] = _acc[_q] || {s:0,n:0}); _acc[_q].s -= _v; _acc[_q].n++;
  }
  const _ccys = Object.keys(_acc).map(c=>({c, str:_acc[c].s/Math.max(_acc[c].n,1)})).sort((a,b)=>b.str-a.str);
  const _chips = _ccys.map(({c,str})=>{
    const cls = str > 0.02 ? "long" : str < -0.02 ? "short" : "flat";
    const ar = str > 0.02 ? "▲" : str < -0.02 ? "▼" : "·";
    return `<span class="rchip ${cls}" title="net strength ${str.toFixed(2)} across its pairs">${c} ${ar}</span>`;
  }).join("");
  // currency ribbon lives in its OWN row (#ribbon) so it never clobbers the
  // Claude/Nemotron regime pills in the header (#regimes).
  const _rib = document.getElementById("ribbon");
  if(_rib) _rib.innerHTML = _chips || '<span class="rchip flat">awaiting market read…</span>';
  // header regime pills: each model's one-line read, full text on hover.
  const _rg = (s.regime) || {};
  const _cl = document.getElementById("regime-claude");
  if(_cl){ _cl.textContent = _rg.claude || "—"; _cl.parentElement.title = _rg.claude || "Claude — no market read yet"; }
  const _nm = document.getElementById("regime-nemo");
  if(_nm){ _nm.textContent = _rg.nemotron || "—"; _nm.parentElement.title = _rg.nemotron || "Nemotron — unavailable (Claude-only)"; }
  const a = s.account || {};
  const kpis = [
    ["EQUITY", fmt(a.equity)], ["RET", a.return_pct!=null? (a.return_pct*100).toFixed(2)+"%":"—"],
    ["LEV", a.gross_leverage!=null? a.gross_leverage.toFixed(1)+"x":"—"],
    ["ML", a.margin_level!=null? Math.round(a.margin_level)+"%":"—"],
    ["DD", a.drawdown!=null? (a.drawdown*100).toFixed(1)+"%":"—"],
    ["SHARPE", a.sharpe!=null? a.sharpe.toFixed(2):"—"],
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([k,v])=>
    `<div class="kpi"><label>${k}</label><b>${v}</b></div>`).join("");
  const _age = s.views_age_secs, _va = document.getElementById("views-age");
  if(_va){
    if(_age == null){ _va.textContent = ""; }
    else { const _m = Math.floor(_age/60);
      _va.textContent = "views " + (_m < 1 ? "<1m" : _m + "m") + (_age > 1200 ? " ⚠" : "");
      _va.classList.toggle("stale", _age > 1200); }
  }
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

function renderPositions(pos){
  const el = document.getElementById("positions");
  if(!el) return;
  if(!pos || !pos.available){ el.innerHTML = '<div style="color:var(--muted)">(MT5 unavailable)</div>'; return; }
  const list = pos.positions || [];
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
setInterval(tick, 15000);

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
  // one row per instrument: Claude (violet, left) — Nemotron (teal, right) confidences,
  // x sign encodes direction so opposing calls point opposite ways. Filtered to the
  // active asset-class tab (FX / Crypto / Metals).
  _lastConsensus = allRows || [];
  const rows = _lastConsensus.filter(r => _grpOf(r.symbol) === _dumbTab);
  const ys = rows.map(r=>r.symbol);
  const sgn = d => d==="long"?1 : d==="short"?-1 : 0;
  const cx = rows.map(r=> r.claude? sgn(r.claude.direction)*r.claude.confidence : 0);
  const nx = rows.map(r=> r.nemotron? sgn(r.nemotron.direction)*r.nemotron.confidence : null);
  const lines = rows.map((r,i)=>({x:[cx[i], nx[i]??cx[i]], y:[r.symbol,r.symbol], type:"scatter", mode:"lines",
    line:{color: r.agreement==="agree"? C.chartreuse : r.agreement==="disagree"? C.short : C.muted,
      width:2, dash: r.boosted? "solid":"dot"}, showlegend:false, hoverinfo:"skip"}));
  const claudePts = {x:cx, y:ys, type:"scatter", mode:"markers", name:"Claude",
    marker:{color:C.claude, size:11}, text:rows.map(r=>r.claude?.confidence?.toFixed(2)||""), hovertemplate:"Claude %{y}: %{x:.2f}<extra></extra>"};
  const nemoPts = {x:nx, y:ys, type:"scatter", mode:"markers", name:"Nemotron",
    marker:{color:C.nemo, size:11}, hovertemplate:"Nemotron %{y}: %{x:.2f}<extra></extra>"};
  Plotly.react("dumbbell", [...lines, claudePts, nemoPts],
    {...PLOT_BG, height: Math.max(120, ys.length*30+40),
     margin:{l:66, r:14, t:8, b:30}, yaxis:{automargin:true, color:C.muted},
     xaxis:{range:[-1.05,1.05], zeroline:true, zerolinecolor:C.muted, title:{text:"short ◀  confidence  ▶ long", font:{size:9}}},
     showlegend:false}, {displayModeBar:false});
}

let _lastHm = null, _conWired = false, _conLayer = "combined";
// pick which view a cell shows: combined (used), Claude-only, or Nemotron-only
function _cellLayer(c){
  if(c==null) return null;
  if(_conLayer === "claude")   return c.claude   || {direction:c.direction, confidence:c.confidence};
  if(_conLayer === "nemotron") return c.nemotron || null;   // null when no 2nd opinion that refresh
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
    return c==null? "" : `${c.direction} ${c.confidence.toFixed(2)}${raw&&raw.boosted&&_conLayer==="combined"?" ★":""}`; }));
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
    type:"heatmap", text, xgap:1, ygap:1,
    hovertemplate:"%{y} · %{customdata}: %{text}<extra></extra>",
    colorscale:[[0,C.short],[0.5,C.canvas],[1,C.long]], zmid:0, showscale:false}],
    {...PLOT_BG, height: Math.max(160, hm.symbols.length*22+40), margin:{l:64,r:8,t:8,b:30},
     xaxis:{tickmode:"array", tickvals, ticktext, tickangle:-45, color:C.muted,
       automargin:true, range:[-0.5, Math.max(0.5, n-0.5)]},
     dragmode:"pan", uirevision:"heatmap"},
    {displayModeBar:true, displaylogo:false, scrollZoom:true,
     modeBarButtonsToRemove:["select2d","lasso2d"]});
}

let curSymbol = null, curTF = "15m", _tfWired = false;

async function plotSymbol(symbol, tf){
  if(symbol) curSymbol = symbol;
  if(tf) curTF = tf;
  if(!curSymbol) return;
  const b = await getJSON(`/api/bars/${curSymbol}?tf=${curTF}`);
  const el = document.getElementById("symbol-chart");
  if(!b || !b.available){ el.innerHTML = '<div style="color:var(--muted);padding:24px">live MT5 unavailable — projection needs a running terminal</div>'; return; }
  const n = b.closes.length;
  // real timestamps for the x-axis (epoch seconds -> ms). The cones project
  // forward in BAR units, so convert their offset to time via the bar interval dt.
  const tms = (b.times || []).map(t => t * 1000);
  const lastT = tms.length ? tms[tms.length - 1] : 0;
  const dt = tms.length > 1 ? (tms[tms.length - 1] - tms[0]) / (tms.length - 1) : 60000;
  const price = [{x:tms, y:b.closes, type:"scatter", mode:"lines", line:{color:C.ink,width:1.5}, name:curSymbol, hovertemplate:"%{y:,.6g}<extra></extra>"}];
  const coneTrace=(cone,color,name,dash)=> cone? [
    {x:cone.upper.map(p=>lastT+p[0]*dt), y:cone.upper.map(p=>p[1]), mode:"lines", line:{width:0}, showlegend:false, hoverinfo:"skip"},
    {x:cone.lower.map(p=>lastT+p[0]*dt), y:cone.lower.map(p=>p[1]), mode:"lines", line:{width:0}, fill:"tonexty",
      fillcolor:color+"22", showlegend:false, hoverinfo:"skip"},
    {x:cone.mid.map(p=>lastT+p[0]*dt), y:cone.mid.map(p=>p[1]), mode:"lines", line:{color:color,width:2,dash:dash||"solid"}, name:name, hovertemplate:"%{y:,.6g}<extra></extra>"},
  ] : [];
  // Consensus is drawn last (on top) and DASHED so that when no Nemotron is
  // running — consensus == Claude, perfectly overlapping — the solid Claude
  // line shows through the gaps instead of being hidden under it.
  const traces = [...price,
    ...coneTrace(b.cones.claude, C.claude, "Claude"),
    ...coneTrace(b.cones.nemotron, C.nemo, "Nemotron"),
    ...coneTrace(b.cones.consensus, C.chartreuse, "Consensus", "dash")];
  Plotly.react("symbol-chart", traces, {...PLOT_BG, height:340, showlegend:true,
    hovermode:"x unified",   // one box tracking the cursor, all series' prices at that x
    hoverlabel:{bgcolor:C.surface, bordercolor:C.lift, font:{family:"JetBrains Mono", size:11, color:C.ink}},
    legend:{font:{size:9}, orientation:"h", x:0, y:1.08, bgcolor:"rgba(0,0,0,0)"},
    uirevision: curSymbol + "|" + curTF,   // keep the user's zoom across refreshes; reset on switch
    margin:{l:10, r:58, t:22, b:26},       // fill the card edge-to-edge; price axis on the right
    xaxis:{type:"date", tickformat:"%H:%M", hoverformat:"%b %d  %H:%M",
      gridcolor:"rgba(255,255,255,0.04)", showgrid:true, color:C.muted, zeroline:false,
      showspikes:true, spikemode:"across", spikethickness:1, spikecolor:C.muted, spikedash:"dot", spikesnap:"cursor",
      title:{text:`${curSymbol} · ${curTF}`, font:{size:9, color:C.muted}}},
    yaxis:{side:"right", gridcolor:"rgba(255,255,255,0.06)", color:C.muted, zeroline:false}},
    {displayModeBar:true, displaylogo:false, scrollZoom:true,
     modeBarButtonsToRemove:["select2d","lasso2d"]});
}

// Single entry point for "show this symbol on the Instrument & Projection panel":
// redraws the chart AND syncs the panel's state (symbol-list highlight + the
// override/AI controls) so everything reflects the selected symbol. Used by both
// the instrument list and a click on a position row.
function selectSymbol(sym){
  if(!sym) return;
  plotSymbol(sym, curTF);
  const list = document.getElementById("symbol-list");
  if(list) list.querySelectorAll(".sym").forEach(x=>x.classList.toggle("active", x.dataset.sym===sym));
  renderOverride();   // Pin / conf / take-profit / AI rationale for the new symbol
}

// asset-class grouping for the symbol list (crypto kept separate from FX/metals)
const _CRYPTO = new Set(["BTCUSD","ETHUSD","SOLUSD","XRPUSD","BARUSD"]);
const _METALS = new Set(["XAGUSD","XAUUSD"]);
const _grpOf = s => _CRYPTO.has(s) ? "crypto" : _METALS.has(s) ? "metals" : "fx";
const _GRP_ORDER = {fx:0, metals:1, crypto:2};

function buildSymbolList(symbols){
  const el = document.getElementById("symbol-list");
  const sig = symbols.join(",") + "|" + ((_ctl && _ctl.block) || []).join(",");
  if(el && el.dataset.sig !== sig){
    el.dataset.sig = sig;
    const blocked = new Set((_ctl && _ctl.block) || []);
    const sorted = [...symbols].sort((a,b)=> _GRP_ORDER[_grpOf(a)] - _GRP_ORDER[_grpOf(b)]); // stable within group
    let html = "", lastG = null;
    for(const s of sorted){
      const g = _grpOf(s);
      if(g !== lastG){ html += `<div class="sym-group">${g.toUpperCase()}</div>`; lastG = g; }
      html += `<div class="sym${s===curSymbol?" active":""}${blocked.has(s)?" blocked":""}" data-sym="${s}">`+
        `<span class="sym-name">${s}</span>`+
        `<span class="sym-block" data-block="${s}" title="block / allow">${blocked.has(s)?"⊘":"·"}</span></div>`;
    }
    el.innerHTML = html;
    el.querySelectorAll(".sym").forEach(node=> node.onclick = (ev)=>{
      if(ev.target.classList.contains("sym-block")){
        const sym = ev.target.dataset.block;
        const cur = new Set((_ctl && _ctl.block) || []);
        cur.has(sym) ? cur.delete(sym) : cur.add(sym);
        postControls({block: [...cur]});
        return;
      }
      selectSymbol(node.dataset.sym);
    });
  } else if(el){
    el.querySelectorAll(".sym").forEach(x=>x.classList.toggle("active", x.dataset.sym===curSymbol));
  }
  if(!_tfWired){
    document.querySelectorAll(".tf-toggle .tf").forEach(btn=> btn.onclick = ()=>{
      document.querySelectorAll(".tf-toggle .tf").forEach(b=>b.classList.toggle("active", b===btn));
      plotSymbol(curSymbol, btn.dataset.tf);
    });
    _tfWired = true;
  }
}

function wireConsensusTabs(){
  if(_conWired) return;
  const tabs = document.querySelectorAll(".con-tabs .contab");
  if(!tabs.length) return;
  tabs.forEach(btn=> btn.onclick = ()=>{
    const which = btn.dataset.con;
    tabs.forEach(b=>b.classList.toggle("active", b===btn));
    const lt = document.getElementById("con-latest"), ot = document.getElementById("con-overtime");
    if(lt) lt.hidden = which !== "latest";
    if(ot) ot.hidden = which !== "overtime";
    if(which === "overtime" && _lastHm) plotHeatmap(_lastHm);   // re-render now that it's visible
  });
  const layers = document.querySelectorAll(".ot-layers .otlayer");
  layers.forEach(btn=> btn.onclick = ()=>{
    _conLayer = btn.dataset.layer;
    layers.forEach(b=>b.classList.toggle("active", b===btn));
    if(_lastHm) plotHeatmap(_lastHm);
  });
  _conWired = true;
}
window.renderConsensus = async function(s){
  plotDumbbell(s.consensus || []);
  wireDumbTabs();
  wireConsensusTabs();
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
  const pb = document.getElementById("pause-btn");
  if(pb){ pb.classList.toggle("on", !!_ctl.paused); pb.textContent = _ctl.paused ? "Paused" : "Pause"; }
  const so = document.getElementById("so-btn");
  if(so){ const on = _ctl.second_opinion !== false;   // null/true = used (default on)
    so.classList.toggle("on", on); so.textContent = on ? "Nemotron input: ON" : "Nemotron input: OFF"; }
  const arm = document.getElementById("scalp-arm");
  if(arm){ const on = !!_ctl.scalper_armed;
    arm.classList.toggle("on", on);
    arm.textContent = on ? "Armed (live)" : (_scalpAllCleared() ? "Arm: dry-run" : "Arm (force)"); }
  renderScalpAssigned();
}

// gate verdict per symbol comes from controls.scalper_gate (persisted by the
// /api/scalper/gate backtest step); _gateRunning tracks in-flight runs.
let _gateRunning = {};
function _scalpGate(sym){ return ((_ctl && _ctl.scalper_gate) || {})[sym] || null; }
function _scalpAllCleared(){
  const syms = (_ctl && _ctl.scalper_symbols) || [];
  return syms.length > 0 && syms.every(s => (_scalpGate(s) || {}).cleared);
}

async function runGate(sym){     // the "backtest as a step" when a symbol is assigned
  _gateRunning[sym] = true; renderScalpAssigned();
  try { await getJSON("/api/scalper/gate?symbol=" + encodeURIComponent(sym)); }
  finally { _gateRunning[sym] = false; await loadControls(); }   // loadControls re-renders
}

function renderScalpAssigned(){
  const el = document.getElementById("scalp-assigned"); if(!el) return;
  const syms = (_ctl && _ctl.scalper_symbols) || [];
  if(!syms.length){ el.innerHTML = '<span style="color:var(--muted);font-size:10px">none assigned (engine trades all)</span>'; return; }
  el.innerHTML = syms.map(s=>{
    const g = _scalpGate(s);
    let verdict, cls;
    if(_gateRunning[s]){ verdict = "backtesting…"; cls = "g-run"; }
    else if(!g){ verdict = "no backtest yet"; cls = "g-none"; }
    else if(g.cleared){ verdict = `clears ✓ (Sh ${(g.test&&g.test.sharpe!=null)?g.test.sharpe:"?"})`; cls = "g-ok"; }
    else { verdict = "no edge — " + (g.reason || "dormant"); cls = "g-no"; }
    return `<div class="scalp-row"><span class="scalp-chip">${s}<button class="scalp-x" data-sym="${s}">&times;</button></span>`+
           `<span class="scalp-gate ${cls}">${verdict}</span></div>`;
  }).join("");
  el.querySelectorAll(".scalp-x").forEach(b=> b.onclick = ()=>{
    const cur = ((_ctl && _ctl.scalper_symbols) || []).filter(x=>x!==b.dataset.sym);
    postControls({scalper_symbols: cur}); });
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
  const pb = document.getElementById("pause-btn");
  if(pb && !pb.dataset.w){ pb.dataset.w="1"; pb.onclick = ()=> postControls({paused: !(_ctl && _ctl.paused)}); }
  const so = document.getElementById("so-btn");
  if(so && !so.dataset.w){ so.dataset.w="1"; so.onclick = ()=>{
    const on = _ctl && _ctl.second_opinion !== false;   // currently on -> turn off, and vice versa
    postControls({second_opinion: !on}).then(()=> setStatus(
      "Nemotron input "+(!on ? "ON" : "OFF")+" — applies at next AI refresh (still recorded)")); }; }
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
  const sarm = document.getElementById("scalp-arm");
  if(sarm && !sarm.dataset.w){ sarm.dataset.w="1"; sarm.onclick = ()=>{
    if(_ctl && _ctl.scalper_armed){ postControls({scalper_armed: false, scalper_armed_force: false})
      .then(()=> setStatus("Scalper disarmed (dry-run)")); return; }
    if(_scalpAllCleared()){
      postControls({scalper_armed: true, scalper_armed_force: false})
        .then(()=> setStatus("Scalper ARMED (live) — gate-cleared"));
    } else if(confirm("Some symbols have NOT cleared the backtest gate. Force-arm anyway (unvalidated)?")){
      postControls({scalper_armed: true, scalper_armed_force: true})
        .then(()=> setStatus("Scalper FORCE-ARMED (unvalidated)"));
    } }; }
  const rb = document.getElementById("refresh-btn");
  if(rb && !rb.dataset.w){ rb.dataset.w="1"; rb.onclick = async ()=>{
    await fetch("/api/refresh", {method:"POST"});
    setStatus("Consensus refresh requested — re-querying AI now"); }; }
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
  const m = document.getElementById("regime-modal"); if(m) m.hidden = false;
}
function wireRegimeModal(){
  const reg = document.getElementById("regimes");
  if(reg && !reg.dataset.w){ reg.dataset.w="1"; reg.title="click for full reasoning"; reg.onclick = openRegimeModal; }
  const m = document.getElementById("regime-modal");
  if(m && !m.dataset.w){ m.dataset.w="1";
    m.onclick = (e)=>{ if(e.target===m || e.target.id==="regime-close") m.hidden=true; };
    document.addEventListener("keydown", (e)=>{ if(e.key==="Escape") m.hidden=true; });
  }
}
let _histSym = null, _navWired = false;
function showView(name){
  ["dashboard","history"].forEach(v=>{
    const el = document.getElementById("view-"+v); if(el) el.hidden = v !== name; });
  const nm = document.getElementById("navmenu"); if(nm) nm.hidden = true;
  document.querySelectorAll(".navitem").forEach(b=>b.classList.toggle("active", b.dataset.view===name));
  if(name === "history") initHistory();
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
  const xs=[], ys=[], eX=[], eY=[], eT=[], cX=[], cY=[], cT=[];
  for(const rec of recs){
    const v = (rec.views||[]).find(x=>x.symbol===sym);
    if(v){ xs.push(rec.ts); ys.push(sgn(v.direction)*(v.confidence||0)); }
    for(const o of (rec.orders||[])) if(o.symbol===sym){
      const lbl = (o.side||"").toUpperCase()+" "+(o.lots||"");
      if(o.side==="close"){ cX.push(rec.ts); cY.push(0); cT.push(lbl); }
      else { eX.push(rec.ts); eY.push(0); eT.push(lbl); }
    }
  }
  const traces=[
    {x:xs, y:ys, type:"scatter", mode:"lines+markers", line:{color:C.chartreuse,width:2}, marker:{size:5},
     name:"stance", hovertemplate:"%{x|%b %d %H:%M}<br>%{y:.2f}<extra></extra>"},
    {x:eX, y:eY, type:"scatter", mode:"markers", marker:{color:C.human,size:11,symbol:"diamond"},
     text:eT, name:"entries", hovertemplate:"%{x|%b %d %H:%M}<br>%{text}<extra></extra>"},
    {x:cX, y:cY, type:"scatter", mode:"markers",
     marker:{color:C.short,size:11,symbol:"x",line:{color:C.ink,width:1}},
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
      items.push(`<div><span style="color:var(--muted)">${t}</span> <span style="color:${col}">${side}</span> ${o.lots||""} ${o.symbol}</div>`);
    }
    if(items.length>=40) break;
  }
  el.innerHTML = items.length ? items.join("") : '<div style="color:var(--muted)">(no orders for '+sym+' yet)</div>';
}
loadControls(); loadPresets(); wireControls(); wireRegimeModal(); wireNav();

let _ov = {};   // last-known overrides {sym:{pinned,view,take_profit,note}}

async function loadOverrides(){ _ov = (await getJSON("/api/overrides")) || {}; renderOverride(); }

function renderOverride(){
  const sym = curSymbol; if(!sym) return;
  const o = _ov[sym] || {};
  const pin = document.getElementById("ov-pin");
  if(pin){ pin.classList.toggle("on", !!o.pinned); pin.textContent = o.pinned ? "Pinned" : "Pin"; }
  const dir = document.getElementById("ov-dir");
  if(dir && document.activeElement !== dir) dir.value = (o.view && o.view.direction) || "";
  const conf = document.getElementById("ov-conf");
  if(conf && document.activeElement !== conf) conf.value = (o.view && o.view.confidence != null) ? o.view.confidence : "";
  const tp = document.getElementById("ov-tp");
  if(tp && document.activeElement !== tp) tp.value = (o.take_profit != null) ? o.take_profit : "";
  const note = document.getElementById("ov-note");
  if(note && document.activeElement !== note) note.value = o.note || "";
  // AI rationale for this symbol from the latest state
  const ai = document.getElementById("ov-ai");
  if(ai && window._lastState){
    const v = (window._lastState.consensus || []).find(r=>r.symbol===sym);
    ai.innerHTML = v ? `<span class="claude">Claude:</span> ${v.claude ? v.claude.confidence : "—"} `+
      `· <span class="nemo">Nemotron:</span> ${v.nemotron ? v.nemotron.confidence : "—"} · ${v.agreement}` : "";
  }
}

async function postOverride(sym, patch){
  await fetch(`/api/overrides/${sym}`, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(patch)});
  await loadOverrides();
}

function wireOverride(){
  const pin = document.getElementById("ov-pin");
  if(pin && !pin.dataset.w){ pin.dataset.w="1"; pin.onclick=()=> postOverride(curSymbol, {pinned: !(_ov[curSymbol] && _ov[curSymbol].pinned)}); }
  const save = document.getElementById("ov-save");
  if(save && !save.dataset.w){ save.dataset.w="1"; save.onclick=()=>{
    const dir = document.getElementById("ov-dir").value;
    const conf = parseFloat(document.getElementById("ov-conf").value);
    const tpv = document.getElementById("ov-tp").value;
    const patch = {};
    patch.view = dir ? {direction: dir, confidence: isNaN(conf)?0.5:conf} : null;
    patch.take_profit = tpv === "" ? null : parseFloat(tpv);
    postOverride(curSymbol, patch);
  }; }
  const ns = document.getElementById("ov-note-save");
  if(ns && !ns.dataset.w){ ns.dataset.w="1"; ns.onclick=()=> postOverride(curSymbol, {note: document.getElementById("ov-note").value}); }
}

// capture last state for the AI-rationale line, and hook into the loop
const _origRenderStrip = renderStrip;
renderStrip = function(s){ window._lastState = s; _origRenderStrip(s); };
const _tick2 = tick;
tick = async function(){ await _tick2(); await loadOverrides(); wireOverride(); };
loadOverrides(); wireOverride();

// Positions refresh on a fast ~1s cadence (live P&L), independent of the 15s
// tick. Self-scheduling (waits for each fetch) so requests never pile up.
// Also pulls per-symbol scalper status for the inline ·s sub-lines (spec §6.3).
let _scalpState = {};
(async function pollPositions(){
  try {
    const sc = await getJSON("/api/scalper");
    _scalpState = {}; ((sc && sc.symbols) || []).forEach(s => { _scalpState[s.symbol] = s; });
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
