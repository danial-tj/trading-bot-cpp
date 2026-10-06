/* Historical OHLC chart. Values always come from the saved run's data snapshot. */
const NS = 'http://www.w3.org/2000/svg';
const COLORS = {
  background: '#121416', grid: '#272c30', text: '#abb0b6', bright: '#e7e9eb',
  up: '#53b597', down: '#e77a80', vwap: '#e0b26d',
  ema_fast: '#97afc1', ema_medium: '#b4a6c6', ema_slow: '#c59676',
};
const INDICATORS = ['vwap', 'ema_fast', 'ema_medium', 'ema_slow'];
const MAX_VISIBLE = 500;
let chartSequence = 0;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const coordinate = value => Number(value.toFixed(2));
const keyFor = value => String(value || '').replace('T', ' ').slice(0, 19);

function svgNode(tag, attributes = {}, value) {
  const element = document.createElementNS(NS, tag);
  for (const [name, attribute] of Object.entries(attributes)) element.setAttribute(name, String(attribute));
  if (value !== undefined) element.textContent = String(value);
  return element;
}

function label(value, x, y, attributes = {}) {
  return svgNode('text', {
    x: coordinate(x), y: coordinate(y), fill: COLORS.text,
    'font-size': 12, 'font-family': 'ui-monospace, SFMono-Regular, Consolas, monospace',
    ...attributes,
  }, value);
}

function price(value) {
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 4 : 2 });
}

function volume(value) {
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return String(Math.round(value));
}

function minuteOf(timestamp) {
  const time = /[T ](\d\d):(\d\d)/.exec(String(timestamp));
  return time ? Number(time[1]) * 60 + Number(time[2]) : null;
}

function timeLabel(timestamp, daily = false, detail = false) {
  const text = String(timestamp);
  if (!daily) return detail ? text.slice(0, 16).replace('T', ' · ') : text.slice(11, 16);
  const [year, month, day] = text.slice(0, 10).split('-');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${day} ${months[Number(month) - 1] || month}${detail ? ` ${year}` : ''}`;
}

// Exported geometry helpers also keep the range and data-integrity checks testable.
export function visibleRange(total, start, count) {
  const size = Math.min(total, MAX_VISIBLE, Math.max(Math.min(12, total), Math.round(count)));
  return { start: clamp(Math.round(start), 0, Math.max(0, total - size)), count: size };
}

export function priceBounds(bars, indicators = INDICATORS, trades = []) {
  let low = Infinity, high = -Infinity;
  const include = value => { if (finite(value)) { low = Math.min(low, value); high = Math.max(high, value); } };
  for (const bar of bars) {
    include(bar.low); include(bar.high);
    for (const indicator of indicators) include(bar[indicator]);
  }
  for (const trade of trades) include(trade.price);
  if (!Number.isFinite(low)) return { low: 0, high: 1 };
  const padding = (high - low || Math.max(Math.abs(high) * .005, .01)) * .13;
  return { low: low - padding, high: high + padding };
}

// Fractional candle cells preserve a 15-minute cutoff even with two-minute bars.
export function openingSpan(bars, open, duration, interval) {
  if (!bars.length || !finite(interval) || interval <= 0) return null;
  const minutes = bars.map(bar => minuteOf(bar.timestamp));
  if (minutes.some(minute => minute == null)) return null;
  const position = minute => {
    const next = minutes.findIndex(value => value >= minute);
    if (next === 0) return (minute - minutes[0]) / interval;
    if (next < 0) return minutes.length - 1 + (minute - minutes.at(-1)) / interval;
    const prior = minutes[next - 1];
    return next - 1 + (minute - prior) / (minutes[next] - prior);
  };
  const start = clamp(position(open), 0, bars.length);
  const end = clamp(position(open + duration), 0, bars.length);
  return end > start ? { start, end } : null;
}

export class MarketChart {
  constructor(container, onInspect = () => {}) {
    this.container = container;
    this.onInspect = onInspect;
    this.id = `market-plot-${++chartSequence}`;
    this.bars = [];
    this.data = {};
    this.view = 'candles';
    this.indicators = Object.fromEntries(INDICATORS.map(name => [name, true]));
    this.range = { start: 0, count: 0 };
    this.inspected = null;
    this.pointerY = null;
    this.drag = null;
    this.handlers = [];
    container.tabIndex = 0;
    container.setAttribute('role', 'group');
    container.setAttribute('aria-label', 'Historical price chart. Arrow keys inspect candles. Plus and minus zoom. Home and End inspect the first and last bar. Zero resets the view.');
    this.svg = svgNode('svg', { width: '100%', height: 360, 'aria-hidden': 'true', class: 'market-svg' });
    container.replaceChildren(this.svg);
    this.listen('pointermove', event => this.pointerMove(event));
    this.listen('pointerleave', () => { if (!this.drag && document.activeElement !== this.container) this.clearInspection(); });
    this.listen('pointerdown', event => this.pointerDown(event));
    this.listen('pointerup', event => this.pointerUp(event));
    this.listen('pointercancel', event => this.pointerUp(event));
    this.listen('keydown', event => this.keyDown(event));
    this.listen('focus', () => { if (this.bars.length && this.inspected === null) this.inspect(this.range.start + this.range.count - 1, 'keyboard'); });
    this.listen('blur', () => this.clearInspection());
    this.listen('wheel', event => {
      if (!event.ctrlKey || document.activeElement !== container || !this.bars.length) return;
      event.preventDefault();
      this.zoom(event.deltaY < 0 ? .75 : 1.35);
    }, { passive: false });
    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(container);
    this.render();
  }

  listen(name, handler, options) {
    this.container.addEventListener(name, handler, options);
    this.handlers.push([name, handler, options]);
  }

  setData(payload) {
    this.data = payload || {};
    this.bars = (this.data.bars || []).filter(bar =>
      [bar.open, bar.high, bar.low, bar.close].every(finite));
    this.tradeMap = new Map();
    for (const trade of this.data.trades || []) {
      const key = keyFor(trade.timestamp);
      const entries = this.tradeMap.get(key) || [];
      entries.push(trade);
      this.tradeMap.set(key, entries);
    }
    this.inspected = null;
    this.pointerY = null;
    this.resetView();
  }

  setView(view) {
    if (!['candles', 'line'].includes(view)) return;
    this.view = view;
    this.render();
  }

  setIndicator(name, visible) {
    if (!INDICATORS.includes(name)) return;
    this.indicators[name] = Boolean(visible);
    this.render();
  }

  resetView() {
    this.range = visibleRange(this.bars.length, Math.max(0, this.bars.length - MAX_VISIBLE), this.bars.length);
    this.inspected = null;
    this.pointerY = null;
    this.render();
    this.publish(this.bars.length - 1, 'latest');
  }

  zoomIn() { this.zoom(.7); }
  zoomOut() { this.zoom(1.4); }
  panLeft() { this.pan(-1); }
  panRight() { this.pan(1); }

  pan(direction) {
    if (!this.bars.length) return;
    const step = Math.max(1, Math.round(this.range.count / 3));
    this.range = visibleRange(this.bars.length, this.range.start + direction * step, this.range.count);
    this.inspected = null;
    this.pointerY = null;
    this.render();
    this.publish(this.range.start + this.range.count - 1, 'latest');
  }

  zoom(factor) {
    if (!this.bars.length) return;
    const center = this.inspected ?? (this.range.start + this.range.count / 2);
    const position = (center - this.range.start) / this.range.count;
    const count = clamp(Math.round(this.range.count * factor), Math.min(12, this.bars.length), Math.min(MAX_VISIBLE, this.bars.length));
    this.range = visibleRange(this.bars.length, center - count * position, count);
    if (this.inspected !== null && (this.inspected < this.range.start || this.inspected >= this.range.start + this.range.count)) {
      this.inspected = null;
      this.pointerY = null;
    }
    this.render();
    this.publish(this.inspected ?? (this.range.start + this.range.count - 1), this.inspected === null ? 'latest' : 'inspection');
  }

  openingRange() {
    const window = this.data.opening_window_minutes ?? 30;
    if (!this.bars.length || !finite(this.data.interval_minutes) || this.data.interval_minutes <= 0 || window <= 0) return null;
    const open = this.data.session_open_minute ?? 570;
    let start = this.bars.findIndex(bar => minuteOf(bar.timestamp) >= open);
    if (start < 0) start = 0;
    let end = this.bars.findIndex((bar, i) => i >= start && minuteOf(bar.timestamp) >= open + window);
    if (end < 0) end = this.bars.length;
    // One following candle makes the boundary and any next-bar fill visible.
    return visibleRange(this.bars.length, start, end - start + 1);
  }

  fitToOpeningWindow() {
    const range = this.openingRange();
    if (!range) return;
    this.range = range;
    this.inspected = null;
    this.pointerY = null;
    this.render();
    this.publish(this.range.start + this.range.count - 1, 'latest');
  }

  viewState() {
    const total = this.bars.length, { start, count } = this.range, opening = this.openingRange();
    return {
      opening: Boolean(opening && start === opening.start && count === opening.count),
      full: total > 0 && start === 0 && count === total,
      canPanLeft: total > 0 && start > 0,
      canPanRight: total > 0 && start + count < total,
      canZoomIn: count > Math.min(12, total),
      canZoomOut: total > 0 && count < Math.min(MAX_VISIBLE, total),
    };
  }

  publishViewState() {
    this.container.dispatchEvent(new CustomEvent('chartviewchange', { detail: this.viewState() }));
  }

  dimensions() {
    const rect = this.container.getBoundingClientRect();
    this.width = Math.max(220, Math.floor(rect.width || 800));
    this.height = Math.max(320, Math.floor(rect.height || 360));
    this.volumeBottom = this.height - 30;
    this.volumeTop = this.volumeBottom - 60;
    this.plot = { left: 14, right: this.width - 78, top: 18, bottom: this.volumeTop - 18 };
    this.plot.width = this.plot.right - this.plot.left;
    this.plot.height = this.plot.bottom - this.plot.top;
    this.step = this.plot.width / Math.max(1, this.range.count);
  }

  x(index) { return this.plot.left + (index - this.range.start + .5) * this.step; }
  y(value) { return this.plot.bottom - (value - this.bounds.low) / (this.bounds.high - this.bounds.low) * this.plot.height; }

  render() {
    this.dimensions();
    const { width, height, plot } = this;
    this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    this.svg.setAttribute('height', String(height));
    this.svg.replaceChildren(svgNode('rect', { width, height, fill: COLORS.background }));
    if (!this.bars.length) {
      this.svg.append(label('No price data for this session', width / 2, height / 2, { 'text-anchor': 'middle', 'font-size': 13 }));
      this.publishViewState();
      return;
    }
    const visible = this.bars.slice(this.range.start, this.range.start + this.range.count);
    const fills = visible.flatMap(bar => this.tradeMap?.get(keyFor(bar.timestamp)) || []);
    this.bounds = priceBounds(visible, INDICATORS.filter(name => this.indicators[name]), fills);
    const definitions = svgNode('defs');
    const clip = svgNode('clipPath', { id: this.id });
    clip.append(svgNode('rect', { x: plot.left, y: plot.top, width: plot.width, height: this.volumeBottom - plot.top }));
    definitions.append(clip);
    this.svg.append(definitions);
    this.renderGrid(visible);
    const content = svgNode('g', { 'clip-path': `url(#${this.id})` });
    this.svg.append(content);
    this.renderOpening(content, visible);
    this.renderPrices(content, visible);
    this.renderIndicators(content, visible);
    this.renderVolume(content, visible);
    this.renderTrades(content, visible);
    this.renderLastPrice(visible.at(-1));
    this.svg.append(label('VOL', plot.left + 2, this.volumeTop - 5, { 'font-size': 11 }));
    this.svg.append(label(volume(Math.max(...visible.map(bar => bar.volume || 0))), plot.right + 10, this.volumeTop + 11, { 'font-size': 11 }));
    this.crosshair = svgNode('g', { 'pointer-events': 'none' });
    this.svg.append(this.crosshair);
    this.renderCrosshair();
    this.publishViewState();
  }

  renderGrid(visible) {
    const { plot, bounds } = this;
    const rough = (bounds.high - bounds.low) / 5;
    const power = 10 ** Math.floor(Math.log10(rough));
    const scaled = rough / power;
    const tick = (scaled > 5 ? 10 : scaled > 2 ? 5 : scaled > 1 ? 2 : 1) * power;
    for (let value = Math.ceil(bounds.low / tick) * tick; value <= bounds.high; value += tick) {
      const y = this.y(value);
      this.svg.append(svgNode('line', { x1: plot.left, x2: plot.right, y1: coordinate(y), y2: coordinate(y), stroke: COLORS.grid }));
      this.svg.append(label(price(value), plot.right + 10, y + 4));
    }
    const tickCount = Math.max(2, Math.floor(plot.width / 105));
    const used = new Set();
    for (let n = 0; n < tickCount; n++) {
      const local = Math.round(n * (visible.length - 1) / (tickCount - 1));
      if (used.has(local)) continue;
      used.add(local);
      const x = this.x(this.range.start + local);
      this.svg.append(svgNode('line', { x1: coordinate(x), x2: coordinate(x), y1: plot.top, y2: this.volumeBottom, stroke: COLORS.grid, 'stroke-dasharray': '2 4' }));
      this.svg.append(label(timeLabel(visible[local].timestamp, this.data.interval_minutes == null), clamp(x, plot.left + 23, plot.right - 23), this.height - 10, { 'text-anchor': 'middle', 'font-size': 11 }));
    }
    this.svg.append(svgNode('line', { x1: plot.right, x2: plot.right, y1: 0, y2: this.height, stroke: COLORS.grid }));
    this.svg.append(svgNode('line', { x1: plot.left, x2: plot.right, y1: this.volumeTop - 17, y2: this.volumeTop - 17, stroke: COLORS.grid }));
    this.svg.append(svgNode('line', { x1: plot.left, x2: plot.right, y1: this.volumeBottom + 1, y2: this.volumeBottom + 1, stroke: COLORS.grid }));
  }

  renderOpening(content, visible) {
    if (this.data.interval_minutes == null || !this.data.opening_window_minutes) return;
    const open = this.data.session_open_minute ?? 570;
    const span = openingSpan(visible, open, this.data.opening_window_minutes, this.data.interval_minutes);
    if (!span) return;
    const left = this.plot.left + span.start * this.step;
    const right = this.plot.left + span.end * this.step;
    content.append(svgNode('rect', { x: coordinate(left), y: this.plot.top, width: coordinate(right - left), height: this.volumeBottom - this.plot.top, fill: COLORS.vwap, opacity: .045 }));
    content.append(svgNode('line', { x1: coordinate(right), x2: coordinate(right), y1: this.plot.top, y2: this.volumeBottom, stroke: COLORS.vwap, opacity: .5, 'stroke-dasharray': '3 5' }));
    if (right - left > 76) content.append(label(`OPEN ${this.data.opening_window_minutes}m`, left + 6, this.plot.top + 15, { fill: COLORS.vwap, 'font-size': 11 }));
  }

  renderPrices(content, bars) {
    if (this.view === 'line') {
      const points = bars.map((bar, local) => `${local ? 'L' : 'M'}${coordinate(this.x(this.range.start + local))},${coordinate(this.y(bar.close))}`).join(' ');
      const area = `${points} L${coordinate(this.x(this.range.start + bars.length - 1))},${this.plot.bottom} L${coordinate(this.x(this.range.start))},${this.plot.bottom}Z`;
      content.append(svgNode('path', { d: area, fill: COLORS.ema_fast, opacity: .055 }));
      content.append(svgNode('path', { d: points, fill: 'none', stroke: COLORS.bright, 'stroke-width': 1.7, 'stroke-linejoin': 'round' }));
      return;
    }
    const paths = { upWick: '', downWick: '', upBody: '', downBody: '' };
    const candleWidth = Math.max(1, Math.min(16, this.step * .68));
    for (const [local, bar] of bars.entries()) {
      const direction = bar.close >= bar.open ? 'up' : 'down';
      const x = this.x(this.range.start + local);
      const top = Math.min(this.y(bar.open), this.y(bar.close));
      const bodyHeight = Math.max(1.25, Math.abs(this.y(bar.open) - this.y(bar.close)));
      paths[`${direction}Wick`] += `M${coordinate(x)},${coordinate(this.y(bar.high))}V${coordinate(this.y(bar.low))}`;
      paths[`${direction}Body`] += `M${coordinate(x - candleWidth / 2)},${coordinate(top)}h${coordinate(candleWidth)}v${coordinate(bodyHeight)}h-${coordinate(candleWidth)}Z`;
    }
    for (const direction of ['up', 'down']) {
      content.append(svgNode('path', { d: paths[`${direction}Wick`], fill: 'none', stroke: COLORS[direction], 'stroke-width': 1 }));
      content.append(svgNode('path', { d: paths[`${direction}Body`], fill: direction === 'up' ? COLORS.background : COLORS.down, stroke: COLORS[direction], 'stroke-width': 1 }));
    }
  }

  renderIndicators(content, bars) {
    for (const name of INDICATORS) {
      if (!this.indicators[name]) continue;
      let path = '', continuing = false;
      for (const [local, bar] of bars.entries()) {
        if (!finite(bar[name])) { continuing = false; continue; }
        path += `${continuing ? 'L' : 'M'}${coordinate(this.x(this.range.start + local))},${coordinate(this.y(bar[name]))}`;
        continuing = true;
      }
      content.append(svgNode('path', { d: path, fill: 'none', stroke: COLORS[name], 'stroke-width': name === 'vwap' ? 1.7 : 1.2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'stroke-dasharray': name === 'ema_slow' ? '5 3' : 'none', opacity: name === 'vwap' ? 1 : .82 }));
    }
  }

  renderVolume(content, bars) {
    const max = Math.max(1, ...bars.map(bar => bar.volume || 0));
    const width = Math.max(1, Math.min(16, this.step * .68));
    const paths = { up: '', down: '' };
    for (const [local, bar] of bars.entries()) {
      const size = Math.max(0, (bar.volume || 0) / max) * (this.volumeBottom - this.volumeTop);
      const x = this.x(this.range.start + local) - width / 2;
      paths[bar.close >= bar.open ? 'up' : 'down'] += `M${coordinate(x)},${this.volumeBottom}v-${coordinate(size)}h${coordinate(width)}v${coordinate(size)}Z`;
    }
    for (const direction of ['up', 'down']) content.append(svgNode('path', { d: paths[direction], fill: COLORS[direction], opacity: .32 }));
  }

  renderTrades(content, bars) {
    for (const [local, bar] of bars.entries()) {
      const trades = this.tradeMap?.get(keyFor(bar.timestamp)) || [];
      for (const [position, trade] of trades.entries()) {
        if (!finite(trade.price)) continue;
        const buy = trade.action === 'BUY', color = buy ? COLORS.up : COLORS.down;
        const x = this.x(this.range.start + local), y = this.y(trade.price);
        const markerY = clamp(y + (buy ? 22 + position * 19 : -22 - position * 19), this.plot.top + 24, this.plot.bottom - 14);
        const group = svgNode('g');
        group.append(svgNode('title', {}, `${trade.action} ${trade.quantity} at ${price(trade.price)} · ${trade.timestamp}`));
        group.append(svgNode('line', { x1: coordinate(x), x2: coordinate(x), y1: coordinate(y), y2: coordinate(markerY), stroke: color, opacity: .7 }));
        group.append(svgNode('circle', { cx: coordinate(x), cy: coordinate(y), r: 2.5, fill: color, stroke: COLORS.background, 'stroke-width': 1 }));
        group.append(svgNode('rect', { x: coordinate(x - 9), y: coordinate(markerY - 9), width: 18, height: 18, rx: 3, fill: color }));
        group.append(label(buy ? 'B' : 'S', x, markerY + 4, { 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: COLORS.background }));
        content.append(group);
      }
    }
  }

  renderLastPrice(bar) {
    const y = this.y(bar.close), color = bar.close >= bar.open ? COLORS.up : COLORS.down;
    this.svg.append(svgNode('line', { x1: this.plot.left, x2: this.plot.right, y1: coordinate(y), y2: coordinate(y), stroke: color, 'stroke-dasharray': '2 4', opacity: .52 }));
    this.svg.append(svgNode('rect', { x: this.plot.right + 1, y: coordinate(y - 11), width: 76, height: 22, fill: color }));
    this.svg.append(label(price(bar.close), this.plot.right + 7, y + 4, { fill: COLORS.background, 'font-weight': 700 }));
  }

  renderCrosshair() {
    if (!this.crosshair) return;
    this.crosshair.replaceChildren();
    if (this.inspected === null || this.inspected < this.range.start || this.inspected >= this.range.start + this.range.count) return;
    const bar = this.bars[this.inspected];
    const x = this.x(this.inspected), y = clamp(this.pointerY ?? this.y(bar.close), this.plot.top, this.plot.bottom);
    const value = this.bounds.high - (y - this.plot.top) / this.plot.height * (this.bounds.high - this.bounds.low);
    this.crosshair.append(svgNode('line', { x1: coordinate(x), x2: coordinate(x), y1: this.plot.top, y2: this.volumeBottom, stroke: '#858b94', 'stroke-dasharray': '4 4' }));
    this.crosshair.append(svgNode('line', { x1: this.plot.left, x2: this.plot.right, y1: coordinate(y), y2: coordinate(y), stroke: '#858b94', 'stroke-dasharray': '4 4' }));
    this.crosshair.append(svgNode('circle', { cx: coordinate(x), cy: coordinate(this.y(bar.close)), r: 3, fill: COLORS.bright, stroke: COLORS.background, 'stroke-width': 1.5 }));
    this.crosshair.append(svgNode('rect', { x: this.plot.right + 1, y: coordinate(y - 11), width: 76, height: 22, fill: '#4b5057' }));
    this.crosshair.append(label(price(value), this.plot.right + 7, y + 4, { fill: '#ffffff' }));
    const text = timeLabel(bar.timestamp, this.data.interval_minutes == null, true);
    const badgeWidth = Math.min(this.plot.width, text.length * 6.8 + 14);
    const badgeX = clamp(x - badgeWidth / 2, this.plot.left, this.plot.right - badgeWidth);
    this.crosshair.append(svgNode('rect', { x: coordinate(badgeX), y: this.height - 28, width: coordinate(badgeWidth), height: 26, rx: 2, fill: '#3b3f45' }));
    this.crosshair.append(label(text, badgeX + badgeWidth / 2, this.height - 11, { 'text-anchor': 'middle', 'font-size': 11, fill: '#ffffff' }));
  }

  publish(index, source) {
    if (!this.bars[index]) return;
    const bar = this.bars[index], previousClose = this.bars[index - 1]?.close ?? bar.open;
    const change = bar.close - previousClose;
    this.onInspect({ bar, index, previousClose, change, changePercent: previousClose ? change / previousClose * 100 : 0, trades: this.tradeMap?.get(keyFor(bar.timestamp)) || [], source });
  }

  inspect(index, source, y = null) {
    if (!this.bars.length) return;
    this.inspected = clamp(index, 0, this.bars.length - 1);
    this.pointerY = y;
    this.renderCrosshair();
    this.publish(this.inspected, source);
  }

  clearInspection() {
    this.inspected = null;
    this.pointerY = null;
    this.renderCrosshair();
    this.publish(this.range.start + this.range.count - 1, 'latest');
  }

  pointerPosition(event) {
    const rect = this.svg.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * this.width / rect.width, y: (event.clientY - rect.top) * this.height / rect.height };
  }

  pointerDown(event) {
    if (event.button !== 0 || !this.bars.length) return;
    const { x, y } = this.pointerPosition(event);
    if (x < this.plot.left || x > this.plot.right || y < this.plot.top || y > this.volumeBottom) return;
    this.container.focus({ preventScroll: true });
    this.drag = { x, start: this.range.start, pointerId: event.pointerId };
    if (event.pointerType !== 'touch') this.container.setPointerCapture(event.pointerId);
    this.inspect(clamp(this.range.start + Math.floor((x - this.plot.left) / this.step), this.range.start, this.range.start + this.range.count - 1), 'pointer', clamp(y, this.plot.top, this.plot.bottom));
  }

  pointerMove(event) {
    if (!this.bars.length) return;
    const { x, y } = this.pointerPosition(event);
    if (this.drag && event.pointerType !== 'touch') {
      const shift = Math.round((this.drag.x - x) / this.step);
      this.range = visibleRange(this.bars.length, this.drag.start + shift, this.range.count);
      this.render();
    }
    if (x < this.plot.left || x > this.plot.right || y < this.plot.top || y > this.volumeBottom) return;
    this.inspect(clamp(this.range.start + Math.floor((x - this.plot.left) / this.step), this.range.start, this.range.start + this.range.count - 1), 'pointer', clamp(y, this.plot.top, this.plot.bottom));
  }

  pointerUp(event) {
    if (this.container.hasPointerCapture(event.pointerId)) this.container.releasePointerCapture(event.pointerId);
    this.drag = null;
  }

  keyDown(event) {
    if (!this.bars.length || event.altKey || event.metaKey || event.ctrlKey) return;
    if (['+', '='].includes(event.key)) { event.preventDefault(); this.zoomIn(); return; }
    if (event.key === '-') { event.preventDefault(); this.zoomOut(); return; }
    if (event.key === '0') { event.preventDefault(); this.resetView(); return; }
    if (event.key === 'Escape') { this.clearInspection(); return; }
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    let next = this.inspected ?? (this.range.start + this.range.count - 1);
    if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = this.bars.length - 1;
    else next += (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 10 : 1);
    next = clamp(next, 0, this.bars.length - 1);
    if (next < this.range.start) this.range.start = next;
    if (next >= this.range.start + this.range.count) this.range.start = next - this.range.count + 1;
    this.pointerY = null;
    this.render();
    this.inspect(next, 'keyboard');
  }

  destroy() {
    this.resizeObserver.disconnect();
    for (const [name, handler, options] of this.handlers) this.container.removeEventListener(name, handler, options);
    this.container.replaceChildren();
  }
}
