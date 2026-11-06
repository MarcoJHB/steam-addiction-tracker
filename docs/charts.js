(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const num = v => v == null ? "–" : Math.round(v).toLocaleString("en-US");
  const hrs = v => v == null ? null : v >= 100 ? `${Math.round(v)} h` : `${v.toFixed(1)} h`;
  const monthName = ym => {
    const [y, m] = ym.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
  };
  const shiftMonth = (ym, n) => {
    const [y, m] = ym.split("-").map(Number);
    const d = new Date(Date.UTC(y, m - 1 + n, 1));
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  };

  const els = {
    meta: $("meta"), cats: $("cats"), month: $("month"), vs: $("vs"), csv: $("csv"),
    board: $("board"), empty: $("empty"), more: $("more"), tpl: $("rowTpl"),
  };
  const state = { data: null, cat: 0, month: null, vs: "month", all: false };

  // ---- the formula ----
  // Everything in a category with at least `min` average players that month,
  // sorted by average players, then peak. Returns appid -> rank (1-based) and the ordered list.
  function chart(catName, month) {
    const min = state.data.min_avg_players;
    const rows = state.data.games
      .filter(g => g.categories.includes(catName) && g.monthly[month] && g.monthly[month][0] >= min)
      .map(g => ({ g, avg: g.monthly[month][0], peak: g.monthly[month][1] || 0, src: g.monthly[month][2] || null, n: g.monthly[month][3] || null }))
      .sort((a, b) => b.avg - a.avg || b.peak - a.peak);
    const rank = new Map(rows.map((r, i) => [r.g.appid, i + 1]));
    return { rows, rank };
  }

  function compareMonth() {
    return shiftMonth(state.month, state.vs === "year" ? -12 : -1);
  }

  // ---- URL state ----
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const cats = state.data.categories.map(c => c.name);
    const c = cats.findIndex(n => slug(n) === p.get("cat"));
    if (c >= 0) state.cat = c;
    if (state.data.months.includes(p.get("m"))) state.month = p.get("m");
    if (p.get("vs") === "year") state.vs = "year";
  }
  function writeHash() {
    const p = new URLSearchParams();
    p.set("cat", slug(state.data.categories[state.cat].name));
    if (state.month !== state.data.months[0]) p.set("m", state.month);
    if (state.vs !== "month") p.set("vs", state.vs);
    history.replaceState(null, "", `#${p}`);
  }
  const slug = s => s.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  // ---- load ----
  async function load() {
    try {
      const r = await fetch(`data/charts.json?t=${Date.now()}`);
      if (!r.ok) throw new Error(r.status);
      state.data = await r.json();
    } catch {
      els.meta.textContent = "No chart data yet. The first monthly run hasn't finished.";
      return;
    }
    const d = state.data;
    state.month = d.months[0];
    readHash();

    d.categories.forEach((c, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = c.name;
      b.addEventListener("click", () => { state.cat = i; state.all = false; render(); });
      els.cats.append(b);
    });
    for (const m of d.months) {
      const o = document.createElement("option");
      o.value = m;
      o.textContent = monthName(m);
      els.month.append(o);
    }
    els.month.addEventListener("change", () => { state.month = els.month.value; render(); });
    els.vs.addEventListener("click", e => {
      const b = e.target.closest("button[data-vs]");
      if (!b) return;
      state.vs = b.dataset.vs;
      render();
    });
    els.more.addEventListener("click", () => { state.all = !state.all; render(); });
    els.csv.addEventListener("click", downloadCsv);

    document.querySelectorAll(".fTopN").forEach(n => { n.textContent = d.genre_top_n; });
    $("fMin").textContent = num(d.min_avg_players);
    const when = new Date(d.generated_at);
    els.meta.textContent = `Updated ${when.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}. ` +
      `${d.games.length} games checked.`;
    render();
  }

  // ---- render ----
  function render() {
    const d = state.data;
    const cat = d.categories[state.cat];
    const cmp = compareMonth();
    const now = chart(cat.name, state.month);
    const then = chart(cat.name, cmp);
    const hasThen = then.rows.length > 0;

    [...els.cats.children].forEach((b, i) => b.setAttribute("aria-pressed", String(i === state.cat)));
    [...els.vs.children].forEach(b => b.setAttribute("aria-pressed", String(b.dataset.vs === state.vs)));
    els.month.value = state.month;
    $("fCatTags").textContent = cat.genres.join(" or ");
    $("fVs").textContent = state.vs === "year" ? `the same month a year earlier (${monthName(cmp)})` : `the month before (${monthName(cmp)})`;
    writeHash();

    const size = d.chart_size;
    const rows = state.all ? now.rows : now.rows.slice(0, size);
    const top = rows.length ? rows[0].avg : 1;
    els.board.replaceChildren();
    els.empty.hidden = rows.length > 0;
    els.empty.textContent = `No games with player data for ${monthName(state.month)}.`;

    rows.forEach((r, i) => {
      const g = r.g;
      const rank = i + 1;
      const li = els.tpl.content.firstElementChild.cloneNode(true);
      const q = s => li.querySelector(s);
      q(".row-head").href = `https://store.steampowered.com/app/${g.appid}/`;
      q(".rank").textContent = rank;
      const art = q(".art");
      art.src = g.header_image;
      art.addEventListener("error", () => { art.style.visibility = "hidden"; }, { once: true });
      q(".name").textContent = g.name;
      const year = g.release_date ? g.release_date.slice(0, 4) : null;
      q(".sub").textContent = [g.genres.filter(x => cat.genres.includes(x)).join(", "), year && `released ${year}`].filter(Boolean).join(" · ");
      q(".bar").style.width = `${Math.max(1.5, (r.avg / top) * 100)}%`;
      q(".value").textContent = num(r.avg);
      q(".value").title = "Average players during the month";
      if (r.src) {
        const est = document.createElement("abbr");
        est.className = "est";
        est.textContent = r.src === "e" ? "est." : "sampled";
        est.title = r.src === "e"
          ? `SteamCharts doesn't track this game. Estimated from our once-a-day player count on ${r.n} days of the month.`
          : `SteamCharts doesn't track this game. Averaged from ${r.n} samples of Steam's live player count, taken every 2 hours.`;
        q(".sub").append(" · ", est);
      }
      q(".m-peak").textContent = num(r.peak);

      // movement
      const mv = q(".mv");
      const prev = then.rank.get(g.appid);
      if (!hasThen) {
        mv.textContent = "–";
        mv.classList.add("same");
        mv.title = `No data for ${monthName(cmp)}`;
      } else if (prev == null) {
        mv.textContent = "NEW";
        mv.classList.add("new");
        mv.title = `Not in the ${monthName(cmp)} chart`;
      } else if (prev === rank) {
        mv.textContent = "=";
        mv.classList.add("same");
        mv.title = `Same place as ${monthName(cmp)}`;
      } else {
        const diff = prev - rank;
        mv.textContent = `${diff > 0 ? "▲" : "▼"} ${Math.abs(diff)}`;
        mv.classList.add(diff > 0 ? "up" : "down");
        mv.title = `${diff > 0 ? "Up" : "Down"} ${Math.abs(diff)} from #${prev} in ${monthName(cmp)}`;
      }
      const pastAvg = g.monthly[cmp] ? g.monthly[cmp][0] : null;
      const ch = q(".m-change");
      if (pastAvg) {
        const pct = (r.avg / pastAvg - 1) * 100;
        ch.innerHTML = `<b class="${pct >= 0 ? "pos" : "neg"}">${pct >= 0 ? "+" : ""}${Math.round(pct)}%</b> players`;
      } else ch.remove();
      const pv = q(".m-prev");
      if (prev != null) pv.innerHTML = `was <b>#${prev}</b>`;
      else pv.remove();
      const h = q(".m-hours");
      if (g.median_hours != null) h.innerHTML = `<b>${hrs(g.median_hours)}</b> typical player`;
      else h.remove();

      li.classList.toggle("podium", rank <= 3);
      els.board.append(li);
    });

    const extra = now.rows.length - size;
    els.more.hidden = extra <= 0;
    els.more.textContent = state.all ? `Show top ${size} only` : `Show all ${now.rows.length} games`;
  }

  // ---- CSV of the chart on screen ----
  function downloadCsv() {
    const d = state.data;
    const cat = d.categories[state.cat];
    const cmp = compareMonth();
    const now = chart(cat.name, state.month);
    const then = chart(cat.name, cmp);
    const head = ["Rank", `Rank ${cmp}`, "Move", "Game", "Avg players", "Peak players", `Avg players ${cmp}`, "Change %", "Median hours", "Released", "Player data", "Steam link"];
    const lines = [head];
    const rows = state.all ? now.rows : now.rows.slice(0, d.chart_size);
    rows.forEach((r, i) => {
      const prev = then.rank.get(r.g.appid);
      const past = r.g.monthly[cmp] ? r.g.monthly[cmp][0] : null;
      lines.push([
        i + 1, prev ?? "", prev == null ? "NEW" : prev - (i + 1), r.g.name, r.avg, r.peak, past ?? "",
        past ? Math.round((r.avg / past - 1) * 1000) / 10 : "", r.g.median_hours ?? "", r.g.release_date ?? "",
        r.src === "e" ? `estimate (${r.n} daily counts)` : r.src === "s" ? `sampled (${r.n} samples)` : "SteamCharts",
        `https://store.steampowered.com/app/${r.g.appid}/`,
      ]);
    });
    const csv = lines.map(l => l.map(v => /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `most-played-${slug(cat.name)}-${state.month}-vs-${cmp}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  load();
})();
