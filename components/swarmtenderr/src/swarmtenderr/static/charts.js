/* Apache ECharts integration. All assets are local; no remote chart services. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SwarmCharts = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function periodBounds(period, mode = "daily") {
    if (!/^\d{4}-\d{2}(-\d{2})?$/.test(period)) throw new Error("Invalid period");
    const [year, month, day] = period.split("-").map(Number);
    if (month < 1 || month > 12 || (day && new Date(Date.UTC(year, month - 1, day)).toISOString().slice(0, 10) !== period)) throw new Error("Invalid calendar period");
    if (period.length === 10 && mode !== "monthly") return { from: period, to: period };
    const prefix = period.slice(0, 7);
    return { from: prefix + "-01", to: prefix + "-" + new Date(Date.UTC(year, month, 0)).getUTCDate() };
  }
  function nearestIndex(values, point) {
    if (!values.length) return -1;
    let left = 0, right = values.length - 1;
    while (left < right) {
      const middle = Math.floor((left + right) / 2);
      if (values[middle] < point) left = middle + 1;
      else right = middle;
    }
    return left > 0 && Math.abs(values[left - 1] - point) <= Math.abs(values[left] - point) ? left - 1 : left;
  }
  function zoomWindow(start, end, factor) {
    const width = Math.min(100, Math.max(2, (end - start) * factor));
    const lower = Math.max(0, Math.min(100 - width, (start + end - width) / 2));
    return [lower, lower + width];
  }
  function create(options) {
    function timestamp(seconds, full = true) {
      return new Date(seconds * 1000).toLocaleString([], {
        timeZone: options.zone(), ...(full ? { month: "short", day: "numeric", year: "numeric" } : {}),
        hour: "numeric", minute: "2-digit", second: "2-digit",
      });
    }
    const records = new Map();
    function record(target, kind) {
      if (records.has(target)) return records.get(target);
      const plot = document.createElement("div");
      plot.className = "chart-canvas";
      const readout = document.createElement("div");
      readout.className = "sr-only";
      readout.setAttribute("aria-live", "polite");
      target.replaceChildren(plot, readout);
      target.tabIndex = 0;
      target.setAttribute("role", "group");
      target.setAttribute("aria-label", (kind === "completion" ? "Completed torrents" : "Transfer rates") + ". Arrow keys inspect values; plus and minus zoom; zero resets zoom; Escape hides values." + (kind === "completion" ? " Enter opens completion history." : ""));
      const controller = new AbortController();
      const chart = echarts.init(plot, null, { renderer: "svg" });
      const value = { chart, target, readout, kind, rows: [], axis: [], index: -1, zoom: null, period: null, selected: { Download: true, Upload: true }, controller };
      records.set(target, value);
      const resize = new ResizeObserver(() => { if (target.clientWidth) chart.resize(); });
      resize.observe(target);
      value.resize = resize;
      chart.on("datazoom", () => {
        const current = chart.getOption().dataZoom[0];
        value.zoom = current.start <= 0 && current.end >= 100 ? null : {
          startValue: kind === "completion" ? value.axis[Math.round(current.startValue)] : current.startValue,
          endValue: kind === "completion" ? value.axis[Math.round(current.endValue)] : current.endValue,
        };
      });
      chart.on("click", (event) => {
        if (event.componentType !== "series") return;
        value.index = event.dataIndex;
        if (kind === "completion") options.openPeriod(value.rows[value.index].period, value.period);
      });
      chart.on("updateAxisPointer", (event) => {
        const axis = event.axesInfo?.[0];
        if (!axis || !value.rows.length) return;
        value.tipVisible = true;
        value.index = kind === "completion" ? Math.max(0, Math.min(value.rows.length - 1, axis.value)) : nearestIndex(value.axis, axis.value);
        readout.textContent = describe(value, value.index);
      });
      chart.on("globalout", () => { value.tipVisible = false; });
      target.addEventListener("keydown", (event) => {
        const last = value.rows.length - 1;
        if (last < 0) return;
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          value.index = event.key === "Home" ? 0 : event.key === "End" ? last : Math.max(0, Math.min(last, (value.index < 0 ? last : value.index) + (event.key === "ArrowLeft" ? -1 : 1)));
          value.tipVisible = true;
          readout.textContent = describe(value, value.index);
          const seriesIndex = kind === "transfer" && !value.selected.Download ? 1 : 0;
          chart.dispatchAction({ type: "showTip", seriesIndex, dataIndex: value.index });
        } else if (["+", "=", "-", "0"].includes(event.key)) {
          event.preventDefault();
          const current = chart.getOption().dataZoom[0];
          const range = event.key === "0" ? [0, 100] : zoomWindow(current.start, current.end, event.key === "-" ? 1.5 : 0.67);
          chart.dispatchAction({ type: "dataZoom", start: range[0], end: range[1] });
        } else if (event.key === "Escape") {
          value.tipVisible = false;
          chart.dispatchAction({ type: "hideTip" });
          readout.textContent = "";
        } else if (event.key === "Enter" && kind === "completion" && value.index >= 0) {
          event.preventDefault();
          options.openPeriod(value.rows[value.index].period, value.period);
        }
      }, { signal: controller.signal });
      target.addEventListener("blur", () => {
        value.tipVisible = false;
        chart.dispatchAction({ type: "hideTip" });
      }, { signal: controller.signal });
      if (kind === "transfer") {
        document.querySelectorAll("[data-chart-series]").forEach((button) => {
          button.addEventListener("click", () => {
            const name = button.dataset.chartSeries;
            value.selected[name] = !value.selected[name];
            button.setAttribute("aria-pressed", String(value.selected[name]));
            chart.dispatchAction({ type: "legendToggleSelect", name });
          }, { signal: controller.signal });
        });
      }
      return value;
    }
    function describe(value, index) {
      const row = value.rows[index];
      if (!row) return "";
      if (value.kind === "transfer") return timestamp(row.at) + " · Download " + options.bytes(row.download) + "/s · Upload " + options.bytes(row.upload) + "/s";
      return (value.period === "monthly" ? row.period.slice(0, 7) : row.period) + ": " + (row.count == null ? "No historical coverage" : row.count + " completed · " + options.bytes(row.bytes)) + (row.partial ? " · Partial coverage" : "");
    }
    function render(target, kind, rows, period) {
      if (!target.clientWidth) return;
      const value = record(target, kind);
      if (value.period !== period) value.zoom = null;
      value.period = period;
      value.rows = rows;
      value.axis = rows.map((row) => kind === "completion" ? row.period : row.at * 1000);
      const css = getComputedStyle(target);
      const color = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
      const download = color("--accent", "#8fb3ff"), upload = color("--green", "#7eddbb");
      const text = color("--muted", "#94a3b8"), line = color("--line", "#2b3747");
      const background = color("--panel", "#141b25");
      const dataZoom = [
        { id: "range", type: "slider", height: 16, bottom: 2, borderColor: line, showDetail: false, brushSelect: false, backgroundColor: background, fillerColor: "rgba(143,179,255,0.12)", handleStyle: { color: download }, ...(value.zoom || { start: 0, end: 100 }) },
        { id: "inside", type: "inside", zoomOnMouseWheel: "ctrl", moveOnMouseWheel: false, moveOnMouseMove: true },
      ];
      const series = kind === "completion" ? [{ id: "completions", name: "Completed", type: "bar", barMaxWidth: 28,
        data: rows.map((row) => ({ value: row.count, itemStyle: { color: download, opacity: row.partial ? 0.45 : 1, borderRadius: [3, 3, 0, 0] } })),
        emphasis: { itemStyle: { opacity: 1 } }, cursor: "pointer" }] : ["Download", "Upload"].map((name, index) => ({ id: name, name, type: "line", showSymbol: rows.length === 1, symbolSize: 5, connectNulls: false, sampling: "lttb", lineStyle: { width: 2 }, itemStyle: { color: index ? upload : download }, emphasis: { focus: "series" }, data: rows.map((row) => [row.at * 1000, row[index ? "upload" : "download"]]) }));
      value.chart.setOption({
        animation: false, backgroundColor: "transparent", textStyle: { fontFamily: css.fontFamily, color: text },
        grid: { top: 18, right: 14, bottom: 55, left: kind === "transfer" ? 78 : 42 },
        tooltip: { trigger: "axis", triggerOn: "mousemove|click", confine: true, renderMode: "richText", backgroundColor: background, borderColor: line, textStyle: { color: color("--text", "#e5eaf2"), fontSize: 12 }, axisPointer: { type: kind === "completion" ? "shadow" : "line" }, formatter: (items) => {
          const index = items[0]?.dataIndex;
          return index == null ? "" : describe(value, index).split(" · ").join("\n");
        } },
        legend: { show: false, selected: { ...value.selected } },
        xAxis: kind === "completion" ? { type: "category", data: value.axis, axisLabel: { color: text, formatter: (label) => (period === "monthly" ? label.slice(0, 7) : label.slice(5)) }, axisTick: { show: false }, axisLine: { lineStyle: { color: line } } } : { type: "time", axisLabel: { color: text, hideOverlap: true, formatter: (stamp) => timestamp(stamp / 1000, false) }, axisLine: { lineStyle: { color: line } }, splitLine: { show: false } },
        yAxis: { type: "value", min: 0, minInterval: kind === "completion" ? 1 : undefined, splitNumber: 3, axisLabel: { color: text, formatter: kind === "completion" ? "{value}" : (amount) => options.bytes(amount) + "/s" }, splitLine: { lineStyle: { color: line, type: "dashed" } } },
        dataZoom, series,
        graphic: rows.length ? [] : [{ type: "text", left: "center", top: "middle", style: { text: "Waiting for observations", fill: text } }],
      }, { notMerge: true });
      value.chart.resize();
      if (value.index >= 0 && value.index < rows.length && value.tipVisible) {
        value.chart.dispatchAction({ type: "showTip", seriesIndex: kind === "transfer" && !value.selected.Download ? 1 : 0, dataIndex: value.index });
      }
    }
    return {
      completion: (target, rows, period) => render(target, "completion", rows, period),
      transfer: (target, rows) => render(target, "transfer", rows, "transfer"),
      dispose: () => {
        for (const value of records.values()) {
          value.controller.abort(); value.resize.disconnect(); value.chart.dispose(); value.target.replaceChildren();
        }
        records.clear();
      },
    };
  }
  return { create, periodBounds, nearestIndex, zoomWindow };
});
