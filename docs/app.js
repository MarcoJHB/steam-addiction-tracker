(() => {
  "use strict";

  // ---- formatting ----
  const hrs = v => v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k h` : `${v >= 100 ? Math.round(v) : v.toFixed(1)} h`;
  const pct = v => v == null ? "–" : `${Math.round(v)}%`;
  const num = v => v == null ? "–" : v.toLocaleString("en-US");
  const score = v => v == null ? "–" : v.toFixed(0);
  const times = v => v == null ? "–" : `${v.toFixed(v >= 10 ? 0 : 1)}×`;
  const monthYear = s => s ? new Date(s).toLocaleDateString("en-GB", { month: "short", year: "numeric" }) : "–";
  const fullDate = s => s ? new Date(s).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }) : "–";

  // get: the number used for sorting/bars. fmt: how the number on the right is shown.
  const METRICS = {
    score:               { fmt: score },
    age_x:               { fmt: times, tickAt: 1, tickTitle: "1.0× = typical for a game its age" },
    median:              { fmt: hrs, tick: "avg" },
    avg:                 { fmt: hrs, tick: "median" },
    max:                 { fmt: hrs },
    p90:                 { fmt: hrs, tick: "median" },
    median_after_review: { fmt: hrs },
    active_pct:          { fmt: pct, fixedMax: 100 },
    heavy_pct:           { fmt: pct, fixedMax: 100 },
    avg_last_2w:         { fmt: hrs },
    players_now:         { fmt: num },
    release:             { fmt: (v, g) => monthYear(g.release_date), get: g => g.release_date ? Date.parse(g.release_date) : null, noBar: true, dirLabels: ["Newest first", "Oldest first"] },
  };
  const valueOf = (g, m) => METRICS[m].get ? METRICS[m].get(g) : g[m];

  const $ = id => document.getElementById(id);
  const els = {
    board: $("board"), empty: $("empty"), meta: $("meta"), metric: $("metric"), dir: $("dir"),
    search: $("search"), tags: $("tags"), csv: $("csv"), tpl: $("rowTpl"), show: $("show"),
    favCount: $("favCount"), hiddenCount: $("hiddenCount"),
  };

  const state = { data: null, history: null, metric: "score", asc: false, q: "", off: new Set(), show: "all", open: new Set() };

  // ---- favourites & hidden games, saved in this browser ----
  const store = {
    load(key) { try { return new Set(JSON.parse(localStorage.getItem(key) || "[]").map(String)); } catch { return new Set(); } },
    save(key, set) { try { localStorage.setItem(key, JSON.stringify([...set])); } catch { /* private mode etc. */ } },
  };
  const favs = store.load("sat:favourites");
  const hidden = store.load("sat:hidden");

  // ---- URL state, so a filtered view can be shared ----
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    if (METRICS[p.get("by")]) state.metric = p.get("by");
    state.asc = p.get("dir") === "asc";
    state.q = p.get("q") || "";
    state.off = new Set((p.get("off") || "").split(",").filter(Boolean));
    if (["fav", "hidden"].includes(p.get("show"))) state.show = p.get("show");
  }
  function writeHash() {
    const p = new URLSearchParams();
    if (state.metric !== "score") p.set("by", state.metric);
    if (state.asc) p.set("dir", "asc");
    if (state.q) p.set("q", state.q);
    if (state.off.size) p.set("off", [...state.off].join(","));
    if (state.show !== "all") p.set("show", state.show);
    const h = p.toString();
    history.replaceState(null, "", h ? `#${h}` : location.pathname);
  }

  // ---- data ----
  async function load() {
    readHash();
    try {
      const r = await fetch(`data/latest.json?t=${Date.now()}`);
      if (!r.ok) throw new Error(r.status);
      state.data = await r.json();
    } catch (e) {
      els.meta.textContent = "No data yet. The first daily run hasn't finished. Check back in about half an hour.";
      return;
    }
    const d = state.data;
    $("reviewCount").textContent = Number(d.reviews_per_game).toLocaleString("en-US");
    $("heavyHours").textContent = d.heavy_player_hours;
    if (d.age_peers) $("agePeers").textContent = d.age_peers;
    const when = new Date(d.generated_at);
    els.meta.textContent = `${d.games.length} games, updated ${when.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })} at ${when.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}.`;

    for (const t of d.tags) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tag";
      b.textContent = t;
      b.setAttribute("aria-pressed", String(!state.off.has(t)));
      b.addEventListener("click", () => {
        state.off.has(t) ? state.off.delete(t) : state.off.add(t);
        b.setAttribute("aria-pressed", String(!state.off.has(t)));
        render();
      });
      els.tags.append(b);
    }
    els.metric.value = state.metric;
    els.search.value = state.q;
    render();
  }

  async function loadHistory() {
    if (state.history) return state.history;
    try {
      const r = await fetch(`data/history.json?t=${Date.now()}`);
      state.history = r.ok ? await r.json() : {};
    } catch { state.history = {}; }
    return state.history;
  }

  function visibleGames() {
    const q = state.q.trim().toLowerCase();
    const m = state.metric;
    const dir = state.asc ? 1 : -1;
    return state.data.games
      .filter(g => {
        const id = String(g.appid);
        if (state.show === "hidden") return hidden.has(id);
        if (state.show === "fav") return favs.has(id);
        return !hidden.has(id);
      })
      .filter(g => g.tags.some(t => !state.off.has(t) || t === "Extra"))
      .filter(g => !q || g.name.toLowerCase().includes(q))
      .slice()
      .sort((a, b) => {
        const va = valueOf(a, m), vb = valueOf(b, m);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;            // missing values always go last
        if (vb == null) return -1;
        return (va - vb) * dir;
      });
  }

  // ---- render ----
  function render() {
    writeHash();
    const m = state.metric, def = METRICS[m];
    const labels = def.dirLabels || ["Highest first", "Lowest first"];
    els.dir.textContent = labels[state.asc ? 1 : 0];
    for (const b of els.show.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.show === state.show));
    els.favCount.textContent = favs.size ? `(${favs.size})` : "";
    els.hiddenCount.textContent = hidden.size ? `(${hidden.size})` : "";

    const games = visibleGames();
    const top = def.fixedMax || Math.max(1, ...games.map(g => valueOf(g, m) ?? 0));
    els.empty.hidden = games.length > 0;
    if (!games.length) {
      els.empty.textContent =
        state.show === "fav" && !favs.size ? "No favourites yet. Click the star next to a game to add it here." :
        state.show === "hidden" && !hidden.size ? "No hidden games. Click the eye next to a game to hide it from the main list." :
        "No games match. Clear the search or turn a tag back on.";
    }
    els.board.replaceChildren(...games.map((g, i) => row(g, i, m, def, top)));
  }

  function row(g, i, m, def, top) {
    const id = String(g.appid);
    const li = els.tpl.content.firstElementChild.cloneNode(true);
    const head = li.querySelector(".row-head");
    li.querySelector(".rank").textContent = i + 1;
    const img = li.querySelector(".art");
    if (g.header_image) img.src = g.header_image; else img.style.visibility = "hidden";
    li.querySelector(".name").textContent = g.name;

    const relYear = g.release_date ? g.release_date.slice(0, 4) : null;
    const eaYear = earlyAccessYear(g);
    const sub = [g.tags.join(", "),
      relYear ? `released ${relYear}` : null,
      eaYear ? `Early Access from ${eaYear}` : null,
      g.stale ? "yesterday's data" : null].filter(Boolean).join(", ");
    li.querySelector(".sub").textContent = sub;
    if (g.stale) li.querySelector(".sub").classList.add("stale");

    const v = valueOf(g, m);
    const barWrap = li.querySelector(".bar-wrap");
    if (def.noBar) barWrap.style.visibility = "hidden";
    else li.querySelector(".bar").style.width = `${Math.min(100, ((v ?? 0) / top) * 100)}%`;
    const tick = li.querySelector(".tick");
    const tickVal = def.tickAt ?? (def.tick ? g[def.tick] : null);
    if (!def.noBar && tickVal != null && tickVal <= top) {
      tick.style.left = `calc(${(tickVal / top) * 100}% - 1px)`;
      tick.title = def.tickTitle || `${def.tick === "avg" ? "Average" : "Median"}: ${hrs(tickVal)}`;
    } else tick.hidden = true;
    li.querySelector(".value").textContent = def.fmt(v, g);
    li.querySelector(".m-avg").textContent = hrs(g.avg);
    li.querySelector(".m-max").textContent = hrs(g.max);
    li.querySelector(".m-active").textContent = pct(g.active_pct);

    // Favourite & hide buttons
    const favBtn = li.querySelector(".fav"), hideBtn = li.querySelector(".hide");
    const sync = () => {
      favBtn.setAttribute("aria-pressed", String(favs.has(id)));
      favBtn.setAttribute("aria-label", favs.has(id) ? `Remove ${g.name} from favourites` : `Add ${g.name} to favourites`);
      favBtn.title = favs.has(id) ? "Remove from favourites" : "Add to favourites";
      hideBtn.setAttribute("aria-pressed", String(hidden.has(id)));
      hideBtn.setAttribute("aria-label", hidden.has(id) ? `Show ${g.name} again` : `Hide ${g.name}`);
      hideBtn.title = hidden.has(id) ? "Show again" : "Hide this game";
      li.classList.toggle("is-hidden", hidden.has(id));
    };
    sync();
    favBtn.addEventListener("click", () => {
      favs.has(id) ? favs.delete(id) : favs.add(id);
      store.save("sat:favourites", favs);
      state.show === "fav" ? render() : (sync(), updateCounts());
    });
    hideBtn.addEventListener("click", () => {
      hidden.has(id) ? hidden.delete(id) : hidden.add(id);
      store.save("sat:hidden", hidden);
      state.show === "fav" ? (sync(), updateCounts()) : render();
    });

    const detail = li.querySelector(".detail");
    const toggle = async (open) => {
      head.setAttribute("aria-expanded", String(open));
      detail.hidden = !open;
      if (open) { state.open.add(g.appid); await fillDetail(detail, g); }
      else state.open.delete(g.appid);
    };
    head.addEventListener("click", () => toggle(detail.hidden));
    if (state.open.has(g.appid)) toggle(true);
    return li;
  }

  function updateCounts() {
    els.favCount.textContent = favs.size ? `(${favs.size})` : "";
    els.hiddenCount.textContent = hidden.size ? `(${hidden.size})` : "";
  }

  // Year a game became playable in Early Access, if well before its official release.
  function earlyAccessYear(g) {
    if (!g.playable_since || !g.release_date) return null;
    const gap = (Date.parse(g.release_date) - Date.parse(g.playable_since)) / 86400000;
    return gap > 60 ? g.playable_since.slice(0, 4) : null;
  }

  async function fillDetail(el, g) {
    const sinceDays = g.sample_span_days;
    const pace = sinceDays != null
      ? sinceDays < 1 ? "in under a day" : `over ${Math.round(sinceDays)} day${Math.round(sinceDays) === 1 ? "" : "s"}`
      : "";
    const peers = g.age_peer_years
      ? g.age_peer_years[0] === g.age_peer_years[1] ? `from ${g.age_peer_years[0]}` : `from ${g.age_peer_years[0]}–${g.age_peer_years[1]}`
      : "";
    const stats = [
      ["Median hours", hrs(g.median)], ["Average hours", hrs(g.avg)],
      ["Top 10% play at least", hrs(g.p90)], ["Most by one player", hrs(g.max)],
      ["Median at review time", hrs(g.median_at_review)], ["Median played after reviewing", hrs(g.median_after_review)],
      ["Still playing", pct(g.active_pct)], ["Heavy players", pct(g.heavy_pct)],
      ["Addiction score", score(g.score)],
      ["Vs games its age", g.age_x == null ? "–" : `${times(g.age_x)} <small>(similar games ${peers} score ${score(g.age_peer_score)})</small>`],
      ["Recommended", pct(g.recommended_pct)], ["Playing right now", num(g.players_now)],
      ["Steam rating", g.review_score_desc || "–"], ["Total reviews", num(g.total_reviews)],
      ["Released", fullDate(g.release_date)],
      ...(earlyAccessYear(g) ? [["Playable since (Early Access)", fullDate(g.playable_since)]] : []),
    ];
    el.innerHTML = `
      <div class="numbers">
        <h3>The numbers</h3>
        <dl class="stats">${stats.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
      </div>
      <div class="chart">
        <h3>How long reviewers have played</h3>
        ${distribution(g.distribution)}
        <p class="legend">Each bar starts at the hours shown below it. Faded bars are heavy players. ${g.sample} most recent reviews, written ${pace}.</p>
      </div>
      <div class="chart trend">
        <h3>Over time</h3>
        <p class="legend">Loading…</p>
      </div>
      <p class="links">
        <a href="https://store.steampowered.com/app/${g.appid}/" target="_blank" rel="noopener">Steam store page</a>
        <a href="https://steamdb.info/app/${g.appid}/charts/" target="_blank" rel="noopener">Player charts on SteamDB</a>
      </p>`;
    const h = (await loadHistory())[g.appid] || [];
    el.querySelector(".trend").innerHTML = `<h3>Over time</h3>${trend(h)}`;
  }

  function distribution(buckets) {
    if (!buckets || !buckets.length) return "";
    const W = 360, H = 150, padB = 22, gap = 4;
    const max = Math.max(1, ...buckets.map(b => b.count));
    const bw = (W - gap * (buckets.length - 1)) / buckets.length;
    const heavyAt = state.data.heavy_player_hours || 100;
    const bars = buckets.map((b, i) => {
      const h = (b.count / max) * (H - padB - 14);
      const x = i * (bw + gap), y = H - padB - h;
      const lo = parseInt(b.label, 10);
      const label = lo >= 1000 ? `${lo / 1000}k+` : String(lo);
      return `<rect class="${lo >= heavyAt ? "heavy" : "bar-d"}" x="${x}" y="${y}" width="${bw}" height="${Math.max(h, b.count ? 1.5 : 0)}" rx="2"><title>${b.label} hours: ${b.count} reviewers</title></rect>
        <text class="axis" x="${x + bw / 2}" y="${y - 4}" text-anchor="middle">${b.count || ""}</text>
        <text class="axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${label}</text>`;
    }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Number of reviewers in each playtime band, in hours">${bars}</svg>`;
  }

  function trend(points) {
    if (points.length < 2) {
      return `<p class="legend">The trend line shows up once there are a couple of days of data. This game has ${points.length} so far.</p>`;
    }
    const W = 360, H = 150, padL = 34, padB = 22, padT = 8;
    const ys = points.flatMap(p => [p.median, p.avg]).filter(v => v != null);
    const maxY = Math.max(1, ...ys) * 1.08;
    const x = i => padL + (i / (points.length - 1)) * (W - padL - 4);
    const y = v => padT + (1 - v / maxY) * (H - padB - padT);
    const path = key => points.map((p, i) => p[key] == null ? "" : `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
    const gridVals = [0, maxY / 2, maxY].map(v => Math.round(v));
    const grid = gridVals.map(v => `<line class="grid" x1="${padL}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${padL - 6}" y="${y(v) + 4}" text-anchor="end">${v}h</text>`).join("");
    const fmtD = d => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Median and average hours over time">${grid}
      <path class="line2" d="${path("avg")}"/><path class="line" d="${path("median")}"/>
      <text class="axis" x="${padL}" y="${H - 4}">${fmtD(points[0].d)}</text>
      <text class="axis" x="${W}" y="${H - 4}" text-anchor="end">${fmtD(points[points.length - 1].d)}</text></svg>
      <p class="legend"><span><i style="background:var(--amber)"></i>Median</span><span><i style="background:var(--teal)"></i>Average</span></p>`;
  }

  // ---- CSV ----
  function downloadCsv() {
    const cols = ["rank", "name", "appid", "tags", "favourite", "score", "age_x", "median", "avg", "p90", "max", "median_at_review",
      "median_after_review", "avg_last_2w", "active_pct", "heavy_pct", "recommended_pct", "players_now",
      "total_reviews", "review_score_desc", "release_date", "playable_since", "sample"];
    const esc = v => v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
    const lines = [cols.join(",")].concat(visibleGames().map((g, i) =>
      cols.map(c => esc(
        c === "rank" ? i + 1 :
        c === "tags" ? g.tags.join("; ") :
        c === "favourite" ? (favs.has(String(g.appid)) ? "yes" : "") :
        g[c])).join(",")));
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `steam-addiction-${state.data.generated_at.slice(0, 10)}-by-${state.metric}${state.show !== "all" ? "-" + state.show : ""}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---- events ----
  els.metric.addEventListener("change", () => {
    state.metric = els.metric.value;
    state.asc = false;
    render();
  });
  els.dir.addEventListener("click", () => { state.asc = !state.asc; render(); });
  els.show.addEventListener("click", e => {
    const b = e.target.closest("button[data-show]");
    if (!b) return;
    state.show = b.dataset.show;
    render();
  });
  let t;
  els.search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = els.search.value; render(); }, 120); });
  els.csv.addEventListener("click", () => state.data && downloadCsv());

  load();
})();
