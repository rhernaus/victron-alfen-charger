const $ = (id) => document.getElementById(id);

const statusNames = {
	0: "Disconnected",
	1: "Connected",
	2: "Charging",
	3: "Charged",
	4: "Wait sun",
	6: "Wait start",
	7: "Low SOC",
};

let currentConfig = null;
let currentSchema = null;

// History series
const history = {
	points: [], // {t, current, allowed, station}
	windowSec: 300,
};

function addHistoryPoint(s) {
	const t = Date.now() / 1000;
	const current = Number(s.ac_current || 0);
	const allowed = Number(s.set_current || 0);
	const station = Number(s.station_max_current || 0);
	history.points.push({ t, current, allowed, station });
	const cutoff = t - history.windowSec;
	history.points = history.points.filter((p) => p.t >= cutoff);
	drawChart();
}

function drawChart() {
	const canvas = $('chart');
	if (!canvas) return;
	const ctx = canvas.getContext('2d');
	const W = canvas.width; const H = canvas.height;
	ctx.clearRect(0, 0, W, H);
	ctx.fillStyle = '#0a1228';
	ctx.fillRect(0, 0, W, H);
	if (history.points.length < 2) return;
	const tMin = history.points[0].t;
	const tMax = history.points[history.points.length - 1].t;
	const tSpan = Math.max(1, tMax - tMin);
	let vMax = 0;
	history.points.forEach((p) => { vMax = Math.max(vMax, p.current, p.allowed, p.station); });
	vMax = Math.max(10, Math.ceil(vMax / 5) * 5);
	function mapX(t) { return 40 + ((t - tMin) / tSpan) * (W - 60); }
	function mapY(v) { return H - 20 - (v / vMax) * (H - 40); }
	// Grid
	ctx.strokeStyle = '#1c273a'; ctx.lineWidth = 1;
	for (let i = 0; i <= 5; i++) { const y = mapY((vMax/5)*i); ctx.beginPath(); ctx.moveTo(40, y); ctx.lineTo(W-20, y); ctx.stroke(); }
	// Series draw function
	function plot(color, key) {
		ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
		history.points.forEach((p, idx) => {
			const x = mapX(p.t); const y = mapY(p[key]);
			if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
		}); ctx.stroke();
	}
	plot('#22c55e', 'current');
	plot('#f59e0b', 'allowed');
	plot('#ef4444', 'station');
	// Axes
	ctx.strokeStyle = '#273042'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(40, 10); ctx.lineTo(40, H-20); ctx.lineTo(W-20, H-20); ctx.stroke();
	ctx.fillStyle = '#9fb0c8'; ctx.font = '12px Inter, sans-serif';
	ctx.fillText(`${vMax} A`, 4, mapY(vMax) + 4);
	ctx.fillText('0', 20, H-22);
}

$('range')?.addEventListener('change', (e) => {
	history.windowSec = parseInt(e.target.value, 10) || 300;
	drawChart();
});

// Interaction state
let isChangingMode = false;
let isChangingCurrent = false;
let modeDirtyUntil = 0;
let currentDirtyUntil = 0;

function setModeUI(mode) {
	// Only update UI if not recently changed by the user
	if (Date.now() < modeDirtyUntil) return;
	['mode_manual','mode_auto','mode_sched'].forEach((id) => $(id).classList.remove('active'));
	if (mode === 0) $('mode_manual').classList.add('active');
	else if (mode === 1) $('mode_auto').classList.add('active');
	else if (mode === 2) $('mode_sched').classList.add('active');
}

function setChargeUI(enabled) {
	const btn = $('charge_btn');
	if (enabled) { btn.textContent = 'Stop'; btn.classList.remove('off'); btn.classList.add('on'); } else { btn.textContent = 'Start'; btn.classList.add('off'); }
}

function setCurrentUI(amps, stationMax) {
	if (Date.now() < currentDirtyUntil) return;
	$('current_slider').value = String(amps);
	$('current_value').textContent = `${Number(amps).toFixed(1)} A`;
	$('station_max').textContent = `${Number(stationMax || 0).toFixed(1)} A`;
}

// Wire controls
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
	// Toggle
	const isOn = $('charge_btn').classList.contains('on');
	setChargeUI(!isOn);
	await postJSON('/api/startstop', { enabled: !isOn });
});

let currentChangeTimer = null;
$('current_slider').addEventListener('input', () => {
	currentDirtyUntil = Date.now() + 2000;
	$('current_value').textContent = `${Number($('current_slider').value).toFixed(1)} A`;
	if (currentChangeTimer) clearTimeout(currentChangeTimer);
	currentChangeTimer = setTimeout(async () => {
		const amps = parseFloat($('current_slider').value);
		await postJSON('/api/set_current', { amps });
	}, 400);
});

async function fetchStatus() {
	try {
		const res = await fetch('/api/status');
		const s = await res.json();
		$('product').textContent = s.product_name || '';
		$('serial').textContent = s.serial ? `SN ${s.serial}` : '';
		$('firmware').textContent = s.firmware ? `FW ${s.firmware}` : '';
		setModeUI(Number(s.mode ?? 0));
		setChargeUI(Number(s.start_stop ?? 1) === 1);
		setCurrentUI(Number(s.set_current ?? 6.0), Number(s.station_max_current ?? 0));
		$('di').textContent = s.device_instance ?? '';
		const stName = statusNames[s.status] || '-';
		$('status').textContent = stName;
		$('status_text').textContent = stName;
		$('hero_power').textContent = `${Math.round(s.ac_power ?? 0)} W`;
		$('hero_energy').textContent = `${(s.energy_forward_kwh ?? 0).toFixed(3)} kWh`;
		$('hero_current').textContent = `${(s.ac_current ?? 0).toFixed(2)} A`;
		// status dot color
		const dot = $('status_dot');
		dot.classList.remove('ok','warn','err');
		if (s.status === 2) dot.classList.add('ok');
		else if (s.status === 4 || s.status === 6) dot.classList.add('warn');
		else if (s.status === 7) dot.classList.add('err');
		$('ac_current').textContent = `${(s.ac_current ?? 0).toFixed(2)} A`;
		$('ac_power').textContent = `${Math.round(s.ac_power ?? 0)} W`;
		$('energy').textContent = `${(s.energy_forward_kwh ?? 0).toFixed(3)} kWh`;
		$('l1').textContent = `${(s.l1_voltage ?? 0).toFixed(1)} V / ${(s.l1_current ?? 0).toFixed(2)} A / ${Math.round(s.l1_power ?? 0)} W`;
		$('l2').textContent = `${(s.l2_voltage ?? 0).toFixed(1)} V / ${(s.l2_current ?? 0).toFixed(2)} A / ${Math.round(s.l2_power ?? 0)} W`;
		$('l3').textContent = `${(s.l3_voltage ?? 0).toFixed(1)} V / ${(s.l3_current ?? 0).toFixed(2)} A / ${Math.round(s.l3_power ?? 0)} W`;
		addHistoryPoint(s);
		// only rebuild form when closed to avoid flicker while editing
		if (!isConfigOpen && currentSchema && currentConfig) {
			// no-op here; form rebuild is heavy and only needed after save
		}
	} catch (e) {
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
			if (def.min != null) input.min = String(def.min);
			if (def.max != null) input.max = String(def.max);
			input.value = value != null ? String(value) : '';
			break;
		}
		case 'number': {
			input = document.createElement('input');
			input.type = 'number';
			input.step = def.step != null ? String(def.step) : 'any';
			if (def.min != null) input.min = String(def.min);
			if (def.max != null) input.max = String(def.max);
			input.value = value != null ? String(value) : '';
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
			(def.values || []).forEach((opt) => {
				const o = document.createElement('option');
				o.value = String(opt);
				o.textContent = String(opt);
				if (String(value) === String(opt)) o.selected = true;
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
			const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
			const set = new Set((value || []).map((n) => Number(n)));
			days.forEach((name, idx) => {
				const chip = document.createElement('div');
				chip.className = 'day-chip' + (set.has(idx) ? ' active' : '');
				chip.textContent = name;
				chip.addEventListener('click', () => {
					if (chip.classList.contains('active')) chip.classList.remove('active');
					else chip.classList.add('active');
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
	if (def.type === 'boolean') return input.checked;
	if (def.type === 'integer') return input.value === '' ? null : parseInt(input.value, 10);
	if (def.type === 'number') return input.value === '' ? null : parseFloat(input.value);
	if (def.type === 'array' && def.ui === 'days') {
		const arr = [];
		Array.from(input.querySelectorAll('.day-chip')).forEach((chip, idx) => {
			if (chip.classList.contains('active')) arr.push(idx);
		});
		return arr;
	}
	return input.value;
}

function validateField(input, def) {
	let val = getValueFromInput(input, def);
	let error = '';
	if ((def.type === 'integer' || def.type === 'number') && val != null) {
		if (def.min != null && val < def.min) error = `Must be ≥ ${def.min}`;
		if (!error && def.max != null && val > def.max) error = `Must be ≤ ${def.max}`;
	}
	if (def.type === 'string' && def.format === 'ipv4' && val) {
		const re = /^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}$/;
		if (!re.test(val)) error = 'Invalid IPv4 address';
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
		Object.keys(fields).forEach((fkey) => {
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

		function addItem(itemCfg = {}) {
			const itemEl = document.createElement('div');
			itemEl.className = 'list-item';
			const itemBody = document.createElement('div');
			const fields = sectionDef.item.fields || {};
			Object.keys(fields).forEach((fkey) => {
				const def = fields[fkey];
				const value = itemCfg[fkey];
				const fieldEl = createInput(fkey, def, value, [key, 'items', String(listWrap.children.length), fkey]);
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

		items.forEach((it) => addItem(it));
		const add = document.createElement('button');
		add.className = 'add-btn';
		add.textContent = 'Add schedule';
		add.addEventListener('click', () => addItem({ active: false, days: [], start_time: '00:00', end_time: '00:00' }));
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
	root.innerHTML = '';
	const sections = schema.sections || {};
	const nav = $('config_nav');
	nav.innerHTML = '';
	Object.keys(sections).forEach((key, idx) => {
		buildSection(root, key, sections[key], cfg[key]);
		const chip = document.createElement('div');
		chip.className = 'chip' + (idx === 0 ? ' active' : '');
		chip.textContent = sections[key].title || key;
		chip.addEventListener('click', () => {
			const sectionEls = Array.from(root.getElementsByClassName('section'));
			const target = sectionEls[idx];
			if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
			nav.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
			chip.classList.add('active');
		});
		nav.appendChild(chip);
	});
}

function collectConfig(schema) {
	const cfg = JSON.parse(JSON.stringify(currentConfig));
	const sections = schema.sections || {};
	const root = $('config_form');

	// Handle object sections
	Object.keys(sections).forEach((key) => {
		const def = sections[key];
		if (def.type === 'object') {
			const fields = def.fields || {};
			cfg[key] = cfg[key] || {};
			Object.keys(fields).forEach((fkey) => {
				const fieldDef = fields[fkey];
				const fieldEl = Array.from(root.querySelectorAll('.form-field')).find((el) => {
					const path = el.dataset.path && JSON.parse(el.dataset.path);
					return path && path[0] === key && path[1] === fkey;
				});
				if (!fieldEl) return;
				const input = fieldEl.querySelector('input, select, .days');
				const { ok, value } = validateField(input, fieldDef);
				if (!ok) throw new Error(`${key}.${fkey}: invalid`);
				cfg[key][fkey] = value;
			});
		} else if (def.type === 'list') {
			cfg[key] = cfg[key] || {};
			cfg[key].items = [];
			const listWrap = root.querySelector('.section-body .list-items');
			if (listWrap) {
				Array.from(listWrap.children).forEach((itemEl) => {
					const fields = def.item.fields || {};
					const item = {};
					Object.keys(fields).forEach((fkey) => {
						const inputs = itemEl.querySelectorAll('.form-field');
						const fieldEl = Array.from(inputs).find((el) => el.querySelector('input, select, .days'));
						const input = itemEl.querySelector(`[id$="__${fkey}"]`) || itemEl.querySelector('.days');
						const { ok, value } = validateField(input, fields[fkey]);
						if (!ok) throw new Error(`schedule.items.${fkey}: invalid`);
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
				if (!ok) throw new Error(`${key}: invalid`);
				cfg[key] = value;
			}
		}
	});
	return cfg;
}

async function saveConfig() {
	try {
		$('config_status').textContent = 'Saving...';
		const payload = collectConfig(currentSchema);
		const resp = await postJSON('/api/config', payload, 'PUT');
		if (resp.ok) {
			$('config_status').textContent = 'Saved';
			setTimeout(() => $('config_status').textContent = '', 1200);
			currentConfig = payload;
			fetchStatus();
		} else {
			$('config_status').textContent = `Error: ${resp.error || 'Validation failed'}`;
		}
	} catch (e) {
		$('config_status').textContent = e.message || 'Invalid configuration';
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
		$('config_status').textContent = 'Failed to load configuration UI';
	}
}

// Smarter polling to avoid clobbering edits: only refresh form when closed
let isConfigOpen = false;

function toggleConfigOpen(open) {
	const sec = document.querySelector('section.config');
	if (!sec) return;
	if (open === undefined) sec.classList.toggle('collapsed');
	else sec.classList.toggle('collapsed', !open);
	isConfigOpen = !sec.classList.contains('collapsed');
	$('toggle_config').textContent = isConfigOpen ? 'Hide configuration' : 'Edit configuration';
}

function initUX() {
	$('toggle_config').addEventListener('click', () => toggleConfigOpen());
	$('show_advanced').addEventListener('change', () => {
		if ($('show_advanced').checked) document.body.classList.add('show-advanced');
		else document.body.classList.remove('show-advanced');
	});
	$('expand_all').addEventListener('click', () => {
		document.querySelectorAll('.section .section-body').forEach((el) => el.style.display = '');
	});
	$('collapse_all').addEventListener('click', () => {
		document.querySelectorAll('.section .section-body').forEach((el) => el.style.display = 'none');
	});
	// start collapsed
	toggleConfigOpen(false);
}

async function applyControls() {
	const mode = parseInt($('mode').value, 10);
	await postJSON('/api/mode', { mode });

	const enabled = $('startstop').checked;
	await postJSON('/api/startstop', { enabled });

	const amps = parseFloat($('setcurrent').value);
	await postJSON('/api/set_current', { amps });

	setTimeout(fetchStatus, 300);
}

// Remove old event wires for apply/mode/startstop/setcurrent
try { $('apply').remove(); } catch(e) {}

fetchStatus();
initConfigForm();
initUX();
// Reduce polling frequency to 2s to lower UI churn
setInterval(fetchStatus, 2000);
