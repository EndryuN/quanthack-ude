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
