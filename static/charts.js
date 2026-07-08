// charts.js — Bộ vẽ biểu đồ SVG thuần (không phụ thuộc), theo CSS var để hợp theme sáng/tối

const CX_PALETTE = ['var(--blue)', 'var(--magenta)', 'var(--green)', 'var(--orange)', 'var(--cyan)', 'var(--yellow)'];
const CX_CARRIER = { vt: 'var(--blue)', mb: 'var(--orange)', vn: 'var(--green)' };
const CX_ALERT = { drops: 'var(--orange)', bads: 'var(--red)' };

function cxEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function cxTrunc(s, n) {
  s = String(s ?? '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
function cxNum(v) {
  return (typeof v === 'number' && !Number.isInteger(v)) ? v.toFixed(1) : v;
}

function chartLegend(items) {
  return `<div class="cx-legend">${items.map(i =>
    `<span><i style="background:${i.color}"></i>${cxEsc(i.label)}</span>`).join('')}</div>`;
}

// Cột xếp chồng dọc. rows[i] = [v0, v1, ...] khớp series; labels[i] = nhãn trục X
function svgStackedBars({ labels, series, rows, height = 170 }) {
  const n = labels.length || 1;
  const W = 540, H = height, padL = 30, padR = 8, padT = 10, padB = 24;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const totals = rows.map(r => r.reduce((s, v) => s + v, 0));
  const maxV = Math.max(1, ...totals);
  const step = plotW / n, bw = Math.min(34, step * 0.62);
  const every = Math.ceil(n / 12);
  let g = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" class="cx-grid"/>` +
          `<line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" class="cx-grid"/>` +
          `<text x="${padL - 4}" y="${padT + 8}" text-anchor="end" class="cx-axis">${maxV}</text>`;
  rows.forEach((r, i) => {
    const x = padL + step * i + (step - bw) / 2;
    let y = padT + plotH;
    r.forEach((v, si) => {
      if (v > 0) {
        const h = (v / maxV) * plotH; y -= h;
        g += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" style="fill:${series[si].color}"><title>${cxEsc(labels[i])} · ${cxEsc(series[si].name)}: ${v}</title></rect>`;
      }
    });
    if (i % every === 0)
      g += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="cx-axis">${cxEsc(labels[i])}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="cx-svg" preserveAspectRatio="xMidYMid meet">${g}</svg>`;
}

// Thanh ngang (top-N). items = [{label, value}]
function svgHBars({ items, color = 'var(--blue)', width = 540, barH = 18, gap = 6 }) {
  if (!items.length) return '<div class="muted small">Không có dữ liệu.</div>';
  const max = Math.max(1, ...items.map(i => i.value));
  const padL = 130, padR = 50;
  const plotW = width - padL - padR;
  const H = items.length * (barH + gap) + gap;
  let g = '';
  items.forEach((it, i) => {
    const y = gap + i * (barH + gap);
    const w = (it.value / max) * plotW;
    g += `<text x="${padL - 6}" y="${(y + barH * 0.72).toFixed(1)}" text-anchor="end" class="cx-lbl">${cxEsc(cxTrunc(it.label, 18))}</text>`;
    g += `<rect x="${padL}" y="${y}" width="${Math.max(1, w).toFixed(1)}" height="${barH}" rx="2" style="fill:${color}"><title>${cxEsc(it.label)}: ${cxNum(it.value)}</title></rect>`;
    g += `<text x="${(padL + w + 4).toFixed(1)}" y="${(y + barH * 0.72).toFixed(1)}" class="cx-val">${cxNum(it.value)}</text>`;
  });
  return `<svg viewBox="0 0 ${width} ${H}" class="cx-svg">${g}</svg>`;
}

// Donut. segments = [{label, value, color}]
function svgDonut({ segments, size = 160, thickness = 24 }) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  if (total <= 0) return '<div class="muted small">Không có dữ liệu.</div>';
  const r = size / 2 - thickness / 2 - 2, cx = size / 2, cy = size / 2;
  let a0 = -Math.PI / 2, paths = '';
  segments.forEach(s => {
    const frac = s.value / total;
    if (frac <= 0) return;
    if (frac >= 0.999) {
      paths += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" style="stroke:${s.color}" stroke-width="${thickness}"><title>${cxEsc(s.label)}: ${cxNum(s.value)} (100%)</title></circle>`;
      return;
    }
    const a1 = a0 + frac * 2 * Math.PI, large = frac > 0.5 ? 1 : 0;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    paths += `<path d="M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" style="stroke:${s.color}" stroke-width="${thickness}" stroke-linecap="butt"><title>${cxEsc(s.label)}: ${cxNum(s.value)} (${(frac * 100).toFixed(0)}%)</title></path>`;
    a0 = a1;
  });
  return `<svg viewBox="0 0 ${size} ${size}" class="cx-svg" style="max-width:${size}px;margin:0 auto;display:block">${paths}<text x="${cx}" y="${cy + 4}" text-anchor="middle" class="cx-donut-total">${cxNum(total)}</text></svg>`;
}

// Sparkline nhỏ (1 chuỗi), không trục. null = ngắt đoạn. Baseline 0 để thấy mức tuyệt đối.
function svgSparkline({ values, color = 'var(--blue)', width = 150, height = 40 }) {
  const padX = 3, padY = 4, n = values.length || 1;
  const vals = values.filter(v => v != null);
  const maxV = Math.max(1, ...vals), minV = Math.min(0, ...vals);
  const xAt = i => padX + (n === 1 ? (width - 2 * padX) / 2 : (width - 2 * padX) * i / (n - 1));
  const yAt = v => height - padY - ((v - minV) / (maxV - minV || 1)) * (height - 2 * padY);
  const pts = values.map((v, i) => v == null ? null : `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).filter(Boolean).join(' ');
  let g = '';
  if (pts) g += `<polyline points="${pts}" fill="none" style="stroke:${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>`;
  for (let i = n - 1; i >= 0; i--) {
    if (values[i] != null) { g += `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(values[i]).toFixed(1)}" r="2.2" style="fill:${color}"/>`; break; }
  }
  return `<svg viewBox="0 0 ${width} ${height}" class="cx-svg cx-spark" preserveAspectRatio="none">${g}</svg>`;
}

// Combo mini: cột (mờ) + đường nối đỉnh + chấm điểm cuối. 1 chuỗi, baseline 0.
function svgMiniCombo({ values, color = 'var(--blue)', width = 150, height = 44 }) {
  const n = values.length || 1, padX = 3, padTop = 5, padBot = 3;
  const maxV = Math.max(1, ...values.filter(v => v != null));
  const plotH = height - padTop - padBot;
  const slot = (width - 2 * padX) / n, bw = Math.max(1.5, slot * 0.6);
  const xC = i => padX + slot * i + slot / 2;
  const yAt = v => padTop + plotH - (v / maxV) * plotH;
  let bars = '', pts = [], lastIdx = -1;
  values.forEach((v, i) => {
    if (v == null) return;
    lastIdx = i;
    const y = yAt(v);
    bars += `<rect x="${(xC(i) - bw / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(padTop + plotH - y).toFixed(1)}" rx="0.8" style="fill:${color}" fill-opacity="0.35"/>`;
    pts.push(`${xC(i).toFixed(1)},${y.toFixed(1)}`);
  });
  let g = bars;
  if (pts.length) g += `<polyline points="${pts.join(' ')}" fill="none" style="stroke:${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>`;
  if (lastIdx >= 0) g += `<circle cx="${xC(lastIdx).toFixed(1)}" cy="${yAt(values[lastIdx]).toFixed(1)}" r="2.2" style="fill:${color}"/>`;
  return `<svg viewBox="0 0 ${width} ${height}" class="cx-svg cx-spark" preserveAspectRatio="none">${g}</svg>`;
}

// Combo nhiều chuỗi: cột nhóm (mờ) + đường nối đỉnh mỗi chuỗi. series = [{name, color, values:[...|null]}]
function svgComboBars({ labels, series, width = 540, height = 190 }) {
  const n = labels.length || 1, S = series.length || 1;
  const padL = 32, padR = 10, padT = 12, padB = 24;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const maxV = Math.max(1, ...series.flatMap(s => s.values).filter(v => v != null));
  const slot = plotW / n, groupW = slot * 0.7, bw = Math.max(1.5, groupW / S);
  const yAt = v => padT + plotH - (v / maxV) * plotH;
  const xC = (i, si) => padL + slot * i + (slot - groupW) / 2 + bw * (si + 0.5);
  let g = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" class="cx-grid"/>` +
          `<line x1="${padL}" y1="${padT + plotH}" x2="${width - padR}" y2="${padT + plotH}" class="cx-grid"/>` +
          `<text x="${padL - 4}" y="${padT + 8}" text-anchor="end" class="cx-axis">${Math.round(maxV)}</text>`;
  series.forEach((s, si) => s.values.forEach((v, i) => {
    if (v == null || v <= 0) return;
    const y = yAt(v);
    g += `<rect x="${(xC(i, si) - bw / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(padT + plotH - y).toFixed(1)}" style="fill:${s.color}" fill-opacity="0.35"><title>${cxEsc(labels[i])} · ${cxEsc(s.name)}: ${cxNum(v)}</title></rect>`;
  }));
  series.forEach((s, si) => {
    const pts = s.values.map((v, i) => v == null ? null : `${xC(i, si).toFixed(1)},${yAt(v).toFixed(1)}`).filter(Boolean).join(' ');
    if (pts) g += `<polyline points="${pts}" fill="none" style="stroke:${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
    s.values.forEach((v, i) => { if (v != null) g += `<circle cx="${xC(i, si).toFixed(1)}" cy="${yAt(v).toFixed(1)}" r="2.2" style="fill:${s.color}"><title>${cxEsc(labels[i])} · ${cxEsc(s.name)}: ${cxNum(v)}</title></circle>`; });
  });
  const every = Math.ceil(n / 8);
  labels.forEach((l, i) => { if (i % every === 0) g += `<text x="${(padL + slot * i + slot / 2).toFixed(1)}" y="${height - 6}" text-anchor="middle" class="cx-axis">${cxEsc(l)}</text>`; });
  return `<svg viewBox="0 0 ${width} ${height}" class="cx-svg">${g}</svg>`;
}

// Đường xu hướng. series = [{name, values:[...|null], color}]; labels khớp values
function svgLine({ labels, series, width = 540, height = 180 }) {
  const n = labels.length || 1;
  const padL = 34, padR = 10, padT = 10, padB = 22;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const all = series.flatMap(s => s.values).filter(v => v != null);
  const maxV = Math.max(1, ...all);
  const xAt = i => padL + (n === 1 ? plotW / 2 : plotW * i / (n - 1));
  const yAt = v => padT + plotH - (v / maxV) * plotH;
  let g = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" class="cx-grid"/>` +
          `<line x1="${padL}" y1="${padT + plotH}" x2="${width - padR}" y2="${padT + plotH}" class="cx-grid"/>` +
          `<text x="${padL - 4}" y="${padT + 8}" text-anchor="end" class="cx-axis">${Math.round(maxV)}</text>`;
  series.forEach(s => {
    const pts = s.values.map((v, i) => v == null ? null : `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).filter(Boolean).join(' ');
    if (pts) g += `<polyline points="${pts}" fill="none" style="stroke:${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
    s.values.forEach((v, i) => {
      if (v != null) g += `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(v).toFixed(1)}" r="2.5" style="fill:${s.color}"><title>${cxEsc(labels[i])} · ${cxEsc(s.name)}: ${cxNum(v)}</title></circle>`;
    });
  });
  const every = Math.ceil(n / 8);
  labels.forEach((l, i) => {
    if (i % every === 0) g += `<text x="${xAt(i).toFixed(1)}" y="${height - 6}" text-anchor="middle" class="cx-axis">${cxEsc(l)}</text>`;
  });
  return `<svg viewBox="0 0 ${width} ${height}" class="cx-svg">${g}</svg>`;
}
