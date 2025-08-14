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

async function fetchStatus() {
	try {
		const res = await fetch('/api/status');
		const s = await res.json();
		$('product').textContent = s.product_name || '';
		$('serial').textContent = s.serial ? `SN ${s.serial}` : '';
		$('firmware').textContent = s.firmware ? `FW ${s.firmware}` : '';

		$('mode').value = String(s.mode ?? 0);
		$('startstop').checked = (s.start_stop ?? 1) === 1;
		$('setcurrent').value = (s.set_current ?? 6.0).toFixed(1);
		$('di').textContent = s.device_instance ?? '';

		$('status').textContent = statusNames[s.status] || '-';
		$('ac_current').textContent = `${(s.ac_current ?? 0).toFixed(2)} A`;
		$('ac_power').textContent = `${Math.round(s.ac_power ?? 0)} W`;
		$('energy').textContent = `${(s.energy_forward_kwh ?? 0).toFixed(3)} kWh`;
		$('l1').textContent = `${(s.l1_voltage ?? 0).toFixed(1)} V / ${(s.l1_current ?? 0).toFixed(2)} A / ${Math.round(s.l1_power ?? 0)} W`;
		$('l2').textContent = `${(s.l2_voltage ?? 0).toFixed(1)} V / ${(s.l2_current ?? 0).toFixed(2)} A / ${Math.round(s.l2_power ?? 0)} W`;
		$('l3').textContent = `${(s.l3_voltage ?? 0).toFixed(1)} V / ${(s.l3_current ?? 0).toFixed(2)} A / ${Math.round(s.l3_power ?? 0)} W`;
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
				input.pattern = '^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$';
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
		const re = /^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/;
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
	section.className = 'section';
	const header = document.createElement('div');
	header.className = 'section-header';
	const title = document.createElement('div');
	title.className = 'section-title';
	title.textContent = sectionDef.title || key;
	header.appendChild(title);
	const body = document.createElement('div');
	body.className = 'section-body';
	if (sectionDef.advanced) {
		body.style.display = 'none';
	}
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
	Object.keys(sections).forEach((key) => {
		buildSection(root, key, sections[key], cfg[key]);
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

async function applyControls() {
	const mode = parseInt($('mode').value, 10);
	await postJSON('/api/mode', { mode });

	const enabled = $('startstop').checked;
	await postJSON('/api/startstop', { enabled });

	const amps = parseFloat($('setcurrent').value);
	await postJSON('/api/set_current', { amps });

	setTimeout(fetchStatus, 300);
}

$('apply').addEventListener('click', applyControls);
$('mode').addEventListener('change', () => postJSON('/api/mode', { mode: parseInt($('mode').value, 10) }));
$('startstop').addEventListener('change', () => postJSON('/api/startstop', { enabled: $('startstop').checked }));
$('setcurrent').addEventListener('change', () => postJSON('/api/set_current', { amps: parseFloat($('setcurrent').value) }));
$('save_config').addEventListener('click', saveConfig);

fetchStatus();
initConfigForm();
setInterval(fetchStatus, 2000);