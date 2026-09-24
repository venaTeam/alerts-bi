"""The portal's one stylesheet.

Served from ``/assets/portal.css`` rather than inlined, so the Content-Security-Policy can be
``style-src 'self'`` with no ``unsafe-inline``. No web fonts are fetched: the portal runs on
the company network and must render with no outbound access.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Final

__all__ = ["STYLESHEET", "STYLESHEET_PATH"]

STYLESHEET: Final = """
:root{
  --bg:#EEF1F4;--surface:#FFFFFF;--surface-2:#F6F8FA;--ink:#15202B;--ink-2:#44515E;--muted:#66727F;
  --line:#DAE0E6;--line-strong:#BFC8D1;--focus:#2F5FD0;
  --v1:#1D7670;--v1-soft:#DDEFEC;--v2:#4050C0;--v2-soft:#E4E7F8;
  --rule:#B3372E;--rule-soft:#F7E3E0;--model:#7443B0;--model-soft:#EFE6F8;
  --ready:#94600F;--ready-soft:#F6ECD8;--good:#2B7548;--good-soft:#E0F0E6;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"Cascadia Mono",Consolas,monospace;
  color-scheme:light;
}
@media (prefers-color-scheme: dark){
  :root{
    color-scheme:dark;
    --bg:#0E1318;--surface:#161D24;--surface-2:#1C242C;--ink:#E5EAEF;--ink-2:#B4BEC8;--muted:#8894A0;
    --line:#29323B;--line-strong:#3A4550;--focus:#8EA8F0;
    --v1:#52B9AE;--v1-soft:#15302D;--v2:#909BF0;--v2-soft:#1F2548;
    --rule:#EF7A6F;--rule-soft:#3A1D1A;--model:#B894EA;--model-soft:#2B2140;
    --ready:#E0AA4E;--ready-soft:#33270F;--good:#63C18A;--good-soft:#162F22;
  }
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.5}
h1,h2,h3,h4{margin:0;line-height:1.25;text-wrap:balance}
a{color:var(--focus)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:4px}
.mono{font-family:var(--mono);font-size:.92em;overflow-wrap:anywhere}
.wrap{max-width:1180px;margin:0 auto;padding-inline:20px}
@media (max-width:520px){.wrap{padding-inline:16px}}
.topbar{background:var(--surface);border-bottom:1px solid var(--line)}
.topbar .wrap{display:flex;align-items:center;gap:16px;min-height:56px;flex-wrap:wrap;padding-block:8px}
.brand{display:flex;align-items:center;gap:10px;font-weight:600;color:var(--ink);text-decoration:none}
.brand small{font-weight:400;color:var(--muted)}
.brand-mark{width:22px;height:22px;border-radius:5px;background:linear-gradient(135deg,var(--v1) 0 50%,var(--v2) 50% 100%)}
.ro{font-size:12px;color:var(--ink-2);border:1px solid var(--line);border-radius:999px;padding:3px 10px}
main{padding-block:20px 64px;display:flex;flex-direction:column;gap:20px}
.crumbs{font-size:13px;color:var(--muted);display:flex;gap:6px;flex-wrap:wrap}
.eyebrow{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:600}
.sub{color:var(--muted);font-size:12.5px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px}
.section-h{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px}
.section-h h2{font-size:17px;font-weight:600}
.section-h p{margin:0;color:var(--muted);font-size:13px}
.chip{display:inline-block;font-size:11.5px;font-weight:500;border-radius:4px;padding:1px 7px;white-space:nowrap;line-height:1.6}
.chip.v1{background:var(--v1-soft);color:var(--v1)} .chip.v2{background:var(--v2-soft);color:var(--v2)}
.chip.rule{background:var(--rule-soft);color:var(--rule)} .chip.model{background:var(--model-soft);color:var(--model)}
.chip.ready{background:var(--ready-soft);color:var(--ready)} .chip.good{background:var(--good-soft);color:var(--good)}
.chip.human{border:1.5px solid var(--ink);color:var(--ink);font-weight:600}
.chip.human.pending{border-style:dotted} .chip.human.dismissed{border-style:dashed;color:var(--ink-2);border-color:var(--ink-2)}
h1{font-size:26px;font-weight:600;letter-spacing:-.015em}
.intro p{margin:6px 0 0;color:var(--ink-2);max-width:62ch}
.table-wrap{overflow-x:auto}
table.dir{width:100%;border-collapse:collapse;min-width:760px}
table.dir th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600;text-align:left;padding:10px 14px;border-bottom:1px solid var(--line)}
table.dir th.v1{color:var(--v1)} table.dir th.v2{color:var(--v2)}
table.dir td{padding:14px;border-bottom:1px solid var(--line);vertical-align:top}
table.dir tr:last-child td{border-bottom:0}
table.dir a.team{font-weight:600;font-size:14.5px}
.num{font-variant-numeric:tabular-nums}
.vol b{display:block;font-variant-numeric:tabular-nums}
.head{display:flex;flex-wrap:wrap;gap:16px 24px;align-items:flex-end;justify-content:space-between}
.weeks{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.weeks select{font:inherit;font-size:13.5px;color:var(--ink);background:var(--surface);border:1px solid var(--line-strong);border-radius:6px;padding:6px 10px;max-width:100%}
.button{font:inherit;font-size:13px;border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);border-radius:6px;padding:6px 12px;cursor:pointer;text-decoration:none;display:inline-block}
.meta{display:flex;flex-wrap:wrap;gap:6px 28px;font-size:13.5px}
.meta .k{display:block;font-size:11px;letter-spacing:.07em;text-transform:uppercase;font-weight:600;color:var(--muted)}
.note{border-left:3px solid var(--ink);padding:10px 14px;background:var(--surface);border-radius:0 8px 8px 0}
.note p{margin:2px 0 0;max-width:80ch;white-space:pre-line}
.phase{padding:14px 18px;display:flex;flex-wrap:wrap;gap:12px 28px;align-items:center}
.steps{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}
.steps span{font-size:12px;padding:3px 9px;border-radius:4px;background:var(--surface-2);color:var(--muted);border:1px solid var(--line)}
.steps span.on{background:var(--ink);color:var(--surface);border-color:var(--ink);font-weight:600}
.meter{display:flex;align-items:center;gap:10px;margin-top:4px}
.meter svg{width:160px;height:8px}
.meter .track{fill:var(--surface-2);stroke:var(--line)} .meter .fill{fill:var(--v2)}
.two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
@media (max-width:900px){.two{grid-template-columns:1fr}}
.schema{padding:16px 18px;display:flex;flex-direction:column;gap:14px;border-top:3px solid var(--line)}
.schema.v1{border-top-color:var(--v1)} .schema.v2{border-top-color:var(--v2)}
.schema h3{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.schema h3 small{font-weight:400;color:var(--muted);font-size:12.5px}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.kpi .n{display:block;font-size:28px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.1}
.kpi .u{display:block;font-size:12.5px;color:var(--ink-2)}
.kpi .d{display:block;font-size:11.5px;color:var(--muted)}
svg.qbar{width:100%;height:10px;display:block}
.q-rule{fill:var(--rule)} .q-model{fill:var(--model)} .q-review{fill:var(--model);opacity:.55} .q-good{fill:var(--good)} .q-un{fill:var(--line-strong)}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;color:var(--ink-2);margin:6px 0 0;padding:0;list-style:none}
.legend svg{width:9px;height:9px;margin-right:5px;vertical-align:0}
.legend b{color:var(--ink)}
dl.facts{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:12.5px;margin:0}
dl.facts dt{color:var(--muted)} dl.facts dd{margin:0;color:var(--ink-2)}
.hist{padding:14px 16px 16px;display:flex;flex-direction:column;gap:10px;border-top:3px solid var(--line)}
.hist.v1{border-top-color:var(--v1)} .hist.v2{border-top-color:var(--v2)}
.hist h3{font-size:15px;font-weight:600}
.chart-title{font-size:12.5px;color:var(--ink-2);font-weight:500;display:flex;justify-content:space-between;gap:8px}
.chart-title span{color:var(--muted);font-weight:400}
svg.chart{width:100%;height:auto;display:block;overflow:visible}
svg.chart text{font-family:var(--sans);font-size:10.5px;fill:var(--muted)}
svg.chart text.value{font-size:11px;font-weight:600;fill:var(--ink)}
svg.chart text.tick.selected{fill:var(--ink);font-weight:600}
svg.chart .grid{stroke:var(--line)} svg.chart .axis{stroke:var(--line-strong)}
svg.chart .line{fill:none;stroke-width:2}
svg.chart .line.v1{stroke:var(--v1)} svg.chart .line.v2{stroke:var(--v2)}
svg.chart .dot{stroke:var(--surface);stroke-width:1.5}
svg.chart .dot.v1{fill:var(--v1)} svg.chart .dot.v2{fill:var(--v2)}
svg.chart .ring{fill:none;stroke-width:1;opacity:.5} svg.chart .ring.v1{stroke:var(--v1)} svg.chart .ring.v2{stroke:var(--v2)}
svg.chart .hit{fill:transparent}
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:14px 16px 0}
.seg{display:inline-flex;border:1px solid var(--line-strong);border-radius:6px;overflow:hidden;background:var(--surface)}
.seg a{padding:5px 11px;font-size:12.5px;color:var(--ink-2);border-right:1px solid var(--line);text-decoration:none}
.seg a:last-child{border-right:0}
.seg a[aria-current="true"]{background:var(--ink);color:var(--surface)}
.wl{list-style:none;margin:10px 0 0;padding:0;border-top:1px solid var(--line)}
.wl li{border-bottom:1px solid var(--line)}
.wl li:last-child{border-bottom:0}
.wl a.row{display:grid;grid-template-columns:4px minmax(0,1fr) auto;gap:0 14px;color:inherit;text-decoration:none}
.wl a.row:hover{background:var(--surface-2)}
.stripe{background:var(--rule)} .stripe.model{background:var(--model)} .stripe.review{background:var(--model);opacity:.55} .stripe.ready{background:var(--ready)} .stripe.good{background:var(--good)}
.body{padding:12px 0;display:flex;flex-direction:column;gap:4px;min-width:0}
.msg{font-size:15px;font-weight:500;overflow-wrap:anywhere}
.ctx,.why{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:12.5px;color:var(--muted);align-items:center}
.ctx .src{color:var(--ink-2);font-weight:500}
.side{display:flex;flex-direction:column;align-items:flex-end;justify-content:center;padding:12px 14px 12px 0;text-align:right;font-size:11.5px;color:var(--muted)}
.side b{font-size:15px;color:var(--ink);font-variant-numeric:tabular-nums}
@media (max-width:560px){.wl a.row{grid-template-columns:4px minmax(0,1fr)}.side{grid-column:2;align-items:flex-start;padding:0 0 12px;text-align:left}}
.pager{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 14px;border-top:1px solid var(--line);font-size:12.5px;color:var(--muted);flex-wrap:wrap}
.pager .links{display:flex;gap:6px}
.pager span.button{opacity:.4;cursor:default}
.empty{padding:22px;color:var(--muted);text-align:center}
.block{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:flex;flex-direction:column;gap:10px}
.block > h2{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px;justify-content:space-between;flex-wrap:wrap}
dl.doc{display:grid;grid-template-columns:150px minmax(0,1fr);gap:6px 14px;margin:0;font-size:13px}
dl.doc dt{color:var(--muted)} dl.doc dd{margin:0;overflow-wrap:anywhere}
.missing{color:var(--ready)} .na{color:var(--muted);font-style:italic}
@media (max-width:480px){dl.doc{grid-template-columns:1fr}}
.finding{border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:8px;border-left:3px solid var(--rule)}
.finding.model{border-left-color:var(--model)} .finding.ready{border-left-color:var(--ready)}
.finding h3{font-size:14px;font-weight:600;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.finding p{margin:0;color:var(--ink-2)}
.ev{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}
.ev.one{grid-template-columns:1fr}
@media (max-width:520px){.ev{grid-template-columns:1fr}}
.ev > div{background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:8px 10px;min-width:0}
.ev .lbl{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600;margin-bottom:2px}
.ev .val{font-family:var(--mono);font-size:12.5px;overflow-wrap:anywhere}
.ev .hint{font-size:11.5px;color:var(--muted);margin-top:3px}
.next{background:var(--good-soft);border-radius:6px;padding:8px 10px;font-size:13px}
.next b{color:var(--good)}
.decide{background:var(--model-soft);border-radius:6px;padding:8px 10px;font-size:13px}
.decide b{color:var(--model)}
blockquote{margin:0;font-style:italic;background:var(--surface-2);border-radius:6px;padding:8px 10px;border:1px solid var(--line);white-space:pre-line;overflow-wrap:anywhere}
.history{border-top:1px dashed var(--line);padding-top:8px;display:flex;flex-direction:column;gap:6px}
.history ol{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.history li{display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 10px;font-size:12.5px}
.history .when,.history .text{grid-column:2}
.history .when{color:var(--muted)}
.history .text{white-space:pre-line;overflow-wrap:anywhere}
details.tech summary{cursor:pointer;font-weight:600;font-size:13px}
details.tech[open] summary{margin-bottom:8px}
.back{font-size:13px}
""".strip()

#: Content-addressed, so a changed stylesheet is never served from a stale cache.
STYLESHEET_PATH: Final = f"/assets/portal-{sha256(STYLESHEET.encode()).hexdigest()[:12]}.css"
