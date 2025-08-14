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

async function fetchConfig() {
	try {
		const res = await fetch('/api/config');
		const cfg = await res.json();
		$('config_editor').value = JSON.stringify(cfg, null, 2);
		$('config_status').textContent = '';
	} catch (e) {
		$('config_status').textContent = 'Failed to load configuration';
		console.error('config error', e);
	}
}

async function postJSON(url, payload, method = 'POST') {
	const res = await fetch(url, {
		method,
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(payload),
	});
	return await res.json();
}

async function saveConfig() {
	try {
		$('config_status').textContent = 'Saving...';
		const text = $('config_editor').value;
		const payload = JSON.parse(text);
		const resp = await postJSON('/api/config', payload, 'PUT');
		if (resp.ok) {
			$('config_status').textContent = 'Saved';
			setTimeout(() => $('config_status').textContent = '', 1200);
			// refresh status to reflect any changes
			fetchStatus();
		} else {
			$('config_status').textContent = `Error: ${resp.error || 'Validation failed'}`;
		}
	} catch (e) {
		$('config_status').textContent = 'Invalid JSON';
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
fetchConfig();
setInterval(fetchStatus, 2000);