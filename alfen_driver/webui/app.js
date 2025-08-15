const $ = id => document.getElementById(id);

function setTextIfExists(id, text) {
  const el = $(id);
  if (el) {
    el.textContent = text;
  }
}

const statusNames = {
  0: 'Disconnected',
  1: 'Connected',
  2: 'Charging',
  3: 'Charged',
  4: 'Wait sun',
  6: 'Wait start',
  7: 'Low SOC',
};

let currentConfig = null;
let currentSchema = null;

// History series
const chartHistory = {
  points: [], // {t, current, allowed, station}
  windowSec: 300,
  maxBufferSec: 21600, // keep up to 6h for smooth window changes
  hoverT: null
};

function addHistoryPoint(s) {
  const t = Date.now() / 1000;
  const current = Number(s.ac_current || 0);
  let allowed = Number(s.set_current || 0);
  const station = Number(s.station_max_current || 0);
  const mode = Number(s.mode || 0);
  if (mode === 1) {
    // AUTO
    allowed = Number(s.applied_current ?? allowed);
  } else if (mode === 2) {
    // SCHEDULED
    allowed = Number(s.applied_current ?? allowed);
  }
  chartHistory.points.push({ t, current, allowed, station });
  const cutoff = t - chartHistory.maxBufferSec;
  chartHistory.points = chartHistory.points.filter(p => p.t >= cutoff);
  drawChart();
}

function drawChart() {
  const canvas = $('chart');
  if (!canvas) {
    return;
  }
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.width / dpr;
  const H = canvas.height / dpr;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1a2332';
  ctx.fillRect(0, 0, W, H);
  if (chartHistory.points.length < 2) {
    return;
  }
  const tEnd = chartHistory.points[chartHistory.points.length - 1].t;
  const tMinDesired = tEnd - chartHistory.windowSec;
  const visible = chartHistory.points.filter(p => p.t >= tMinDesired);
  if (visible.length < 2) {
    return;
  }
  const tMin = visible[0].t;
  const tMax = visible[visible.length - 1].t;
  const tSpan = Math.max(1, tMax - tMin);
  let vMax = 0;
  visible.forEach(p => {
    vMax = Math.max(vMax, p.current, p.allowed, p.station);
  });
  vMax = Math.max(10, Math.ceil(vMax / 5) * 5);
  function mapX(t) {
    return 40 + ((t - tMin) / tSpan) * (W - 60);
  }
  function mapY(v) {
    return H - 20 - (v / vMax) * (H - 40);
  }
  // Grid (horizontal)
  ctx.strokeStyle = 'rgba(255,255,255,0.1)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {
    const y = mapY((vMax / 5) * i);
    ctx.beginPath();
    ctx.moveTo(40, y);
    ctx.lineTo(W - 20, y);
    ctx.stroke();
  }
  // Series draw function
  function plot(color, key) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    visible.forEach((p, idx) => {
      const x = mapX(p.t);
      const y = mapY(p[key]);
      if (idx === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  }
  plot('#22c55e', 'current');
  plot('#f59e0b', 'allowed');
  plot('#ef4444', 'station');
  // Axes
  ctx.strokeStyle = 'rgba(255,255,255,0.2)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(40, 10);
  ctx.lineTo(40, H - 20);
  ctx.lineTo(W - 20, H - 20);
  ctx.stroke();
  ctx.fillStyle = '#8899aa';
  ctx.font = '12px -apple-system, sans-serif';
  ctx.fillText(`${vMax} A`, 4, mapY(vMax) + 4);
  ctx.fillText('0', 20, H - 22);

  // X-axis ticks and labels (HH:MM)
  const numTicks = 5;
  const step = tSpan / numTicks;
  ctx.fillStyle = '#94a3b8';
  for (let i = 0; i <= numTicks; i++) {
    const tTick = tMin + step * i;
    const x = mapX(tTick);
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.beginPath();
    ctx.moveTo(x, H - 20);
    ctx.lineTo(x, H - 16);
    ctx.stroke();
    const d = new Date(tTick * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const label = `${hh}:${mm}`;
    const textW = ctx.measureText(label).width;
    ctx.fillText(label, Math.min(Math.max(40, x - textW / 2), W - 20 - textW), H - 4);
  }

  // Hover crosshair and tooltip
  const tip = $('chart_tooltip');
  if (chartHistory.hoverT && tip) {
    // Find nearest point in visible range
    let nearest = visible[0];
    let bestDt = Math.abs(chartHistory.hoverT - nearest.t);
    for (let i = 1; i < visible.length; i++) {
      const dt = Math.abs(chartHistory.hoverT - visible[i].t);
      if (dt < bestDt) {
        bestDt = dt;
        nearest = visible[i];
      }
    }
    const x = mapX(nearest.t);
    // Vertical line
    ctx.strokeStyle = 'rgba(148,163,184,0.6)';
    ctx.beginPath();
    ctx.moveTo(x, 10);
    ctx.lineTo(x, H - 20);
    ctx.stroke();
    // Points
    function drawDot(color, value) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, mapY(value), 3, 0, Math.PI * 2);
      ctx.fill();
    }
    drawDot('#22c55e', nearest.current);
    drawDot('#f59e0b', nearest.allowed);
    drawDot('#ef4444', nearest.station);
    // Tooltip content
    const d = new Date(nearest.t * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    tip.innerHTML = `${hh}:${mm}:${ss} — cur ${nearest.current.toFixed(1)} A · allow ${nearest.allowed.toFixed(1)} A · max ${nearest.station.toFixed(0)} A`;
    // Place tooltip
    const rect = canvas.getBoundingClientRect();
    const parent = canvas.parentElement;
    const parentRect = parent ? parent.getBoundingClientRect() : { left: 0, top: 0, width: rect.width };
    const canvasCssW = rect.width;
    const scale = canvasCssW / W;
    const cssX = (x * scale) + (rect.left - parentRect.left);
    const top = (rect.top - parentRect.top) + 12;
    tip.style.left = `${cssX}px`;
    tip.style.top = `${top}px`;
    tip.style.display = '';
  } else if (tip) {
    tip.style.display = 'none';
  }
}

$('range')?.addEventListener('change', e => {
  chartHistory.windowSec = parseInt(e.target.value, 10) || 300;
  drawChart();
});

// Interaction state
let modeDirtyUntil = 0;
let currentDirtyUntil = 0;

function setModeUI(mode) {
  // Only update UI if not recently changed by the user
  if (Date.now() < modeDirtyUntil) {
    return;
  }
  ['mode_manual', 'mode_auto', 'mode_sched'].forEach(id => {
    const btn = $(id);
    btn.classList.remove('active');
    btn.setAttribute('aria-pressed', 'false');
  });

  if (mode === 0) {
    $('mode_manual').classList.add('active');
    $('mode_manual').setAttribute('aria-pressed', 'true');
  } else if (mode === 1) {
    $('mode_auto').classList.add('active');
    $('mode_auto').setAttribute('aria-pressed', 'true');
  } else if (mode === 2) {
    $('mode_sched').classList.add('active');
    $('mode_sched').setAttribute('aria-pressed', 'true');
  }
}

function setChargeUI(enabled) {
  const btn = $('charge_btn');
  const icon = btn.querySelector('.btn-icon');
  const text = btn.querySelector('span:not(.btn-icon)');

  if (enabled) {
    if (icon) {
      icon.textContent = '⏹️';
    }
    if (text) {
      text.textContent = 'Stop';
    }
    btn.classList.remove('start');
    btn.classList.add('stop');
    btn.setAttribute('aria-pressed', 'true');
    btn.setAttribute('aria-label', 'Stop charging');
  } else {
    if (icon) {
      icon.textContent = '▶️';
    }
    if (text) {
      text.textContent = 'Start';
    }
    btn.classList.remove('stop');
    btn.classList.add('start');
    btn.setAttribute('aria-pressed', 'false');
    btn.setAttribute('aria-label', 'Start charging');
  }
}

function setCurrentUI(displayAmps, stationMax) {
  if (Date.now() < currentDirtyUntil) {
    return;
  }
  const slider = $('current_slider');
  $('current_display').textContent = `${Math.round(displayAmps)} A`;
  // Update slider min/max based on station capabilities
  if (slider && stationMax > 0) {
    const max = Math.min(stationMax, 25);
    slider.max = String(max);
    slider.setAttribute('aria-valuemax', String(max));
  }
}

function setConnectionState(ok) {
  const dot = $('conn_dot');
  const text = $('conn_text');
  if (!dot || !text) {
    return;
  }

  // Add transition animation
  dot.style.transition = 'all 0.3s ease';
  text.style.transition = 'all 0.3s ease';

  if (ok) {
    dot.style.background = '#22c55e';
    dot.style.boxShadow = '0 0 0 3px rgba(34,197,94,0.2)';
    text.textContent = 'Online';
    text.style.color = '#22c55e';
  } else {
    dot.style.background = '#ef4444';
    dot.style.boxShadow = '0 0 0 3px rgba(239,68,68,0.2)';
    text.textContent = 'Offline';
    text.style.color = '#ef4444';
  }
}

function showError(msg) {
  const el = document.getElementById('error_banner');
  if (!el) {
    return;
  }
  if (msg) {
    el.textContent = msg;
    el.style.display = '';
  } else {
    el.textContent = '';
    el.style.display = 'none';
  }
}

// Responsive canvas handling
let chartDevicePixelRatio = 0;
function resizeChartCanvas() {
  const canvas = $('chart');
  if (!canvas) {
    return;
  }
  const dpr = window.devicePixelRatio || 1;
  if (chartDevicePixelRatio === dpr && canvas.dataset.sized === '1') {
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const cssWidth = Math.floor(rect.width);
  const cssHeight = Math.floor(rect.height);
  canvas.width = Math.max(320, cssWidth) * dpr;
  canvas.height = Math.max(120, cssHeight) * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  canvas.dataset.sized = '1';
  chartDevicePixelRatio = dpr;
  drawChart();
}
window.addEventListener('resize', () => {
  chartDevicePixelRatio = 0;
  resizeChartCanvas();
});

// Enhanced visual feedback for interactions
function addButtonFeedback(button) {
  button.addEventListener('click', function () {
    this.style.transform = 'scale(0.95)';
    setTimeout(() => {
      this.style.transform = '';
    }, 150);
  });
}

// Wire controls with enhanced feedback
$('mode_manual').addEventListener('click', async () => {
  modeDirtyUntil = Date.now() + 2000;
  setModeUI(0);
  await postJSON('/api/mode', { mode: 0 });
});
$('mode_auto').addEventListener('click', async () => {
  modeDirtyUntil = Date.now() + 2000;
  setModeUI(1);
  await postJSON('/api/mode', { mode: 1 });
});
$('mode_sched').addEventListener('click', async () => {
  modeDirtyUntil = Date.now() + 2000;
  setModeUI(2);
  await postJSON('/api/mode', { mode: 2 });
});

$('charge_btn').addEventListener('click', async () => {
  // Toggle with animation
  const isEnabled = !$('charge_btn').classList.contains('start');
  const btn = $('charge_btn');

  // Add loading state
  btn.style.opacity = '0.7';
  btn.style.pointerEvents = 'none';

  try {
    setChargeUI(!isEnabled);
    await postJSON('/api/startstop', { enabled: !isEnabled });

    // Success animation
    btn.style.transform = 'scale(1.05)';
    setTimeout(() => {
      btn.style.transform = '';
    }, 200);
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error('Failed to toggle charging:', error);
    // Revert UI on error
    setChargeUI(isEnabled);
  } finally {
    btn.style.opacity = '';
    btn.style.pointerEvents = '';
  }
});

// Add feedback to all mode buttons
['mode_manual', 'mode_auto', 'mode_sched'].forEach(id => {
  const btn = $(id);
  if (btn) {
    addButtonFeedback(btn);
  }
});

// Add feedback to charge button
const chargeBtn = $('charge_btn');
if (chargeBtn) {
  addButtonFeedback(chargeBtn);
}

let currentChangeTimer = null;
$('current_slider').addEventListener('input', () => {
  currentDirtyUntil = Date.now() + 2000;
  const slider = $('current_slider');
  $('current_display').textContent = `${Math.round(slider.value)} A`;
  slider.setAttribute('aria-valuenow', String(Math.round(slider.value)));
  if (currentChangeTimer) {
    clearTimeout(currentChangeTimer);
  }
  currentChangeTimer = setTimeout(async () => {
    const amps = parseFloat(slider.value);
    await postJSON('/api/set_current', { amps });
  }, 400);
});

// Extend dirty window while the user is dragging the slider
$('current_slider').addEventListener('pointerdown', () => {
  currentDirtyUntil = Date.now() + 5000;
});
$('current_slider').addEventListener('pointerup', () => {
  currentDirtyUntil = Date.now() + 1500;
});

// Chart hover listeners
(function initChartHover() {
  const canvas = $('chart');
  if (!canvas) return;
  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.width / dpr;
    // Recompute current visible window mapping
    if (chartHistory.points.length < 2) return;
    const tEnd = chartHistory.points[chartHistory.points.length - 1].t;
    const tMinDesired = tEnd - chartHistory.windowSec;
    const visible = chartHistory.points.filter(p => p.t >= tMinDesired);
    if (visible.length < 2) return;
    const tMin = visible[0].t;
    const tMax = visible[visible.length - 1].t;
    const tSpan = Math.max(1, tMax - tMin);
    const rectW = rect.width;
    const x = Math.max(40, Math.min(rectW - 20, cssX));
    const frac = (x - 40) / Math.max(1, rectW - 60);
    chartHistory.hoverT = tMin + frac * tSpan;
    drawChart();
  });
  canvas.addEventListener('mouseleave', () => {
    chartHistory.hoverT = null;
    drawChart();
  });
})();

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    const s = await res.json();
    setConnectionState(true);
    showError('');
    window.lastStatusData = s; // Store for session timer
    setTextIfExists('product', s.product_name || '');
    setTextIfExists('serial', s.serial ? `SN ${s.serial}` : '');
    setTextIfExists('firmware', s.firmware ? `FW ${s.firmware}` : '');
    setModeUI(Number(s.mode ?? 0));
    setChargeUI(Number(s.start_stop ?? 1) === 1);
    // Determine which current to display based on mode
    const mode = Number(s.mode ?? 0);
    const setpoint = Number(s.set_current ?? 6.0);
    let displayCurrent = setpoint;
    if (mode === 1) {
      // AUTO
      displayCurrent = Number(s.applied_current ?? setpoint);
    } else if (mode === 2) {
      // SCHEDULED
      displayCurrent = Number(s.applied_current ?? setpoint);
    }
    // Update display and slider separately
    const stationMax = Number(s.station_max_current ?? 0);
    setCurrentUI(displayCurrent, stationMax);
    const slider = $('current_slider');
    if (slider && Date.now() >= currentDirtyUntil) {
      slider.value = String(setpoint);
      slider.setAttribute('aria-valuenow', String(Math.round(setpoint)));
    }
    setTextIfExists('di', s.device_instance ?? '');
    const stName = statusNames[s.status] || '-';
    setTextIfExists('status', stName);
    setTextIfExists('status_text', s.status === 2 ? 'Charging 3P' : stName);
    const p = Number(s.ac_power || 0);

    // Animate power value changes
    const powerEl = $('hero_power_w');
    if (powerEl) {
      const currentPower = parseInt(powerEl.textContent) || 0;
      const newPower = Math.round(p);

      if (Math.abs(newPower - currentPower) > 10) {
        powerEl.style.transform = 'scale(1.1)';
        powerEl.style.transition = 'all 0.3s ease';
        setTimeout(() => {
          powerEl.style.transform = '';
        }, 300);
      }

      // Display power in watts
      powerEl.textContent = newPower;
    }
    // Display power in kW with one decimal for the status card (if present)
    setTextIfExists('active_power', (p / 1000).toFixed(1));

    // Update session info elements with actual data from backend
    if ($('session_time')) {
      // Use session data from backend if available
      if (s.session && s.session.start_ts) {
        const startTime = new Date(s.session.start_ts).getTime();
        const endTime = s.session.end_ts ? new Date(s.session.end_ts).getTime() : Date.now();
        const duration = Math.floor((endTime - startTime) / 1000);
        const hours = Math.floor(duration / 3600);
        const minutes = Math.floor((duration % 3600) / 60);
        const seconds = duration % 60;
        $('session_time').textContent = `${hours.toString().padStart(2, '0')}:${minutes
          .toString()
          .padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
      } else if (s.charging_time) {
        // Use ChargingTime from D-Bus if available (in seconds)
        const duration = s.charging_time;
        const hours = Math.floor(duration / 3600);
        const minutes = Math.floor((duration % 3600) / 60);
        const seconds = duration % 60;
        $('session_time').textContent = `${hours.toString().padStart(2, '0')}:${minutes
          .toString()
          .padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
      } else {
        $('session_time').textContent = '00:00:00';
      }
    }
    if ($('session_energy')) {
      // Use actual session energy from Ac/Energy/Forward
      $('session_energy').textContent = (s.energy_forward_kwh ?? 0).toFixed(2);
    }
    if ($('session_cost')) {
      // Prefer server-calculated session_cost (hourly price aware) if provided
      let cost = s.session_cost;
      if (cost == null) {
        const energy = s.energy_forward_kwh ?? 0;
        // Fallback: flat rate per kWh when hourly breakdown is unavailable
        const rate = s.energy_rate ?? 0.25;
        cost = energy * rate;
      }
      $('session_cost').textContent = Number(cost).toFixed(2);
    }
    if ($('total_energy')) {
      // Use total lifetime energy if available
      // TODO: Get actual total energy from Modbus registers
      const totalEnergy = s.total_energy_kwh ?? 0;
      $('total_energy').textContent = totalEnergy.toFixed(2);
    }
    // Update active status indicator
    if ($('active_status')) {
      $('active_status').style.color = s.status === 2 ? '#22c55e' : '#666';
    }
    // Update charging port animation
    const chargingPort = document.querySelector('.charging-port');
    if (chargingPort) {
      chargingPort.style.fill = s.status === 2 ? '#22c55e' : '#666';
    }
    setTextIfExists('ac_current', `${(s.ac_current ?? 0).toFixed(2)} A`);
    setTextIfExists('ac_power', `${Math.round(p)} W`);
    setTextIfExists('energy', `${(s.energy_forward_kwh ?? 0).toFixed(3)} kWh`);
    setTextIfExists(
      'l1',
      `${(s.l1_voltage ?? 0).toFixed(1)} V / ${(s.l1_current ?? 0).toFixed(2)} A / ${Math.round(
        s.l1_power ?? 0
      )} W`
    );
    setTextIfExists(
      'l2',
      `${(s.l2_voltage ?? 0).toFixed(1)} V / ${(s.l2_current ?? 0).toFixed(2)} A / ${Math.round(
        s.l2_power ?? 0
      )} W`
    );
    setTextIfExists(
      'l3',
      `${(s.l3_voltage ?? 0).toFixed(1)} V / ${(s.l3_current ?? 0).toFixed(2)} A / ${Math.round(
        s.l3_power ?? 0
      )} W`
    );
    addHistoryPoint(s);
    // only rebuild form when closed to avoid flicker while editing
    if (!isConfigOpen && currentSchema && currentConfig) {
      // no-op here; form rebuild is heavy and only needed after save
    }
  } catch (e) {
    setConnectionState(false);
    showError('Failed to fetch status. Retrying…');
    // eslint-disable-next-line no-console
    console.error('status error', e);
  }
}

async function getJSON(url) {
  const res = await fetch(url);
  return await res.json();
}

async function postJSON(url, payload, method = 'POST') {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return await res.json();
}

function createInput(fieldKey, def, value, path) {
  const wrap = document.createElement('div');
  wrap.className = 'form-field';
  const id = `${path.join('__')}`;
  let labelText = def.title || fieldKey;
  const label = document.createElement('label');
  label.htmlFor = id;
  label.textContent = labelText;
  wrap.appendChild(label);

  let input = null;
  let error = document.createElement('div');
  error.className = 'error';
  error.style.display = 'none';

  switch (def.type) {
    case 'string': {
      input = document.createElement('input');
      input.type = 'text';
      if (def.format === 'ipv4') {
        input.placeholder = 'e.g. 192.168.1.100';
        input.pattern = '^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}$';
      }
      input.value = value ?? '';
      break;
    }
    case 'integer': {
      input = document.createElement('input');
      input.type = 'number';
      input.step = '1';
      if (def.min !== null && def.min !== undefined) {
        input.min = String(def.min);
      }
      if (def.max !== null && def.max !== undefined) {
        input.max = String(def.max);
      }
      input.value = value !== null && value !== undefined ? String(value) : '';
      break;
    }
    case 'number': {
      input = document.createElement('input');
      input.type = 'number';
      input.step = def.step !== null && def.step !== undefined ? String(def.step) : 'any';
      if (def.min !== null && def.min !== undefined) {
        input.min = String(def.min);
      }
      if (def.max !== null && def.max !== undefined) {
        input.max = String(def.max);
      }
      input.value = value !== null && value !== undefined ? String(value) : '';
      break;
    }
    case 'boolean': {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!value;
      break;
    }
    case 'enum': {
      input = document.createElement('select');
      (def.values || []).forEach(opt => {
        const o = document.createElement('option');
        o.value = String(opt);
        o.textContent = String(opt);
        if (String(value) === String(opt)) {
          o.selected = true;
        }
        input.appendChild(o);
      });
      break;
    }
    case 'time': {
      input = document.createElement('input');
      input.type = 'time';
      input.value = value || '00:00';
      break;
    }
    case 'array': {
      // Only special case we support here is days-of-week chips
      const container = document.createElement('div');
      container.className = 'days';
      const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      const set = new Set((value || []).map(n => Number(n)));
      days.forEach((name, idx) => {
        const chip = document.createElement('div');
        chip.className = 'day-chip' + (set.has(idx) ? ' active' : '');
        chip.textContent = name;
        chip.addEventListener('click', () => {
          if (chip.classList.contains('active')) {
            chip.classList.remove('active');
          } else {
            chip.classList.add('active');
          }
        });
        container.appendChild(chip);
      });
      input = container;
      break;
    }
    default: {
      input = document.createElement('input');
      input.type = 'text';
      input.value = value ?? '';
    }
  }
  input.id = id;
  wrap.appendChild(input);
  wrap.appendChild(error);
  return wrap;
}

function getValueFromInput(input, def) {
  if (def.type === 'boolean') {
    return input.checked;
  }
  if (def.type === 'integer') {
    return input.value === '' ? null : parseInt(input.value, 10);
  }
  if (def.type === 'number') {
    return input.value === '' ? null : parseFloat(input.value);
  }
  if (def.type === 'array' && def.ui === 'days') {
    const arr = [];
    Array.from(input.querySelectorAll('.day-chip')).forEach((chip, idx) => {
      if (chip.classList.contains('active')) {
        arr.push(idx);
      }
    });
    return arr;
  }
  return input.value;
}

function validateField(input, def) {
  let val = getValueFromInput(input, def);
  let error = '';
  if ((def.type === 'integer' || def.type === 'number') && val !== null && val !== undefined) {
    if (def.min !== null && def.min !== undefined && val < def.min) {
      error = `Must be ≥ ${def.min}`;
    }
    if (!error && def.max !== null && def.max !== undefined && val > def.max) {
      error = `Must be ≤ ${def.max}`;
    }
  }
  if (def.type === 'string' && def.format === 'ipv4' && val) {
    const re = /^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/;
    if (!re.test(val)) {
      error = 'Invalid IPv4 address';
    }
  }
  const errEl = input.parentElement.querySelector('.error');
  if (error) {
    errEl.textContent = error;
    errEl.style.display = '';
    return { ok: false, value: val };
  }
  errEl.textContent = '';
  errEl.style.display = 'none';
  return { ok: true, value: val };
}

function buildSection(container, key, sectionDef, cfg) {
  const section = document.createElement('div');
  section.className = 'section' + (sectionDef.advanced ? ' advanced' : '');
  const header = document.createElement('div');
  header.className = 'section-header';
  const title = document.createElement('div');
  title.className = 'section-title';
  title.textContent = sectionDef.title || key;
  header.appendChild(title);
  const body = document.createElement('div');
  body.className = 'section-body';
  header.addEventListener('click', () => {
    body.style.display = body.style.display === 'none' ? '' : 'none';
  });
  section.appendChild(header);
  section.appendChild(body);

  if (sectionDef.type === 'object') {
    const fields = sectionDef.fields || {};
    Object.keys(fields).forEach(fkey => {
      const def = fields[fkey];
      const value = cfg && cfg[fkey];
      const fieldEl = createInput(fkey, def, value, [key, fkey]);
      fieldEl.dataset.path = JSON.stringify([key, fkey]);
      body.appendChild(fieldEl);
    });
  } else if (sectionDef.type === 'list') {
    const listWrap = document.createElement('div');
    listWrap.className = 'list-items';
    const items = (cfg && cfg.items) || [];

    // eslint-disable-next-line no-inner-declarations
    function addItem(itemCfg = {}) {
      const itemEl = document.createElement('div');
      itemEl.className = 'list-item';
      const itemBody = document.createElement('div');
      const fields = sectionDef.item.fields || {};
      Object.keys(fields).forEach(fkey => {
        const def = fields[fkey];
        const value = itemCfg[fkey];
        const fieldEl = createInput(fkey, def, value, [
          key,
          'items',
          String(listWrap.children.length),
          fkey,
        ]);
        itemBody.appendChild(fieldEl);
      });
      const actions = document.createElement('div');
      actions.className = 'list-actions';
      const removeBtn = document.createElement('button');
      removeBtn.className = 'remove-btn';
      removeBtn.textContent = 'Remove';
      removeBtn.addEventListener('click', () => {
        listWrap.removeChild(itemEl);
      });
      actions.appendChild(removeBtn);
      itemEl.appendChild(itemBody);
      itemEl.appendChild(actions);
      listWrap.appendChild(itemEl);
    }

    items.forEach(it => addItem(it));
    const add = document.createElement('button');
    add.className = 'add-btn';
    add.textContent = 'Add schedule';
    add.addEventListener('click', () =>
      addItem({ active: false, days: [], start_time: '00:00', end_time: '00:00' })
    );
    body.appendChild(listWrap);
    body.appendChild(add);
  } else if (sectionDef.type === 'integer') {
    const fieldEl = createInput(key, sectionDef, cfg, [key]);
    fieldEl.dataset.path = JSON.stringify([key]);
    body.appendChild(fieldEl);
  } else if (sectionDef.type === 'string') {
    const fieldEl = createInput(key, sectionDef, cfg, [key]);
    fieldEl.dataset.path = JSON.stringify([key]);
    body.appendChild(fieldEl);
  }

  container.appendChild(section);
}

function buildForm(schema, cfg) {
  const root = $('config_form');
  if (!root) {
    return;
  }

  root.innerHTML = '';
  const sections = schema.sections || {};
  const nav = $('config_nav');
  if (nav) {
    nav.innerHTML = '';
  }

  Object.keys(sections).forEach((key, idx) => {
    buildSection(root, key, sections[key], cfg[key]);

    if (nav) {
      const chip = document.createElement('div');
      chip.className = 'chip' + (idx === 0 ? ' active' : '');
      chip.textContent = sections[key].title || key;
      chip.addEventListener('click', () => {
        const sectionEls = Array.from(root.getElementsByClassName('section'));
        const target = sectionEls[idx];
        if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        nav.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
      });
      nav.appendChild(chip);
    }
  });
}

function collectConfig(schema) {
  const cfg = JSON.parse(JSON.stringify(currentConfig));
  const sections = schema.sections || {};
  const root = $('config_form');

  // Handle object sections
  Object.keys(sections).forEach(key => {
    const def = sections[key];
    if (def.type === 'object') {
      const fields = def.fields || {};
      cfg[key] = cfg[key] || {};
      Object.keys(fields).forEach(fkey => {
        const fieldDef = fields[fkey];
        const fieldEl = Array.from(root.querySelectorAll('.form-field')).find(el => {
          const path = el.dataset.path && JSON.parse(el.dataset.path);
          return path && path[0] === key && path[1] === fkey;
        });
        if (!fieldEl) {
          return;
        }
        const input = fieldEl.querySelector('input, select, .days');
        const { ok, value } = validateField(input, fieldDef);
        if (!ok) {
          throw new Error(`${key}.${fkey}: invalid`);
        }
        cfg[key][fkey] = value;
      });
    } else if (def.type === 'list') {
      cfg[key] = cfg[key] || {};
      cfg[key].items = [];
      const listWrap = root.querySelector('.section-body .list-items');
      if (listWrap) {
        Array.from(listWrap.children).forEach(itemEl => {
          const fields = def.item.fields || {};
          const item = {};
          Object.keys(fields).forEach(fkey => {
            const input =
              itemEl.querySelector(`[id$="__${fkey}"]`) || itemEl.querySelector('.days');
            const { ok, value } = validateField(input, fields[fkey]);
            if (!ok) {
              throw new Error(`schedule.items.${fkey}: invalid`);
            }
            item[fkey] = value;
          });
          cfg[key].items.push(item);
        });
      }
    } else if (def.type === 'integer' || def.type === 'string') {
      const fieldEl = root.querySelector('.section-body .form-field');
      if (fieldEl) {
        const input = fieldEl.querySelector('input');
        const { ok, value } = validateField(input, def);
        if (!ok) {
          throw new Error(`${key}: invalid`);
        }
        cfg[key] = value;
      }
    }
  });
  return cfg;
}

async function saveConfig() {
  const statusEl = $('config_status');
  const saveBtn = $('save_config');

  try {
    if (statusEl) {
      statusEl.textContent = 'Saving...';
      statusEl.style.background = 'rgba(59, 130, 246, 0.1)';
      statusEl.style.color = '#3b82f6';
    }

    if (saveBtn) {
      saveBtn.style.opacity = '0.7';
      saveBtn.style.pointerEvents = 'none';
    }

    const payload = collectConfig(currentSchema);
    const resp = await postJSON('/api/config', payload, 'PUT');

    if (resp.ok) {
      if (statusEl) {
        statusEl.textContent = '✅ Configuration saved successfully!';
        statusEl.style.background = 'rgba(16, 185, 129, 0.1)';
        statusEl.style.color = '#10b981';
        setTimeout(() => {
          statusEl.textContent = '';
          statusEl.style.background = '';
          statusEl.style.color = '';
        }, 3000);
      }
      currentConfig = payload;
      fetchStatus();
    } else {
      if (statusEl) {
        statusEl.textContent = `❌ Error: ${resp.error || 'Validation failed'}`;
        statusEl.style.background = 'rgba(239, 68, 68, 0.1)';
        statusEl.style.color = '#ef4444';
      }
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = `❌ ${e.message || 'Invalid configuration'}`;
      statusEl.style.background = 'rgba(239, 68, 68, 0.1)';
      statusEl.style.color = '#ef4444';
    }
  } finally {
    if (saveBtn) {
      saveBtn.style.opacity = '';
      saveBtn.style.pointerEvents = '';
    }
  }
}

async function initConfigForm() {
  try {
    [currentSchema, currentConfig] = await Promise.all([
      getJSON('/api/config/schema'),
      getJSON('/api/config'),
    ]);
    buildForm(currentSchema, currentConfig);
  } catch (e) {
    const statusEl = $('config_status');
    if (statusEl) {
      statusEl.textContent = '❌ Failed to load configuration UI';
      statusEl.style.background = 'rgba(239, 68, 68, 0.1)';
      statusEl.style.color = '#ef4444';
    }
    // eslint-disable-next-line no-console
    console.error('Failed to initialize config form:', e);
  }
}

// Enhanced view management
let isConfigOpen = false;

function switchView(viewName) {
  const dashboardContent = $('dashboard_content');
  const configContent = $('config_content');
  const dashboardBtn = $('dashboard_view');
  const configBtn = $('config_view');

  if (!dashboardContent || !configContent || !dashboardBtn || !configBtn) {
    return;
  }

  // Handle view switching with smooth animation
  if (viewName === 'dashboard') {
    configContent.style.opacity = '0';
    setTimeout(() => {
      configContent.style.display = 'none';
      dashboardContent.style.display = 'block';
      dashboardContent.style.opacity = '1';
    }, 150);

    // Update button states
    dashboardBtn.classList.add('active');
    configBtn.classList.remove('active');
    dashboardBtn.setAttribute('aria-pressed', 'true');
    configBtn.setAttribute('aria-pressed', 'false');

    isConfigOpen = false;
  } else if (viewName === 'config') {
    dashboardContent.style.opacity = '0';
    setTimeout(() => {
      dashboardContent.style.display = 'none';
      configContent.style.display = 'block';
      configContent.style.opacity = '1';
    }, 150);

    // Update button states
    configBtn.classList.add('active');
    dashboardBtn.classList.remove('active');
    configBtn.setAttribute('aria-pressed', 'true');
    dashboardBtn.setAttribute('aria-pressed', 'false');

    isConfigOpen = true;

    // Initialize config form if not already done
    if (currentSchema && currentConfig) {
      buildForm(currentSchema, currentConfig);
    }
  }
}

function initUX() {
  // View toggle functionality
  const dashboardBtn = $('dashboard_view');
  const configBtn = $('config_view');

  if (dashboardBtn) {
    dashboardBtn.addEventListener('click', () => switchView('dashboard'));
    addButtonFeedback(dashboardBtn);
  }

  if (configBtn) {
    configBtn.addEventListener('click', () => switchView('config'));
    addButtonFeedback(configBtn);
  }

  // Configuration functionality
  const showAdvanced = $('show_advanced');
  if (showAdvanced) {
    showAdvanced.addEventListener('change', () => {
      if (showAdvanced.checked) {
        document.body.classList.add('show-advanced');
      } else {
        document.body.classList.remove('show-advanced');
      }
    });
  }

  const saveBtn = $('save_config');
  if (saveBtn) {
    saveBtn.addEventListener('click', saveConfig);
    addButtonFeedback(saveBtn);
  }

  // Start with dashboard view
  switchView('dashboard');
}

// Removed old control functions that are no longer needed

// Kick off
resizeChartCanvas();
fetchStatus();
initConfigForm();
initUX();
// Reduce polling frequency to 2s to lower UI churn
setInterval(() => {
  resizeChartCanvas();
  fetchStatus();
}, 2000);

// Update session time more frequently when charging
setInterval(() => {
  const sessionTimeEl = $('session_time');
  if (sessionTimeEl && window.lastStatusData && window.lastStatusData.status === 2) {
    // Update time display if actively charging
    if (window.lastStatusData.session && window.lastStatusData.session.start_ts) {
      const startTime = new Date(window.lastStatusData.session.start_ts).getTime();
      const duration = Math.floor((Date.now() - startTime) / 1000);
      const hours = Math.floor(duration / 3600);
      const minutes = Math.floor((duration % 3600) / 60);
      const seconds = duration % 60;
      sessionTimeEl.textContent = `${hours.toString().padStart(2, '0')}:${minutes
        .toString()
        .padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    }
  }
}, 1000);
