"""Chart rendering via lightweight-charts v4.2.0 CDN + HTML markers."""

import json
import math

from PySide6.QtCore import QThread, Signal

from spotbot.constants import (
    CHART_CDN_URL,
    CANDLE_LIMIT,
    FLOAT_EPS,
    TIMEFRAME_MAP,
    QUOTE_ASSETS,
)
from spotbot.indicators import fli_compute_all_indicators, fli_ohlcv_to_df


def _to_chart_time(ts):
    """Convert exchange-style OHLCV timestamps to Lightweight Charts time values."""
    if ts is None:
        return None
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return None
    if abs(value) > 1e10:
        value = value / 1000.0
    return int(value)


class ChartRenderer:
    CDN = CHART_CDN_URL

    @staticmethod
    def build_html(
        candles, indicators=None, pair="BTC/USDT", timeframe="5m", markers=None
    ):
        if not candles:
            return ChartRenderer._empty(pair)

        candle_js = ChartRenderer._candles_js(candles)
        vol_js = ChartRenderer._volumes_js(candles)
        marker_js = ChartRenderer._markers_js(markers) if markers else "[]"
        overlay_js, pane_js = ("", "")
        if indicators:
            overlay_js, pane_js = ChartRenderer._indicators_js(candles, indicators)

        # Compute last price info for ticker
        last_c = candles[-1] if candles else None
        ticker_price = f"{last_c[4]:.4f}" if last_c and len(last_c) > 4 else "0.00"
        ticker_change = "0.00%"
        if len(candles) >= 2:
            prev_close = candles[-2][4] if len(candles[-2]) > 4 else 0.0
            last_close = candles[-1][4] if len(candles[-1]) > 4 else 0.0
            change_pct = (
                ((last_close - prev_close) / prev_close * 100) if prev_close else 0
            )
            sign = "+" if change_pct >= 0 else ""
            ticker_change = f"{sign}{change_pct:.2f}%"
        ticker_color = (
            "#339b0b"
            if ticker_change.startswith("+") or ticker_change == "0.00%"
            else "#f6465d"
        )
        ticker_info = ""
        if last_c and len(last_c) >= 5:
            ticker_info = f"O:{last_c[1]:.2f} H:{last_c[2]:.2f} L:{last_c[3]:.2f} C:{last_c[4]:.2f}"

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#0b0e11;overflow:hidden;height:100vh;display:flex;flex-direction:column;}}
#ticker{{background:#0b0e11;border-bottom:1px solid #1e2329;padding:8px 12px;display:flex;align-items:center;gap:16px;flex-shrink:0;}}
#tickerPair{{font-size:15px;font-weight:700;color:#eaecef;}}
#tickerPrice{{font-size:14px;font-weight:700;color:{ticker_color};font-family:Consolas,monospace;}}
#tickerChange{{font-size:13px;font-weight:700;color:{ticker_color};font-family:Consolas,monospace;padding:2px 8px;border-radius:4px;background:#1e2329;}}
#tickerInfo{{font-size:11px;color:#848e9c;font-family:Consolas,monospace;}}
#chartArea{{flex:1;display:flex;flex-direction:column;min-height:0;}}
#main-chart{{flex:3;min-height:0;}}
#rsi-pane{{flex:1;min-height:0;border-top:1px solid #1e2329;}}
#macd-pane{{flex:1;min-height:0;border-top:1px solid #1e2329;}}
</style>
<script src="{ChartRenderer.CDN}"></script>
</head><body>

<div id="ticker">
  <span id="tickerPair">{pair} · {timeframe}</span>
  <span id="tickerPrice">{ticker_price}</span>
  <span id="tickerChange">{ticker_change}</span>
  <span id="tickerInfo">{ticker_info}</span>
</div>

<div id="chartArea">
  <div id="main-chart"></div>
  <div id="rsi-pane"></div>
  <div id="macd-pane"></div>
</div>

<script>
const DPI = window.devicePixelRatio || 1;

// ── Main Chart ──
const mc = document.getElementById('main-chart');
const chart = LightweightCharts.createChart(mc, {{
    layout: {{
        background: {{ type: LightweightCharts.ColorType.Solid, color: '#0b0e11' }},
        textColor: '#848e9c', fontSize: 11,
    }},
    grid: {{ vertLines: {{ color: '#1e2329' }}, horzLines: {{ color: '#1e2329' }} }},
    crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    rightPriceScale: {{ borderColor: '#2b3139' }},
    timeScale: {{ borderColor: '#2b3139', timeVisible: true, secondsVisible: false, barSpacing: 8 }},
    watermark: {{
        visible: true, fontSize: 48, horzAlign: 'center', vertAlign: 'center',
        color: 'rgba(240,165,0,0.05)', text: '{pair}',
    }},
}});

const cs = chart.addCandlestickSeries({{
    upColor: '#0ecb81', downColor: '#f6465d',
    borderUpColor: '#0ecb81', borderDownColor: '#f6465d',
    wickUpColor: '#0ecb81', wickDownColor: '#f6465d',
}});
cs.setData({candle_js});

// ── Volume ──
const vs = chart.addHistogramSeries({{
    priceFormat: {{ type: 'volume' }},
    priceScaleId: 'volume',
}});
chart.priceScale('volume').applyOptions({{ scaleMargins: {{ top: 0.85, bottom: 0 }} }});
vs.setData({vol_js});

{overlay_js}

// ── Buy/Sell Markers ──
cs.setMarkers({marker_js});

// ── Resize ──
function resizeMain() {{
    chart.applyOptions({{ width: mc.clientWidth, height: mc.clientHeight }});
}}
new ResizeObserver(resizeMain).observe(mc);
chart.timeScale().fitContent();

{pane_js}
</script></body></html>"""

    @staticmethod
    def _candles_js(candles):
        import json

        entries = []
        for c in candles:
            ts = _to_chart_time(c[0])
            if ts is None:
                continue
            entries.append(
                {"time": ts, "open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            )
        entries.sort(key=lambda x: x["time"])
        return json.dumps(entries)

    @staticmethod
    def _markers_js(markers):
        import json

        if not markers:
            return "[]"
        result = []
        for m in markers:
            ts = _to_chart_time(m.get("ts", 0))
            if ts is None:
                continue
            side = m.get("action", "buy")
            if side == "buy":
                result.append(
                    {
                        "time": ts,
                        "position": "belowBar",
                        "color": "#0ecb81",
                        "shape": "arrowUp",
                        "text": f"BUY @ {m.get('price', 0):.4f}",
                    }
                )
            elif side == "sell":
                result.append(
                    {
                        "time": ts,
                        "position": "aboveBar",
                        "color": "#f6465d",
                        "shape": "arrowDown",
                        "text": f"SELL @ {m.get('price', 0):.4f}",
                    }
                )
            elif side == "pending":
                result.append(
                    {
                        "time": ts,
                        "position": "belowBar",
                        "color": "#f0a500",
                        "shape": "circle",
                        "text": "PENDING",
                    }
                )
        result.sort(key=lambda x: float(x["time"]))
        return json.dumps(result)

    @staticmethod
    def _volumes_js(candles):
        import json

        entries = []
        for c in candles:
            ts = _to_chart_time(c[0])
            if ts is None:
                continue
            entries.append(
                {
                    "time": ts,
                    "value": c[5],
                    "color": "#0ecb81" if c[4] >= c[1] else "#f6465d",
                }
            )
        entries.sort(key=lambda x: x["time"])
        return json.dumps(entries)

    @staticmethod
    def _indicators_js(candles, indicators):
        import json

        overlays = []
        pane_scripts = ""

        # Bollinger
        bb = indicators.get("bollinger", {})
        for key, clr in [
            ("upper", "#2b313988"),
            ("lower", "#2b313988"),
            ("middle", "#f0a50088"),
        ]:
            if bb.get(key):
                data = ChartRenderer._series_js(candles, bb[key])
                overlays.append(
                    f"const bb_{key}=chart.addLineSeries({{color:'{clr}',lineWidth:1,priceLineVisible:false,lastValueVisible:false}});bb_{key}.setData({data});"
                )

        # EMAs
        for ek, clr in [("ema_9", "#f0a500"), ("ema_21", "#8b5cf6")]:
            d = indicators.get(ek, [])
            if d:
                data = ChartRenderer._series_js(candles, d)
                overlays.append(
                    f"const {ek}=chart.addLineSeries({{color:'{clr}',lineWidth:1,priceLineVisible:false,lastValueVisible:true}});{ek}.setData({data});"
                )

        overlay_js = "\n".join(overlays)

        # RSI pane
        rsi = indicators.get("rsi_14", [])
        if rsi:
            data = ChartRenderer._series_js(candles, rsi)
            pane_scripts += f"""
const rc=document.getElementById('rsi-pane');
const rChart=LightweightCharts.createChart(rc,{{layout:{{
    background:{{type:LightweightCharts.ColorType.Solid,color:'#0b0e11'}},
    textColor:'#848e9c',fontSize:10}},grid:{{vertLines:{{color:'#1e2329'}},horzLines:{{color:'#1e2329'}}}},
    rightPriceScale:{{borderColor:'#2b3139'}},timeScale:{{borderColor:'#2b3139',timeVisible:true}}}});
const rsiS=rChart.addLineSeries({{color:'#f0a500',lineWidth:2,priceLineVisible:false}});
rsiS.setData({data});
rsiS.createPriceLine({{price:70,color:'#f6465d',lineStyle:2,axisLabelVisible:true,title:'70'}});
rsiS.createPriceLine({{price:30,color:'#0ecb81',lineStyle:2,axisLabelVisible:true,title:'30'}});
new ResizeObserver(()=>rChart.applyOptions({{width:rc.clientWidth,height:rc.clientHeight}})).observe(rc);
rChart.timeScale().fitContent();"""

        # MACD pane
        macd = indicators.get("macd", {})
        ml = macd.get("macd_line", [])
        sl = macd.get("signal_line", [])
        hl = macd.get("histogram", [])
        if ml:
            d1 = ChartRenderer._series_js(candles, ml)
            d2 = ChartRenderer._series_js(candles, sl)
            d3 = ChartRenderer._series_js(candles, hl)
            pane_scripts += f"""
const dc=document.getElementById('macd-pane');
const dChart=LightweightCharts.createChart(dc,{{layout:{{
    background:{{type:LightweightCharts.ColorType.Solid,color:'#0b0e11'}},
    textColor:'#848e9c',fontSize:10}},grid:{{vertLines:{{color:'#1e2329'}},horzLines:{{color:'#1e2329'}}}},
    rightPriceScale:{{borderColor:'#2b3139'}},timeScale:{{borderColor:'#2b3139',timeVisible:true}}}});
const mS=dChart.addLineSeries({{color:'#2b82e4',lineWidth:2,priceLineVisible:false}});
mS.setData({d1});
const sS=dChart.addLineSeries({{color:'#f6465d',lineWidth:1,priceLineVisible:false}});
sS.setData({d2});
const hS=dChart.addHistogramSeries({{}});
hS.setData({d3});
new ResizeObserver(()=>dChart.applyOptions({{width:dc.clientWidth,height:dc.clientHeight}})).observe(dc);
dChart.timeScale().fitContent();"""

        return overlay_js, pane_scripts

    @staticmethod
    def _series_js(candles, values):
        import json

        entries = []
        for i, v in enumerate(values):
            if v is None:
                continue
            ts = candles[i][0] if i < len(candles) else None
            ts = _to_chart_time(ts)
            if ts is None:
                continue
            entries.append({"time": ts, "value": float(v)})
        return json.dumps(entries)

    @staticmethod
    def _empty(pair):
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{background:#0b0e11;color:#848e9c;font-family:Segoe UI;
display:flex;align-items:center;justify-content:center;height:100vh;}}</style>
</head><body><h2 style='color:#f0a500;'>📊 {pair}</h2><p>No data — click Connection to start</p></body></html>"""


# ═══════════════════════════════════════════════════════════════════════
#  SECTION 6B — FLI Persistent Chart (ported from main.py's _HTML_TEMPLATE)
#  Loaded ONCE into engChart; all further updates go through small JS calls
#  via page().runJavaScript() instead of rebuilding the whole page — this is
#  the "live-bridge" pattern from main.py and is what keeps zoom/scroll state
#  stable across refreshes. SL / TP1 / TP2 series & UI removed per request.
# ═══════════════════════════════════════════════════════════════════════

_FLI_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script src="__LW_CDN__"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:#0b0e11;font-family:'Segoe UI',sans-serif}
#wrapper{width:100%;height:100%;display:flex;flex-direction:column}
#header{
  display:flex;align-items:center;justify-content:space-between;
  padding:7px 16px;background:linear-gradient(180deg,rgba(20,22,26,0.97),rgba(14,14,17,0.97));
  border-bottom:1px solid #2b3139;flex-shrink:0;
}
#header h1{font-size:13px;font-weight:700;color:#eaecef;letter-spacing:.2px}
#legend{font-family:'Consolas','SF Mono',monospace;display:flex;gap:14px;align-items:center}
#legend .price{font-size:17px;font-weight:800;color:#eaecef}
#legend .lbl{font-size:10px;color:#5b6472}
#legend .val{font-size:11px;color:#eaecef;font-weight:600}
#charts{flex:1;display:flex;flex-direction:column;overflow:hidden;position:relative}
#toast{
  position:absolute;top:10px;left:50%;transform:translateX(-50%);
  z-index:100;display:none;align-items:center;gap:8px;
  padding:8px 14px;border-radius:8px;font-size:12px;font-weight:600;
  font-family:'Consolas','SF Mono',monospace;
  box-shadow:0 6px 20px rgba(0,0,0,0.5);border:1px solid transparent;
  max-width:80%;pointer-events:none;
  transition:opacity 0.3s ease,transform 0.3s ease;
}
#toast.show{display:flex;animation:toastIn 0.25s ease forwards}
#toast.hide{opacity:0;transform:translateX(-50%) translateY(-8px)}
#toast.success{background:rgba(14,203,129,0.96);color:#0b0e11;border-color:#0ecb81}
#toast.error{background:rgba(246,70,93,0.96);color:#fff;border-color:#f6465d}
#toast.info{background:rgba(240,165,0,0.96);color:#0b0e11;border-color:#f0a500}
#toast .toast-icon{font-size:14px;line-height:1;font-weight:900}
@keyframes toastIn{
  from{opacity:0;transform:translateX(-50%) translateY(-12px)}
  to{opacity:1;transform:translateX(-50%) translateY(0)}
}
.chart-box{position:relative;flex:1}
.chart-box .title{
  position:absolute;top:6px;left:12px;z-index:10;
  font-family:'Consolas','SF Mono',monospace;
  font-size:11px;font-weight:700;padding:3px 9px;border-radius:4px;
  letter-spacing:.4px;
}
#infoPanel{
  position:absolute;bottom:24px;left:14px;z-index:10;
  font-family:'Consolas','SF Mono',monospace;
  background:rgba(13,16,20,0.92);backdrop-filter:blur(6px);
  padding:10px 12px;border-radius:8px;
  border:1px solid #2b3139;min-width:210px;
  box-shadow:0 8px 24px rgba(0,0,0,0.45);
}
#infoPanel .panelTitle{font-size:10px;font-weight:700;color:#5b6472;letter-spacing:.6px;margin-bottom:6px;text-transform:uppercase}
#infoPanel .lbl{font-size:10px;color:#848e9c}
#infoPanel .val{font-size:11px;color:#eaecef;font-weight:700}
#infoPanel .row{display:flex;justify-content:space-between;gap:14px;margin-top:3px}
#infoPanel .signal{margin-top:8px;font-size:12px;font-weight:800;text-align:center;padding:5px 0;border-radius:5px;letter-spacing:.3px}
#infoPanel .buy{color:#0ecb81;background:rgba(14,203,129,0.14)}
#infoPanel .sell{color:#f6465d;background:rgba(246,70,93,0.14)}
#infoPanel .none{color:#848e9c;background:rgba(132,142,156,0.10)}
#tradePanel{
  position:absolute;top:24px;left:14px;z-index:10;
  font-family:'Consolas','SF Mono',monospace;
  background:rgba(13,16,20,0.92);backdrop-filter:blur(6px);
  padding:10px 12px;border-radius:8px;
  border:1px solid #2b3139;min-width:210px;
  box-shadow:0 8px 24px rgba(0,0,0,0.45);
}
#tradePanel .panelTitle{font-size:10px;font-weight:700;color:#5b6472;letter-spacing:.6px;margin-bottom:6px;text-transform:uppercase}
#tradePanel .lbl{font-size:10px;color:#848e9c}
#tradePanel .val{font-size:11px;color:#eaecef;font-weight:700}
#tradePanel .row{display:flex;justify-content:space-between;gap:14px;margin-top:3px}
#tradePanel .posLong{color:#0ecb81}
#tradePanel .posFlat{color:#848e9c}
#tradePanel .pnlPos{color:#0ecb81}
#tradePanel .pnlNeg{color:#f6465d}
#tradePanel .pnlFlat{color:#848e9c}
#badge{
  position:absolute;top:6px;right:12px;z-index:10;
  display:inline-flex;align-items:center;gap:6px;
  padding:5px 12px;border-radius:5px;font-size:11px;font-weight:700;letter-spacing:.3px;
}
#badge.bull{background:rgba(14,203,129,0.16);color:#0ecb81;border:1px solid rgba(14,203,129,0.3)}
#badge.bear{background:rgba(246,70,93,0.16);color:#f6465d;border:1px solid rgba(246,70,93,0.3)}
#badge.wait{background:rgba(132,142,156,0.16);color:#848e9c;border:1px solid rgba(132,142,156,0.25)}
</style>
</head>
<body>
<div id="wrapper">
  <div id="header">
    <h1 id="symLabel">--</h1>
    <div id="legend">
      <span class="price" id="lPrice">--</span>
      <span><span class="lbl">O </span><span class="val" id="lO">--</span></span>
      <span><span class="lbl">H </span><span class="val" id="lH">--</span></span>
      <span><span class="lbl">L </span><span class="val" id="lL">--</span></span>
      <span><span class="lbl">C </span><span class="val" id="lC">--</span></span>
    </div>
  </div>
  <div id="charts">
    <div id="toast" class="info"><span class="toast-icon" id="toastIcon">&#9679;</span><span id="toastMsg">--</span></div>
    <div class="chart-box">
      <div id="fliChart" style="width:100%;height:100%;"></div>
      <div id="tradePanel">
        <div class="panelTitle">Trading</div>
        <div class="row"><span class="lbl">Mode:</span><span class="val" id="tMode">DEMO</span></div>
        <div class="row"><span class="lbl">Invest:</span><span class="val" id="tInvest">-- USDT</span></div>
        <div class="row"><span class="lbl">Style:</span><span class="val" id="tStyle">FIXED</span></div>
        <div class="row"><span class="lbl">Wallet:</span><span class="val" id="tWallet">-- USDT</span></div>
        <div class="row"><span class="lbl">Position:</span><span class="val posFlat" id="tPos">FLAT</span></div>
        <div class="row"><span class="lbl">Entry:</span><span class="val" id="tEntry">--</span></div>
        <div class="row"><span class="lbl">Qty:</span><span class="val" id="tQty">--</span></div>
        <div class="row"><span class="lbl">Unreal PnL:</span><span class="val pnlFlat" id="tUPnl">--</span></div>
        <div class="row"><span class="lbl">Day PnL:</span><span class="val pnlFlat" id="tDPnl">--</span></div>
        <div class="row"><span class="lbl">Total PnL:</span><span class="val pnlFlat" id="tTPnl">--</span></div>
      </div>
      <div id="infoPanel">
        <div class="panelTitle">Indicators</div>
        <div class="row"><span class="lbl">SAI Trend:</span><span class="val" id="iFLI">--</span></div>
        <div class="row"><span class="lbl">CCI:</span><span class="val" id="iCCI">--</span></div>
        <div class="row"><span class="lbl">ADX:</span><span class="val" id="iADX">--</span></div>
        <div class="row"><span class="lbl">OBV:</span><span class="val" id="iOBV">--</span></div>
        <div class="row"><span class="lbl">Score:</span><span class="val" id="iScore">--</span></div>
        <div class="row"><span class="lbl">BB Upper:</span><span class="val" id="iBBU">--</span></div>
        <div class="row"><span class="lbl">BB Lower:</span><span class="val" id="iBBL">--</span></div>
        <div class="signal none" id="iSignal">-- WAIT</div>
      </div>
      <div id="walletBuyPanel" style="display:none;position:absolute;bottom:24px;left:50%;transform:translateX(-50%);z-index:10;
        font-family:'Consolas','SF Mono',monospace;
        background:rgba(13,16,20,0.92);backdrop-filter:blur(6px);
        padding:10px 12px;border-radius:8px;
        border:1px solid #2b3139;min-width:220px;max-width:320px;
        box-shadow:0 8px 24px rgba(0,0,0,0.45);">
        <div class="panelTitle" style="font-size:10px;font-weight:700;color:#e040fb;letter-spacing:.6px;margin-bottom:6px;text-transform:uppercase">Wallet Purchase History</div>
        <div id="walletBuyList" style="max-height:120px;overflow-y:auto;font-size:10px;color:#eaecef;"></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:6px">
          <span style="font-size:10px;color:#848e9c">Total Buys:</span>
          <span style="font-size:11px;color:#e040fb;font-weight:700" id="wbTotalBuys">0</span>
        </div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px">
          <span style="font-size:10px;color:#848e9c">Total Qty:</span>
          <span style="font-size:11px;color:#eaecef;font-weight:700" id="wbTotalQty">0</span>
        </div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px">
          <span style="font-size:10px;color:#848e9c">Avg Price:</span>
          <span style="font-size:11px;color:#eaecef;font-weight:700" id="wbAvgPrice">--</span>
        </div>
      </div>
      <div id="btPanel" style="display:none;position:absolute;bottom:24px;right:14px;z-index:10;
        font-family:'Consolas','SF Mono',monospace;
        background:rgba(13,16,20,0.92);backdrop-filter:blur(6px);
        padding:10px 12px;border-radius:8px;
        border:1px solid #2b3139;min-width:185px;max-width:240px;
        box-shadow:0 8px 24px rgba(0,0,0,0.45);">
        <div class="panelTitle" style="font-size:10px;font-weight:700;color:#5b6472;letter-spacing:.6px;margin-bottom:6px;text-transform:uppercase">Backtest</div>
        <div id="btDurationRow" style="display:none;margin-top:3px"><span style="font-size:10px;color:#f0a500">Duration:</span><span style="font-size:11px;color:#f0a500;font-weight:700" id="btDuration">--</span></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px"><span style="font-size:10px;color:#848e9c">Trades:</span><span style="font-size:11px;color:#eaecef;font-weight:700" id="btTrades">0</span></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px"><span style="font-size:10px;color:#848e9c">Win Rate:</span><span style="font-size:11px;color:#eaecef;font-weight:700" id="btWinRate">0%</span></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px"><span style="font-size:10px;color:#848e9c">Wins:</span><span style="font-size:11px;font-weight:700" id="btWins">0</span></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px"><span style="font-size:10px;color:#848e9c">Losses:</span><span style="font-size:11px;font-weight:700" id="btLosses">0</span></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px"><span style="font-size:10px;color:#848e9c">Net P&L:</span><span style="font-size:11px;font-weight:700" id="btPnl">0.00%</span></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px"><span style="font-size:10px;color:#848e9c">Equity Final:</span><span style="font-size:11px;color:#2962ff;font-weight:700" id="btEquityFinal">$0.00</span></div>
        <div style="display:flex;justify-content:space-between;gap:14px;margin-top:3px"><span style="font-size:10px;color:#848e9c">Equity Peak:</span><span style="font-size:11px;color:#0ecb81;font-weight:700" id="btEquityPeak">$0.00</span></div>
      </div>
      <div class="badge wait" id="badge"><span id="badgeText">SCANNING...</span></div>
    </div>
  </div>
</div>
<script>
const C = {
  bg:'#0b0e11', surface:'#1e2329', border:'#2b3139', accent:'#f0a500',
  green:'#0ecb81', red:'#f6465d', blue:'#2196f3', text:'#eaecef',
  fliBuy:'#0ecb81', fliSell:'#f6465d'
};

const fliChart = LightweightCharts.createChart(
  document.getElementById('fliChart'), {
    layout:{background:{type:'solid',color:C.bg},textColor:'#848e9c',
            fontFamily:"'Consolas','SF Mono',monospace",fontSize:11},
    grid:{vertLines:{color:'rgba(43,49,57,0.2)'},horzLines:{color:'rgba(43,49,57,0.2)'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal,
      vertLine:{color:'rgba(240,165,0,0.3)',width:1,style:LightweightCharts.LineStyle.Dashed},
      horzLine:{color:'rgba(240,165,0,0.3)',width:1,style:LightweightCharts.LineStyle.Dashed}},
    rightPriceScale:{borderColor:C.border,scaleMargins:{top:0.08,bottom:0.08}},
    timeScale:{borderColor:C.border,timeVisible:true,secondsVisible:false,rightOffset:5},
  }
);

function _priceFmt(price){
  if(price>=1000) return price.toFixed(2);
  if(price>=1) return price.toFixed(4);
  if(price>=0.01) return price.toFixed(6);
  /* Small prices: strip leading zeros after decimal
     e.g. 0.00000010 -> "0.10" */
  var s=price.toFixed(8);
  var after=s.substring(2); /* digits after "0." */
  var i=0;
  while(i<after.length && after[i]==='0') i++;
  if(i>=after.length) return '0';
  var sig=after.substring(i).replace(/0+$/,'');
  if(sig.length<2) sig=after.substring(i,i+2);
  return '0.'+sig;
}
var _priceFmtObj={type:'custom',minMove:0.00000001,formatter:_priceFmt};

const fliCandles = fliChart.addCandlestickSeries({
  upColor:C.green, downColor:C.red,
  borderDownColor:C.red, borderUpColor:C.green,
  wickDownColor:C.red, wickUpColor:C.green,
  priceFormat: _priceFmtObj,
});

const fliSignalLine = fliChart.addLineSeries({
  color:C.fliBuy, lineWidth:2, priceLineVisible:false, lastValueVisible:false,
  priceFormat: _priceFmtObj,
});

// ── Alert price lines (horizontal dashed gray) ──
let _alertPriceLines = [];
function setAlertPriceLines(prices){
  clearAlertPriceLines();
  if(!prices||!prices.length)return;
  for(var i=0;i<prices.length;i++){
    var pl = fliCandles.createPriceLine({
      price: prices[i],
      color: 'rgba(138,143,160,0.45)',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'Alert ' + _priceFmt(prices[i]),
    });
    _alertPriceLines.push(pl);
  }
}
function clearAlertPriceLines(){
  for(var i=0;i<_alertPriceLines.length;i++){
    try{fliCandles.removePriceLine(_alertPriceLines[i]);}catch(e){}
  }
  _alertPriceLines = [];
}

const fliBBUpper = fliChart.addLineSeries({
  color:'rgba(240,165,0,0.28)', lineWidth:1, priceLineVisible:false, lastValueVisible:false,
});
const fliBBLower = fliChart.addLineSeries({
  color:'rgba(240,165,0,0.28)', lineWidth:1, priceLineVisible:false, lastValueVisible:false,
});

const $price=document.getElementById('lPrice');
const $O=document.getElementById('lO'),$H=document.getElementById('lH');
const $L=document.getElementById('lL'),$C_=document.getElementById('lC');
const $FLI=document.getElementById('iFLI'),$CCI=document.getElementById('iCCI');
const $ADX=document.getElementById('iADX'),$OBV=document.getElementById('iOBV');
const $Score=document.getElementById('iScore');
const $BBU=document.getElementById('iBBU'),$BBL=document.getElementById('iBBL');
const $Signal=document.getElementById('iSignal');
const $badge=document.getElementById('badge'),$badgeText=document.getElementById('badgeText');
const $tMode=document.getElementById('tMode'),$tInvest=document.getElementById('tInvest');
const $tStyle=document.getElementById('tStyle'),$tWallet=document.getElementById('tWallet');
const $tPos=document.getElementById('tPos'),$tEntry=document.getElementById('tEntry');
const $tQty=document.getElementById('tQty'),$tUPnl=document.getElementById('tUPnl');
const $tDPnl=document.getElementById('tDPnl'),$tTPnl=document.getElementById('tTPnl');

let _lastCandle=null, _allMarkers=[], _btMarkers=[], _walletBuyMarkers=[];

fliChart.subscribeCrosshairMove(p => {
  if(!p||!p.time){refreshLegend(_lastCandle);return;}
  const d=p.seriesData.get(fliCandles);
  if(d){_lastCandle=d;refreshLegend(d);}
});

function refreshLegend(d){
  if(!d)return;
  const c=d.close, col=c>=d.open?C.green:C.red;
  $price.textContent=c.toFixed(4); $price.style.color=col;
  $O.textContent=d.open.toFixed(4); $O.style.color=col;
  $H.textContent=d.high.toFixed(4); $H.style.color=col;
  $L.textContent=d.low.toFixed(4); $L.style.color=col;
  $C_.textContent=d.close.toFixed(4); $C_.style.color=col;
}

function setSymbol(t){document.getElementById('symLabel').textContent=t;}
function setFliCandles(d){fliCandles.setData(d);if(d.length)refreshLegend(d[d.length-1]);}
function setFliSignalLine(d){fliSignalLine.setData(d);}
function setFliBBUpper(d){fliBBUpper.setData(d);}
function setFliBBLower(d){fliBBLower.setData(d);}
function updateFliCandle(c){fliCandles.update(c);_lastCandle=c;refreshLegend(c);if(_autoScroll)fliChart.timeScale().scrollToRealTime();}

function setMarkers(markers){
  _allMarkers=markers.slice();
  _allMarkers.sort((a,b)=>a.time-b.time);
  fliCandles.setMarkers(_allMarkers);
}
function setBacktestMarkers(btMarkers){
  _btMarkers=btMarkers.slice();
  _mergeMarkers();
}

function setWalletBuyMarkers(markers){
  _walletBuyMarkers=markers.slice();
  _mergeMarkers();
}
function _mergeMarkers(){
  const combined=_allMarkers.slice().concat(_btMarkers.slice()).concat(_walletBuyMarkers.slice());
  combined.sort((a,b)=>a.time-b.time);
  fliCandles.setMarkers(combined);
}
function addMarker(m){
  const dup=_allMarkers.some(x=>x.time===m.time&&x.text===m.text);
  if(!dup){_allMarkers.push(m);_allMarkers.sort((a,b)=>a.time-b.time);fliCandles.setMarkers(_allMarkers);}
}
function clearAll(){
  fliCandles.setData([]);
  fliSignalLine.setData([]);fliBBUpper.setData([]);fliBBLower.setData([]);
  clearAlertPriceLines();
  _allMarkers=[];fliCandles.setMarkers([]);
}
function fitContent(){fliChart.timeScale().fitContent();}

let _autoScroll=true;
fliChart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
  if(!range)return;
  const bars=fliCandles.data();
  if(!bars.length)return;
  _autoScroll=(range.to>=bars.length-3);
});
function zoomToRecent(count){
  const bars=fliCandles.data();
  if(!bars.length){fliChart.timeScale().fitContent();return;}
  if(bars.length<=count){fliChart.timeScale().fitContent();return;}
  fliChart.timeScale().setVisibleLogicalRange({from:bars.length-count,to:bars.length-1});
}

new QWebChannel(qt.webChannelTransport, ch => { window.Qt = ch.objects.Qt; });

fliChart.subscribeClick(param => {
  if(!param || !param.point) return;
  // Use the exact Y price where user clicked (not candle close)
  const price = param.point.y || 0;
  if(typeof Qt !== 'undefined' && Qt.onChartCandleClick) {
    Qt.onChartCandleClick(param.time || 0, price);
  }
});

// Disable right-click context menu on chart
document.getElementById('fliChart').addEventListener('contextmenu', function(e){ e.preventDefault(); return false; });

document.getElementById('charts').addEventListener('contextmenu', function(e){ e.preventDefault(); return false; });

function updateUIState(fliTrend,cciVal,adxVal,obvDir,score,bbUpper,bbLower,signal){
  $FLI.textContent=fliTrend>0?'BULL':fliTrend<0?'BEAR':'FLAT';
  $FLI.style.color=fliTrend>0?C.green:fliTrend<0?C.red:'#848e9c';
  $CCI.textContent=isNaN(cciVal)?'--':cciVal.toFixed(1);
  $CCI.style.color=cciVal>110?C.green:cciVal<-110?C.red:'#eaecef';
  $ADX.textContent=isNaN(adxVal)?'--':adxVal.toFixed(1);
  $ADX.style.color=adxVal>24?C.green:'#f6465d';
  $OBV.textContent=obvDir>0?'Above SMA':obvDir<0?'Below SMA':'--';
  $OBV.style.color=obvDir>0?C.green:obvDir<0?C.red:'#848e9c';
  $Score.textContent=score+'/3';
  $Score.style.color=score>=2?C.green:score>=1?C.accent:'#848e9c';
  $BBU.textContent=bbUpper>0?bbUpper.toFixed(4):'--';
  $BBL.textContent=bbLower>0?bbLower.toFixed(4):'--';
  if(signal==='BUY'){
    $badge.className='badge bull';$badgeText.textContent='BUY SIGNAL';
    $Signal.className='signal buy';$Signal.textContent='BUY SIGNAL (SAI Confirmed)';
  }else if(signal==='SELL'){
    $badge.className='badge bear';$badgeText.textContent='SELL SIGNAL';
    $Signal.className='signal sell';$Signal.textContent='SELL SIGNAL (SAI Confirmed)';
  }else{
    $badge.className='badge wait';$badgeText.textContent='SCANNING...';
    $Signal.className='signal none';$Signal.textContent='-- WAIT';
  }
}

function updateTradePanel(mode,invest,style,wallet,inPos,entry,qty,uPnl,dPnl,tPnl){
  $tMode.textContent=mode;
  $tInvest.textContent=invest.toFixed(2)+' USDT';
  $tStyle.textContent=style;
  if(wallet===null||wallet===undefined||isNaN(wallet)){
    $tWallet.textContent='--';$tWallet.style.color='#848e9c';
  }else{
    $tWallet.textContent=wallet.toFixed(2)+' USDT';
    $tWallet.style.color=wallet>0?'#0ecb81':'#f6465d';
  }
  if(inPos){
    $tPos.textContent='LONG';$tPos.className='val posLong';
    $tEntry.textContent=entry>0?entry.toFixed(4):'--';
    $tQty.textContent=qty>0?qty.toFixed(6):'--';
  }else{
    $tPos.textContent='FLAT';$tPos.className='val posFlat';
    $tEntry.textContent='--';$tQty.textContent='--';
  }
  function fmtPnl(el,v){
    if(v===null||v===undefined||isNaN(v)){el.textContent='--';el.className='val pnlFlat';return;}
    el.textContent=(v>0?'+':'')+v.toFixed(4)+' USDT';
    el.className=v>0?'val pnlPos':v<0?'val pnlNeg':'val pnlFlat';
  }
  fmtPnl($tUPnl,uPnl);fmtPnl($tDPnl,dPnl);fmtPnl($tTPnl,tPnl);
}

const $toast=document.getElementById('toast');
const $toastMsg=document.getElementById('toastMsg');
const $toastIcon=document.getElementById('toastIcon');
let _toastTimer=null;
function updateWalletBuyPanel(buys, totalQty, avgPrice){
  document.getElementById('walletBuyPanel').style.display=buys.length>0?'block':'none';
  document.getElementById('wbTotalBuys').textContent=buys.length;
  document.getElementById('wbTotalQty').textContent=totalQty.toFixed(8);
  document.getElementById('wbAvgPrice').textContent=avgPrice>0?avgPrice.toFixed(6):'--';
  var html='';
  for(var i=0;i<buys.length;i++){
    var b=buys[i];
    html+='<div style="display:flex;justify-content:space-between;gap:8px;margin-top:2px;padding:2px 0;border-bottom:1px solid rgba(43,49,57,0.3)">';
    html+='<span style="color:#848e9c">'+b.date+'</span>';
    html+='<span style="color:#e040fb;font-weight:600">@ '+b.price.toFixed(6)+'</span>';
    html+='<span style="color:#eaecef">x'+b.qty.toFixed(8)+'</span>';
    html+='</div>';
  }
  document.getElementById('walletBuyList').innerHTML=html;
}

function updateBacktestStats(trades,winRate,wins,losses,pnl,eqFinal,eqPeak,duration){
  document.getElementById('btPanel').style.display=trades>0?'block':'none';
  document.getElementById('btTrades').textContent=trades;
  document.getElementById('btWinRate').textContent=winRate.toFixed(1)+'%';
  document.getElementById('btWinRate').style.color=winRate>=50?'#2962ff':'#ff6d00';
  document.getElementById('btWins').textContent=wins;
  document.getElementById('btWins').style.color='#2962ff';
  document.getElementById('btLosses').textContent=losses;
  document.getElementById('btLosses').style.color='#ff6d00';
  var $pnl=document.getElementById('btPnl');
  $pnl.textContent=(pnl>0?'+':'')+pnl.toFixed(2)+'%';
  $pnl.style.color=pnl>0?'#2962ff':pnl<0?'#ff6d00':'#848e9c';
  var $eqF=document.getElementById('btEquityFinal');
  $eqF.textContent='$'+eqFinal.toFixed(2);
  $eqF.style.color=eqFinal>=10?'#2962ff':'#ff6d00';
  var $eqP=document.getElementById('btEquityPeak');
  $eqP.textContent='$'+eqPeak.toFixed(2);
  $eqP.style.color='#0ecb81';
  if(duration && duration.length>0){
    document.getElementById('btDurationRow').style.display='block';
    document.getElementById('btDuration').textContent=duration;
  }else{
    document.getElementById('btDurationRow').style.display='none';
  }
}

function showToast(type,msg,duration){
  if(_toastTimer){clearTimeout(_toastTimer);_toastTimer=null;}
  $toast.className='show '+type;
  $toastIcon.textContent=type==='success'?'\\u2713':type==='error'?'\\u2715':'\\u25cf';
  $toastMsg.textContent=msg;
  if(duration>0)_toastTimer=setTimeout(hideToast,duration);
}
function hideToast(){$toast.className='';if(_toastTimer){clearTimeout(_toastTimer);_toastTimer=null;}}

new ResizeObserver(entries=>{
  for(const e of entries){
    const h=Math.floor(e.contentRect.height)-2;
    document.getElementById('fliChart').style.height=h+'px';
    fliChart.resize(e.contentRect.width,h);
  }
}).observe(document.getElementById('charts'));
var _pageReady = true;
</script>
</body>
</html>""".replace("__LW_CDN__", CHART_CDN_URL)


class FLIChartWorker(QThread):
    """Background computation of the FLI indicator set for the chart.

    Mirrors main.py's IndicatorWorker but is entirely separate from
    IndicatorCalcWorker/TradingEngine — it only feeds the chart, never
    trading decisions.

    Code Review Low #15: exposes a ``_running`` flag and ``stop()`` so the
    main thread can request a cooperative shutdown — otherwise an in-flight
    indicator computation could still be running when the user closes the
    tab or quits the app, leaving a dangling QThread.
    """

    fli_ready = Signal(object)
    fli_error = Signal(str)

    def __init__(self, candles, params, parent=None):
        super().__init__(parent)
        self.candles = candles
        self.params = params
        self._running = True

    def run(self):
        try:
            if not self._running:
                return
            df = fli_ohlcv_to_df(self.candles)
            if not self._running:
                return
            df = fli_compute_all_indicators(df, self.params)
            if not self._running:
                return
            self.fli_ready.emit(df)
        except Exception as e:
            if self._running:
                self.fli_error.emit(str(e))

    def stop(self):
        # Cooperative flag + Qt thread termination.
        self._running = False
        self.quit()
        self.wait(3000)
