// Bar chart "revenue and cost per month" on the dashboard (issue #207). The data comes from the
// data-chart attribute of the canvas (JSON written by the server); Chart.js is delivered with the
// app. Colours come from the theme variables and are drawn again when the theme changes.
(() => {
  const canvas = document.querySelector("canvas[data-chart]");
  if (!canvas || typeof Chart === "undefined") return;
  let data;
  try {
    data = JSON.parse(canvas.dataset.chart);
  } catch (error) {
    return;
  }
  const root = document.documentElement;
  const color = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  const euro = (value) =>
    Number(value).toLocaleString(data.language, { style: "currency", currency: "EUR" });
  const monthName = (text) => {
    const [year, month] = text.split("-").map(Number);
    return new Date(year, month - 1, 1).toLocaleDateString(data.language, {
      month: "short",
      year: "2-digit",
    });
  };
  const datasets = () => [
    {
      label: canvas.dataset.revenue,
      data: data.months.map((m) => Number(m.revenue)),
      backgroundColor: color("--mh-accent"),
    },
    {
      label: canvas.dataset.cost,
      data: data.months.map((m) => Number(m.cost)),
      backgroundColor: color("--mh-text-muted"),
    },
  ];
  const chart = new Chart(canvas, {
    type: "bar",
    data: { labels: data.months.map((m) => monthName(m.month)), datasets: datasets() },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { ticks: { color: color("--mh-text-muted") }, grid: { color: color("--mh-border") } },
        y: {
          beginAtZero: true,
          ticks: { color: color("--mh-text-muted"), callback: (value) => euro(value) },
          grid: { color: color("--mh-border") },
        },
      },
      plugins: {
        legend: { labels: { color: color("--mh-text") } },
        tooltip: { callbacks: { label: (item) => `${item.dataset.label}: ${euro(item.parsed.y)}` } },
      },
    },
  });
  const recolor = () => {
    const sets = datasets();
    chart.data.datasets.forEach((set, index) => Object.assign(set, sets[index]));
    chart.options.scales.x.ticks.color = color("--mh-text-muted");
    chart.options.scales.x.grid.color = color("--mh-border");
    chart.options.scales.y.ticks.color = color("--mh-text-muted");
    chart.options.scales.y.grid.color = color("--mh-border");
    chart.options.plugins.legend.labels.color = color("--mh-text");
    chart.update();
  };
  new MutationObserver(recolor).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
})();
