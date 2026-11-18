// Line chart "cost per piece over time" on the analysis page (issue #204). The data comes from
// the data-chart attribute of the canvas (JSON written by the server); Chart.js is delivered with
// the app. The colours are read from the theme variables and drawn again when the theme changes.
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
  const day = (iso) => {
    const [year, month, date] = iso.split("-").map(Number);
    return new Date(year, month - 1, date).toLocaleDateString(data.language);
  };
  const euro = (value) =>
    Number(value).toLocaleString(data.language, {
      style: "currency",
      currency: "EUR",
    });
  const costs = data.points.map((point) => Number(point.cost));

  // Many points would melt into one thick line: smaller markers, none at all from 200 points.
  const markerSize = costs.length > 200 ? 0 : costs.length > 60 ? 2 : 4;

  const datasets = () => {
    const sets = [
      {
        label: data.labels.actual,
        data: costs,
        borderColor: color("--mh-accent"),
        backgroundColor: color("--mh-accent"),
        tension: 0,
        pointRadius: markerSize,
        pointHoverRadius: 5,
      },
    ];
    if (data.planned !== null) {
      sets.push({
        label: data.labels.planned,
        data: costs.map(() => Number(data.planned)),
        borderColor: color("--mh-text-muted"),
        backgroundColor: color("--mh-text-muted"),
        borderDash: [6, 4],
        pointRadius: 0,
      });
    }
    return sets;
  };

  const chart = new Chart(canvas, {
    type: "line",
    data: { labels: data.points.map((point) => day(point.date)), datasets: datasets() },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      // The values show up as soon as the mouse is near a day, not only exactly on a point
      interaction: { mode: "index", intersect: false },
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
        tooltip: {
          callbacks: {
            label: (item) => `${item.dataset.label}: ${euro(item.parsed.y)}`,
            footer: (items) =>
              `${data.labels.pieces}: ${data.points[items[0].dataIndex].quantity}`,
          },
        },
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
