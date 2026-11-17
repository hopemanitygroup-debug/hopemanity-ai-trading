const ALLOWED_ORIGIN = "https://hopemanitygroup-debug.github.io";
const MAX_OPEN_PER_SIDE = 40;
const MAX_OPEN_TOTAL = 80;
const MAX_CLOSED = 500;
const MAX_EVENTS = 500;

function blankState(balance) {
  return {
    startBalance: balance, realized: 0, peakEquity: balance, maxDrawdown: 0,
    lastPrice: 0, lastTradeTime: 0, tickCount: 0, events: [], prices: [],
    closedPositions: [], runActiveMs: 0, runLastStartedAt: null,
    sides: { long: { pending: [], positions: [], center: 0 }, short: { pending: [], positions: [], center: 0 } },
    initialized: false, running: false, halted: false, haltReason: "",
    reentryUntil: 0, lastGridRefreshAt: 0, lastSavedAt: 0, connected: false
  };
}
function cleanConfig(c) {
  const out = {
    startBalance: Number(c.startBalance), positionUsd: Number(c.positionUsd),
    levels: Math.floor(Number(c.levels)), stepUsd: Number(c.stepUsd),
    feePercent: Number(c.feePercent), slippagePercent: Number(c.slippagePercent),
    profitUsd: Number(c.profitUsd), stopLossUsd: Number(c.stopLossUsd),
    sessionLossPct: Number(c.sessionLossPct), exposurePct: Number(c.exposurePct)
  };
  if (Object.values(out).some(x => !Number.isFinite(x)) || out.startBalance <= 0 ||
      out.positionUsd <= 0 || out.levels < 1 || out.levels > 50 || out.stepUsd <= 0 ||
      out.feePercent < 0 || out.slippagePercent < 0 || out.profitUsd <= 0 ||
      out.stopLossUsd <= 0 || out.sessionLossPct <= 0 || out.exposurePct <= 0 || out.exposurePct > 500) {
    throw new Error("Проверь настройки PAPER: некорректное или отрицательное значение.");
  }
  out.feeRate = out.feePercent / 100;
  out.slipRate = out.slippagePercent / 100;
  return out;
}
export class PaperBot {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.state = null;
    this.cfg = null;
    this.orderId = 0;
    this.feed = null;
    this.queue = Promise.resolve();
    this.loading = this.load();
  }
  async load() {
    this.state = await this.ctx.storage.get("state") || blankState(10000);
    this.cfg = await this.ctx.storage.get("cfg") || null;
    this.orderId = Number(await this.ctx.storage.get("orderId")) || 0;
    if (this.state.running) {
      this.state.connected = false;
      this.ensureAlarm(5000);
    }
  }
  async persist() {
    await this.ctx.storage.put({ state: this.state, cfg: this.cfg, orderId: this.orderId });
  }
  ensureAlarm(delay) {
    this.ctx.storage.setAlarm(Date.now() + delay).catch(() => {});
  }
  async fetch(request) {
    await this.loading;
    const url = new URL(request.url);
    const method = request.method.toUpperCase();
    if (method === "GET" && url.pathname === "/state") return this.json(this.snapshot());
    if (method === "POST" && url.pathname === "/start") {
      const body = await request.json();
      if (this.state.halted) return this.json({ error: "Сессия остановлена по лимиту убытка. Сбросьте PAPER перед новым запуском." }, 409);
      if (!this.state.initialized) {
        try { this.cfg = cleanConfig(body.config || {}); } catch (e) { return this.json({ error: e.message }, 400); }
        this.state = blankState(this.cfg.startBalance);
      } else if (!this.cfg) {
        try { this.cfg = cleanConfig(body.config || {}); } catch (e) { return this.json({ error: e.message }, 400); }
      }
      this.state.running = true;
      if (!this.state.runLastStartedAt) this.state.runLastStartedAt = Date.now();
      await this.persist();
      this.ensureAlarm(5 * 60 * 1000);
      this.ctx.waitUntil(this.connectFeed());
      return this.json(this.snapshot());
    }
    if (method === "POST" && url.pathname === "/stop") {
      this.stopRuntime();
      await this.persist();
      return this.json(this.snapshot());
    }
    if (method === "POST" && url.pathname === "/reset") {
      if (this.state.running) return this.json({ error: "Сначала остановите PAPER." }, 409);
      const body = await request.json().catch(() => ({}));
      const bal = Number(body.startBalance) || (this.cfg && this.cfg.startBalance) || this.state.startBalance || 10000;
      this.closeFeed();
      this.state = blankState(bal); this.cfg = null; this.orderId = 0;
      await this.persist();
      return this.json(this.snapshot());
    }
    if (method === "POST" && url.pathname === "/flatten") {
      if (!this.state.lastPrice) return this.json({ error: "Пока нет котировки для закрытия." }, 409);
      this.closeAll(this.state.lastPrice, "Закрыто вручную в PAPER", Date.now());
      await this.persist();
      return this.json(this.snapshot());
    }
    const closeMatch = url.pathname.match(/^\/close\/(\d+)$/);
    if (method === "POST" && closeMatch) {
      const id = Number(closeMatch[1]);
      let done = false;
      for (const side of ["long", "short"]) {
        const ix = this.state.sides[side].positions.findIndex(p => p.id === id);
        if (ix >= 0) { this.closePosition(side, ix, this.state.lastPrice, "Закрыто вручную в PAPER", Date.now()); done = true; break; }
      }
      if (!done) return this.json({ error: "Позиция уже закрыта или не найдена." }, 404);
      await this.persist();
      return this.json(this.snapshot());
    }
    return this.json({ error: "Не найден маршрут." }, 404);
  }
  json(data, status) {
    return Response.json(data, { status: status || 200, headers: { "Cache-Control": "no-store" } });
  }
  equity() { return this.state.startBalance + this.state.realized + this.floating(); }
  exposure() {
    let n = 0;
    for (const s of ["long", "short"]) for (const p of this.state.sides[s].positions) n += p.notional;
    return n;
  }
  openCount() { return this.state.sides.long.positions.length + this.state.sides.short.positions.length; }
  positionPnl(p, market) {
    const exit = p.side === "long" ? market * (1 - this.cfg.slipRate) : market * (1 + this.cfg.slipRate);
    const gross = p.side === "long" ? p.qty * (exit - p.entryPrice) : p.qty * (p.entryPrice - exit);
    return gross - p.entryFee - p.qty * exit * this.cfg.feeRate;
  }
  floating() {
    if (!this.state.lastPrice || !this.cfg) return 0;
    return ["long", "short"].reduce((sum, s) => sum + this.state.sides[s].positions.reduce((a, p) => a + this.positionPnl(p, this.state.lastPrice), 0), 0);
  }
  addEvent(type, side, price, count, pnl, reason, time) {
    this.state.events.unshift({ time: time || Date.now(), type, side, price: price || 0, count: count || 0, pnl: pnl == null ? null : pnl, reason: reason || "" });
    if (this.state.events.length > MAX_EVENTS) this.state.events.length = MAX_EVENTS;
  }
  placeGrid(side, center) {
    const dir = side === "long" ? 1 : -1;
    const levels = [];
    for (let i = 1; i <= this.cfg.levels; i++) levels.push({ level: i, price: center + dir * this.cfg.stepUsd * i });
    this.state.sides[side].center = center;
    this.state.sides[side].pending = levels;
  }
  initializeGrid(price, ts) {
    this.state.initialized = true;
    this.placeGrid("long", price); this.placeGrid("short", price);
    this.state.lastGridRefreshAt = Date.now();
    this.addEvent("Сетка выставлена", "LONG / SHORT", price, this.cfg.levels * 2, null, "Поток цен обрабатывается сервером", ts);
  }
  openPosition(side, level, price, ts) {
    if (this.openCount() >= MAX_OPEN_TOTAL || this.state.sides[side].positions.length >= MAX_OPEN_PER_SIDE) return false;
    const base = side === "long" ? Math.max(price, level.price) : Math.min(price, level.price);
    const fill = side === "long" ? base * (1 + this.cfg.slipRate) : base * (1 - this.cfg.slipRate);
    const qty = this.cfg.positionUsd / fill, notional = qty * fill;
    if (this.exposure() + notional > Math.max(0, this.equity() * this.cfg.exposurePct / 100)) return false;
    this.state.sides[side].positions.push({
      id: ++this.orderId, side, qty, notional, entryPrice: fill,
      entryFee: notional * this.cfg.feeRate, openedAt: ts, level: level.level
    });
    return true;
  }
  processEntries(side, price, ts) {
    let opened = 0, skipped = 0; const remain = [];
    for (const level of this.state.sides[side].pending) {
      const hit = side === "long" ? price >= level.price : price <= level.price;
      if (!hit) { remain.push(level); continue; }
      if (this.openPosition(side, level, price, ts)) opened++; else skipped++;
    }
    this.state.sides[side].pending = remain;
    if (opened) this.addEvent("Открыта сетка", side.toUpperCase(), price, opened, null, "Объём позиции задан в USDT", ts);
    if (skipped) this.addEvent("Уровни пропущены", side.toUpperCase(), price, skipped, null, "Лимит позиций или экспозиции", ts);
  }
  closePosition(side, ix, price, reason, ts) {
    const arr = this.state.sides[side].positions, p = arr[ix];
    if (!p || !this.cfg) return 0;
    const pnl = this.positionPnl(p, price);
    arr.splice(ix, 1); this.state.realized += pnl;
    this.state.closedPositions.unshift({
      side, entryPrice: p.entryPrice, exitPrice: price, notional: p.notional,
      qty: p.qty, pnl, closedAt: ts || Date.now(), openedAt: p.openedAt, reason
    });
    if (this.state.closedPositions.length > MAX_CLOSED) this.state.closedPositions.length = MAX_CLOSED;
    this.addEvent("Позиция закрыта", side.toUpperCase(), price, 1, pnl, reason, ts);
    return pnl;
  }
  closeAll(price, reason, ts) {
    let n = 0, pnl = 0;
    for (const side of ["long", "short"]) {
      for (let i = this.state.sides[side].positions.length - 1; i >= 0; i--) {
        pnl += this.closePosition(side, i, price, reason, ts); n++;
      }
      this.state.sides[side].pending = [];
    }
    if (n) this.addEvent("Корзина закрыта", "LONG / SHORT", price, n, pnl, reason, ts);
    return pnl;
  }
  stopRuntime() {
    if (this.state.runLastStartedAt) this.state.runActiveMs += Math.max(0, Date.now() - this.state.runLastStartedAt);
    this.state.runLastStartedAt = null; this.state.running = false; this.state.connected = false;
    this.closeFeed();
  }
  closeFeed() {
    const ws = this.feed; this.feed = null;
    if (ws) { try { ws.close(1000, "PAPER stopped"); } catch (_) {} }
  }
  async connectFeed() {
    await this.loading;
    if (!this.state.running || (this.feed && this.feed.readyState === 1)) return;
    try {
      const response = await fetch("https://stream.binance.com:9443/ws/btcusdt@trade", { headers: { Upgrade: "websocket" } });
      if (!response.webSocket) throw new Error("Binance WebSocket handshake failed: " + response.status);
      const ws = response.webSocket;
      ws.accept();
      this.feed = ws; this.state.connected = true; this.state.feedError = "";
      ws.addEventListener("message", event => {
        this.queue = this.queue.then(() => this.handleTrade(event.data)).catch(() => {});
      });
      ws.addEventListener("close", () => {
        if (this.feed === ws) this.feed = null;
        this.state.connected = false;
        if (this.state.running) { this.state.feedError = "Поток отключён, выполняется переподключение"; this.ensureAlarm(5000); }
        this.persist().catch(() => {});
      });
      ws.addEventListener("error", () => {
        this.state.connected = false; this.state.feedError = "Ошибка потока Binance";
        if (this.state.running) this.ensureAlarm(5000);
      });
      this.ensureAlarm(23 * 60 * 60 * 1000);
      await this.persist();
    } catch (error) {
      this.state.connected = false; this.state.feedError = String(error && error.message || error);
      if (this.state.running) this.ensureAlarm(15000);
      await this.persist();
    }
  }
  async alarm() {
    await this.loading;
    if (!this.state.running) return;
    if (this.feed && this.feed.readyState === 1 && this.state.connected) {
      this.ensureAlarm(23 * 60 * 60 * 1000);
      return;
    }
    await this.connectFeed();
  }
  async handleTrade(raw) {
    if (!this.state.running || !this.cfg) return;
    let msg; try { msg = JSON.parse(raw); } catch (_) { return; }
    const price = Number(msg.p), ts = Number(msg.T) || Date.now();
    if (!(price > 0)) return;
    this.state.lastPrice = price; this.state.lastTradeTime = ts; this.state.tickCount++;
    this.state.prices.push(price); if (this.state.prices.length > 240) this.state.prices.shift();
    if (!this.state.initialized) {
      this.initializeGrid(price, ts);
    } else if (this.state.running && !this.state.halted) {
      if (this.openCount() && this.floating() <= -this.cfg.stopLossUsd) {
        this.closeAll(price, "Стоп-лосс корзины", ts);
        this.state.reentryUntil = ts + 60000;
        this.addEvent("Пауза после стоп-лосса", "—", price, 0, null, "Повторный вход приостановлен на 60 секунд", ts);
      }
      if (!this.state.halted) {
        for (const side of ["long", "short"]) {
          for (let i = this.state.sides[side].positions.length - 1; i >= 0; i--) {
            if (this.positionPnl(this.state.sides[side].positions[i], price) >= this.cfg.profitUsd)
              this.closePosition(side, i, price, "Чистая прибыль позиции достигла цели", ts);
          }
        }
      }
      if (this.state.reentryUntil && ts >= this.state.reentryUntil) {
        this.state.reentryUntil = 0; this.placeGrid("long", price); this.placeGrid("short", price);
        this.state.lastGridRefreshAt = Date.now();
        this.addEvent("Пауза завершена", "LONG / SHORT", price, this.cfg.levels * 2, null, "Сетка выставлена заново", ts);
      }
      const blocked = Boolean(this.state.reentryUntil && ts < this.state.reentryUntil);
      if (this.state.running && !this.state.halted && !blocked && this.openCount() < MAX_OPEN_TOTAL &&
          this.state.sides.long.pending.length === 0 && this.state.sides.short.pending.length === 0) {
        this.placeGrid("long", price); this.placeGrid("short", price); this.state.lastGridRefreshAt = Date.now();
      }
      if (this.state.running && !this.state.halted && !blocked && this.openCount() < MAX_OPEN_TOTAL) {
        this.processEntries("long", price, ts); this.processEntries("short", price, ts);
      }
      if (this.openCount() >= MAX_OPEN_TOTAL) {
        this.state.sides.long.pending = []; this.state.sides.short.pending = [];
      }
      const now = Date.now();
      if (this.state.running && !this.state.halted && !blocked && this.openCount() < MAX_OPEN_TOTAL &&
          now - (this.state.lastGridRefreshAt || 0) >= 1000) {
        this.placeGrid("long", price); this.placeGrid("short", price);
        this.state.lastGridRefreshAt = now;
      }
      if (this.state.running && !this.state.halted &&
          this.state.realized + this.floating() <= -this.state.startBalance * this.cfg.sessionLossPct / 100) {
        this.closeAll(price, "Остановка по лимиту сессии", ts);
        this.state.halted = true; this.state.haltReason = "Достигнут лимит убытка сессии";
        this.addEvent("PAPER остановлен", "—", price, 0, this.state.realized, this.state.haltReason, ts);
        this.stopRuntime();
      }
    }
    const eq = this.equity();
    if (eq > this.state.peakEquity) this.state.peakEquity = eq;
    const dd = this.state.peakEquity > 0 ? (this.state.peakEquity - eq) / this.state.peakEquity * 100 : 0;
    if (dd > this.state.maxDrawdown) this.state.maxDrawdown = dd;
    if (Date.now() - this.state.lastSavedAt >= 1000) {
      this.state.lastSavedAt = Date.now();
      await this.persist();
    }
  }
  snapshot() {
    const s = this.state || blankState(10000);
    const cfg = this.cfg;
    const open = this.openCount();
    return {
      ok: true, running: s.running, connected: s.connected, feedError: s.feedError || "",
      price: s.lastPrice, tickCount: s.tickCount,
      equity: this.equity(), balance: s.startBalance + s.realized,
      pnl: s.realized + this.floating(), realized: s.realized, floating: this.floating(),
      openCount: open, exposure: this.exposure(),
      pendingCount: s.sides.long.pending.length + s.sides.short.pending.length,
      pendingLong: s.sides.long.pending.length, pendingShort: s.sides.short.pending.length,
      runtimeMs: s.runActiveMs + (s.running && s.runLastStartedAt ? Math.max(0, Date.now() - s.runLastStartedAt) : 0),
      maxDrawdown: s.maxDrawdown, halted: s.halted, haltReason: s.haltReason,
      config: cfg ? { startBalance: cfg.startBalance, positionUsd: cfg.positionUsd, levels: cfg.levels, stepUsd: cfg.stepUsd,
        feePercent: cfg.feePercent, slippagePercent: cfg.slippagePercent, profitUsd: cfg.profitUsd, stopLossUsd: cfg.stopLossUsd,
        sessionLossPct: cfg.sessionLossPct, exposurePct: cfg.exposurePct } : null,
      openPositions: ["long","short"].flatMap(side => s.sides[side].positions.map(p => ({ ...p, pnl: s.lastPrice ? this.positionPnl(p, s.lastPrice) : 0 }))).sort((a,b) => b.openedAt-a.openedAt),
      closedPositions: s.closedPositions.slice(0,100), pending: ["long","short"].flatMap(side => s.sides[side].pending.map(p => ({side, ...p}))),
      events: s.events.slice(0,100), lastTradeTime: s.lastTradeTime
    };
  }
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
    if (origin && origin !== ALLOWED_ORIGIN) return Response.json({ error: "Origin not allowed" }, { status: 403 });
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length < 2 || parts[0] !== "api" || !/^[a-zA-Z0-9_-]{16,80}$/.test(parts[1]))
      return Response.json({ error: "Expected /api/{sessionId}/..." }, { status: 400, headers: cors(origin) });
    const id = env.PAPER_BOT.idFromName(parts[1]);
    const stub = env.PAPER_BOT.get(id);
    const inner = "/" + parts.slice(2).join("/");
    const response = await stub.fetch("https://paper.internal" + (inner === "/" ? "/state" : inner), request);
    const headers = new Headers(response.headers);
    for (const [k,v] of Object.entries(cors(origin))) headers.set(k,v);
    headers.set("Cache-Control","no-store");
    return new Response(response.body, { status: response.status, headers });
  }
};
function cors(origin) {
  return {
    "Access-Control-Allow-Origin": origin || ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin"
  };
}
