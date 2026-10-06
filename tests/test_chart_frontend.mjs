import assert from 'node:assert/strict';
import { MarketChart, visibleRange, priceBounds, openingSpan } from '../service/static/market-chart.js';

// Empty data, a short session, aggressive zoom, and out-of-bounds panning.
assert.deepEqual(visibleRange(0, 100, 500), { start: 0, count: 0 });
assert.deepEqual(visibleRange(4, 30, 1000), { start: 0, count: 4 });
assert.deepEqual(visibleRange(195, -20, 60), { start: 0, count: 60 });
assert.deepEqual(visibleRange(195, 190, 60), { start: 135, count: 60 });
assert.deepEqual(visibleRange(1200, 0, 1200), { start: 0, count: 500 });
assert.deepEqual(visibleRange(195, 0, 1), { start: 0, count: 12 });

// Warm-up nulls must never pull a stock's displayed price axis down to zero.
const bars = [{ low: 99, high: 101, vwap: 100, ema_fast: null, ema_medium: null, ema_slow: null }];
let domain = priceBounds(bars);
assert.ok(domain.low > 98 && domain.high < 102);
assert.ok(domain.low < 99 && domain.high > 101);

// A visible overlay and an actual fill both belong in the y-axis domain.
domain = priceBounds([{ ...bars[0], ema_slow: 94 }]);
assert.ok(domain.low < 94);
assert.ok(priceBounds([{ ...bars[0], ema_slow: 94 }], ['vwap']).low > 98);
assert.ok(priceBounds(bars, [], [{ price: 103 }]).high > 103);

// Flat-price sessions still require a nonzero domain for finite SVG geometry.
domain = priceBounds([{ low: 100, high: 100 }]);
assert.ok(domain.low < 100 && domain.high > 100);
assert.deepEqual(priceBounds([]), { low: 0, high: 1 });

// A partial final candle must not overstate the configured entry window.
const session = Array.from({ length: 20 }, (_, index) => {
  const minutes = 570 + index * 2;
  return { timestamp: `2024-05-01T${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}:00` };
});
assert.deepEqual(openingSpan(session, 570, 15, 2), { start: 0, end: 7.5 });
assert.deepEqual(openingSpan(session, 570, 30, 2), { start: 0, end: 15 });
assert.deepEqual(openingSpan(session.slice(5), 570, 15, 2), { start: 0, end: 2.5 });
assert.equal(openingSpan(session.slice(8), 570, 15, 2), null);
assert.equal(openingSpan([{ timestamp: '2024-01-01' }], 570, 15, null), null);

// Exercise navigation and event association without depending on browser layout.
const chart = Object.create(MarketChart.prototype), inspections = [];
chart.render = () => {};
chart.onInspect = details => inspections.push(details);
chart.setData({ bars: session.map(bar => ({ ...bar, open: 100, high: 102, low: 99, close: 101 })),
  trades: [{ timestamp: '2024-05-01 09:34:00', signal_timestamp: '2024-05-01T09:32:00', action: 'BUY', quantity: 2, price: 101.03 }] });
const key = value => chart.keyDown({ key: value, preventDefault() {} });
key('Home'); key('ArrowLeft');
assert.equal(inspections.at(-1).index, 0);
key('End'); key('ArrowRight');
assert.equal(inspections.at(-1).index, session.length - 1);
chart.zoomIn();
key('Home');
assert.equal(chart.range.start, 0);
key('End');
assert.equal(chart.range.start + chart.range.count, session.length);
chart.inspect(1, 'keyboard');
assert.equal(inspections.at(-1).trades.length, 0);
chart.inspect(2, 'keyboard');
assert.equal(inspections.at(-1).trades[0].price, 101.03);
assert.equal(inspections.at(-1).bar.timestamp, '2024-05-01T09:34:00');

// Toolbar panning is an alternative to dragging, and cannot run past either end.
chart.range = { start: 0, count: 12 };
chart.panLeft();
assert.deepEqual(chart.range, { start: 0, count: 12 });
chart.panRight();
assert.deepEqual(chart.range, { start: 4, count: 12 });
assert.equal(inspections.at(-1).index, 15);
assert.equal(chart.inspected, null);
chart.panRight(); chart.panRight();
assert.deepEqual(chart.range, { start: 8, count: 12 });
assert.equal(inspections.at(-1).index, 19);
chart.panLeft();
assert.deepEqual(chart.range, { start: 4, count: 12 });

// Toolbar state follows the actual range, including keyboard and capped views.
const navigation = Object.create(MarketChart.prototype), states = [], quotes = [];
navigation.container = new EventTarget();
navigation.container.addEventListener('chartviewchange', event => states.push(event.detail));
navigation.render = () => navigation.publishViewState();
navigation.onInspect = details => quotes.push(details);
const candles = session.map(bar => ({ ...bar, open: 100, high: 102, low: 99, close: 101 }));
navigation.setData({ bars: candles, interval_minutes: 2, opening_window_minutes: 30 });
assert.deepEqual(states.at(-1), { opening: false, full: true, canPanLeft: false, canPanRight: false, canZoomIn: true, canZoomOut: false });
navigation.fitToOpeningWindow();
assert.deepEqual(states.at(-1), { opening: true, full: false, canPanLeft: false, canPanRight: true, canZoomIn: true, canZoomOut: true });
navigation.panRight();
assert.equal(states.at(-1).opening, false);
assert.equal(states.at(-1).canPanLeft, true);
assert.equal(states.at(-1).canPanRight, false);
navigation.zoomIn();
assert.equal(states.at(-1).canZoomIn, false);
assert.equal(quotes.at(-1).index, navigation.range.start + navigation.range.count - 1);
navigation.keyDown({ key: 'Home', preventDefault() {} });
assert.equal(states.at(-1).canPanLeft, false);
assert.equal(states.at(-1).canPanRight, true);
navigation.data.opening_window_minutes = 15;
navigation.fitToOpeningWindow();
assert.equal(states.at(-1).opening, true);
assert.equal(navigation.range.count, 12); // Minimum useful candle context is retained.
navigation.resetView();
assert.equal(states.at(-1).full, true);
assert.equal(states.at(-1).opening, false);
navigation.setData({ bars: candles, interval_minutes: null });
assert.equal(states.at(-1).opening, false);
assert.equal(states.at(-1).full, true);
navigation.setData({ bars: candles, interval_minutes: 2, opening_window_minutes: 0 });
assert.equal(states.at(-1).opening, false); // Non-VWAP strategy suppresses the opening view.
navigation.setData({ bars: Array.from({ length: 1000 }, (_, i) => candles[i % candles.length]) });
assert.equal(states.at(-1).full, false); // A render cap must not claim all history is visible.
assert.equal(states.at(-1).canZoomOut, false);
assert.equal(states.at(-1).canPanLeft, true);
navigation.setData(null);
assert.deepEqual(states.at(-1), { opening: false, full: false, canPanLeft: false, canPanRight: false, canZoomIn: false, canZoomOut: false });

// Shorter workbench charts retain a fixed 60px volume pane and a useful price pane.
for (const [width, height] of [[960, 330], [960, 380], [320, 320]]) {
  chart.container = { getBoundingClientRect: () => ({ width, height }) };
  chart.dimensions();
  assert.equal(chart.width, width);
  assert.equal(chart.height, height);
  assert.equal(chart.volumeBottom - chart.volumeTop, 60);
  assert.ok(chart.plot.height >= 194);
  assert.ok(chart.plot.bottom < chart.volumeTop);
  chart.bounds = { low: 98, high: 102 };
  assert.equal(chart.y(102), chart.plot.top);
  assert.equal(chart.y(98), chart.plot.bottom);
  assert.ok(chart.x(chart.range.start) > chart.plot.left);
  assert.ok(chart.x(chart.range.start + chart.range.count - 1) < chart.plot.right);
}
console.log('Chart checks passed: actual-range events, navigation limits, zoom quotes, compact plot geometry, null overlays, fill timestamps/prices, flat prices, exact opening windows.');
