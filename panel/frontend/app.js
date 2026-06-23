// SIGNAL//LAYER — read-only dashboard. Polls the backend and renders.
const C = { canvas:"#0D0E12", surface:"#14161D", lift:"#1E2330",
  chartreuse:"#C8FF00", claude:"#7B61FF", nemo:"#00C8A0", human:"#F5A623",
  long:"#2ECC71", short:"#FF4D4D", flat:"#3A3F50", ink:"#E8EAF0", muted:"#6B7080" };
const PLOT_BG = { paper_bgcolor:C.surface, plot_bgcolor:C.surface,
  font:{color:C.ink, family:"JetBrains Mono"}, margin:{l:40,r:12,t:8,b:28} };

async function getJSON(u){ const r = await fetch(u); return r.json(); }
const fmt = (n,d=0)=> n==null? "—" : Number(n).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});

function renderStrip(s){
  document.getElementById("regime-claude").textContent = s.regime?.claude || "—";
  document.getElementById("regime-nemo").textContent = s.regime?.nemotron || "— unavailable";
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
}

function plotEquity(series){
  const xs = series.map(p=>p.ts), ys = series.map(p=>p.equity);
  const start = ys.length? ys[0] : 1000000;
  const killLine = start * (1 - 0.20);   // 20% drawdown kill level
  const traces = [
    {x:xs, y:ys, type:"scatter", mode:"lines", line:{color:C.chartreuse, shape:"hv", width:2}, name:"equity"},
    {x:xs, y:xs.map(()=>killLine), type:"scatter", mode:"lines",
     line:{color:C.short, dash:"dash", width:1}, name:"kill (-20%)"},
  ];
  Plotly.react("equity-chart", traces, {...PLOT_BG, height:180, showlegend:false}, {displayModeBar:false});
}

function bandSteps(bands){           // [{to,color}] -> Plotly gauge steps
  let from = 0;
  return bands.map(b => { const s = {range:[from, b.to], color:b.color}; from = b.to; return s; });
}
function plotGauges(gauges){
  const data = gauges.map((g,i)=>({
    type:"indicator", mode:"gauge+number", value:g.value,
    number:{suffix:g.unit, font:{size:16}},
    title:{text:g.label, font:{size:10, color:C.muted}},
    domain:{row:Math.floor(i/2), column:i%2},
    gauge:{axis:{range:[0,g.max], tickcolor:C.muted},
      bar:{color:C.ink, thickness:0.18},
      steps: bandSteps(g.bands),
      threshold:{line:{color:C.ink,width:2}, value: (g.hard[0] && g.hard[0].at) || g.max}},
  }));
  Plotly.react("gauges", data, {...PLOT_BG, height:220,
    grid:{rows:2, columns:2, pattern:"independent"}, margin:{l:10,r:10,t:24,b:10}}, {displayModeBar:false});
}

function renderTripwires(tw){
  document.getElementById("tripwires").innerHTML = tw.map(t=>
    `<div class="tw ${t.active?"active":"ok"}"><span>${t.label}</span>`+
    `<span>${t.active? "⚠ ACTIVE (penalty "+t.penalty+")":"ok"}</span></div>`).join("");
}

function renderOrders(orders){
  const el = document.getElementById("orders");
  if(!orders || !orders.length){ el.innerHTML = '<div style="color:var(--muted)">(none this refresh)</div>'; return; }
  el.innerHTML = orders.map(o=>{
    if(o.side==="close") return `<div>CLOSE ${o.symbol}</div>`;
    const col = o.side==="buy"? C.long : C.short;
    const sl = o.sl? `  SL=${o.sl}`:"";
    return `<div><span style="color:${col}">${o.side.toUpperCase()}</span> ${o.lots} ${o.symbol}  `+
      `(${o.cur.toFixed(2)}→${o.target.toFixed(2)})${sl}</div>`;
  }).join("");
}

window.renderCharts = async function(s){
  const eq = await getJSON("/api/equity");
  plotEquity(eq.series || []);
  plotGauges(s.gauges || []);
  renderTripwires(s.tripwires || []);
  renderOrders(s.orders || []);
  if (window.renderConsensus) await window.renderConsensus(s);  // Task 9
};

async function tick(){
  const s = await getJSON("/api/state");
  renderStrip(s);
  if (window.renderCharts) await window.renderCharts(s);  // added in Tasks 8-9
}
tick();
setInterval(tick, 15000);

const dirColor = d => d==="long"? C.long : d==="short"? C.short : C.flat;

function plotDumbbell(rows){
  // one row per instrument: Claude (violet, left) — Nemotron (teal, right) confidences,
  // x sign encodes direction so opposing calls point opposite ways.
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
    {...PLOT_BG, height: Math.max(120, ys.length*30+40), xaxis:{range:[-1.05,1.05], zeroline:true, zerolinecolor:C.muted, title:{text:"short ◀  confidence  ▶ long", font:{size:9}}},
     showlegend:false}, {displayModeBar:false});
}

function plotHeatmap(hm){
  const lum = c => c==null? 0 : c.confidence;             // 0..1
  const sign = c => c==null? 0 : (c.direction==="long"?1 : c.direction==="short"?-1 : 0);
  // z = signed confidence (-1..1): red short, green long, dark flat
  const z = hm.cells.map(row=> row.map(c=> c==null? null : sign(c)*Math.max(lum(c),0.05)));
  const text = hm.cells.map(row=> row.map(c=> c==null? "" :
    `${c.direction} ${c.confidence.toFixed(2)}${c.boosted?" ★":""}`));
  Plotly.react("heatmap", [{z, x:hm.timestamps.map(t=>t.slice(11,16)), y:hm.symbols,
    type:"heatmap", text, hovertemplate:"%{y} %{x}: %{text}<extra></extra>",
    colorscale:[[0,C.short],[0.5,C.canvas],[1,C.long]], zmid:0, showscale:false}],
    {...PLOT_BG, height: Math.max(160, hm.symbols.length*22+40), margin:{l:64,r:8,t:8,b:24}}, {displayModeBar:false});
}

async function plotSymbol(symbol){
  const b = await getJSON("/api/bars/"+symbol);
  const el = document.getElementById("symbol-chart");
  if(!b.available){ el.innerHTML = '<div style="color:var(--muted);padding:20px">live MT5 unavailable — projection needs a running terminal</div>'; return; }
  const n = b.closes.length;
  const xhist = b.closes.map((_,i)=>i);
  const price = [{x:xhist, y:b.closes, type:"scatter", mode:"lines", line:{color:C.muted,width:1}, name:symbol, hoverinfo:"skip"}];
  const coneTrace=(cone,color,name)=> cone? [
    {x:cone.upper.map(p=>n-1+p[0]), y:cone.upper.map(p=>p[1]), mode:"lines", line:{width:0}, showlegend:false, hoverinfo:"skip"},
    {x:cone.lower.map(p=>n-1+p[0]), y:cone.lower.map(p=>p[1]), mode:"lines", line:{width:0}, fill:"tonexty",
      fillcolor:color+"22", showlegend:false, hoverinfo:"skip"},
    {x:cone.mid.map(p=>n-1+p[0]), y:cone.mid.map(p=>p[1]), mode:"lines", line:{color:color,width:2}, name:name},
  ] : [];
  const traces = [...price,
    ...coneTrace(b.cones.claude, C.claude, "Claude"),
    ...coneTrace(b.cones.nemotron, C.nemo, "Nemotron"),
    ...coneTrace(b.cones.consensus, C.chartreuse, "Consensus")];
  Plotly.react("symbol-chart", traces, {...PLOT_BG, height:220, showlegend:true,
    legend:{font:{size:9}, orientation:"h"}}, {displayModeBar:false});
}

window.renderConsensus = async function(s){
  plotDumbbell(s.consensus || []);
  const hm = await getJSON("/api/heatmap");
  plotHeatmap(hm);
  const sel = document.getElementById("symbol-select");
  if(!sel.dataset.init && (s.consensus||[]).length){
    sel.innerHTML = s.consensus.map(r=>`<option>${r.symbol}</option>`).join("");
    sel.dataset.init = "1";
    sel.onchange = ()=>plotSymbol(sel.value);
    plotSymbol(sel.value);
  }
};
