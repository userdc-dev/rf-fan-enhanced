class RfFanRemoteCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._durations ||= {};
    this._units ||= {};
    if (!this.shadowRoot) this.attachShadow({mode: "open"});
    this._refresh();
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._devices) this._refresh();
    this._subscribe();
    this._updateDynamic();
  }
  getCardSize() { return Math.max(3, (this._selectedFans()?.length || 1) * 5); }
  static getConfigElement() { return document.createElement("rf-fan-remote-card-editor"); }
  static getStubConfig() { return {}; }
  connectedCallback() {
    this._subscribe();
  }
  _subscribe() {
    if (this._hass && this.isConnected && !this._unsubscribe && !this._subscribing) {
      this._subscribing = true;
      this._hass.connection.subscribeEvents(() => this._refresh(), "rf_fan_updated")
        .then(unsub => this._unsubscribe = unsub)
        .finally(() => this._subscribing = false);
    }
  }
  disconnectedCallback() { this._unsubscribe?.(); this._unsubscribe = null; }
  async _refresh() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try { this._devices = await this._hass.callWS({type: "rf_fan/devices"}); }
    catch (err) { this._error = String(err); }
    this._loading = false;
    this._render();
  }
  _call(domain, service, entity_id, data={}) {
    return this._hass.callService(domain, service, data, {entity_id});
  }
  _command(fan, command) { return this._call("rf_fan", "send_command", fan.entity_id, {command}); }
  _selectedFans() {
    const wantedFan = this._config?.fan_id;
    const wantedDevice = this._config?.device_id;
    return (this._devices || []).flatMap(device => device.fans
      .filter(fan => wantedFan && wantedFan !== "all" ? fan.fan_id === wantedFan : (!wantedDevice || wantedDevice === "all" || device.device_id === wantedDevice))
      .map(fan => ({device, fan})));
  }
  _updateDynamic() {
    if (!this.shadowRoot || !this._hass) return;
    this.shadowRoot.querySelectorAll('[data-role="timer-remaining"]').forEach(node => {
      const state = this._hass.states[node.dataset.entity];
      node.firstChild.textContent = `Remaining: ${state?.attributes?.timer_remaining || "0:00:00"} `;
    });
    this.shadowRoot.querySelectorAll('[data-role="timer-start"]').forEach(node => {
      const state = this._hass.states[node.dataset.entity];
      node.textContent = state?.attributes?.timer_remaining_seconds ? "Restart" : "Start";
    });
  }
  _button(label, action, cls="") {
    const button = document.createElement("button");
    button.textContent = label;
    button.className = cls;
    button.onclick = action;
    return button;
  }
  _section(title, commands, fan, collapsible=false) {
    if (!commands.length) return null;
    const body = document.createElement("div"); body.className = "section";
    const buttons = document.createElement("div"); buttons.className = "buttons";
    commands.forEach(cmd => buttons.append(this._button(cmd.label, () => {
      if (title === "Lighting" && cmd.key === "light" && fan.light_entity_id) return this._call("homeassistant", "toggle", fan.light_entity_id);
      return this._command(fan, cmd.command);
    })));
    if (collapsible) {
      const details = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = title;
      details.append(summary, buttons); body.append(details);
    } else {
      const heading = document.createElement("div"); heading.className = "label"; heading.textContent = title;
      body.append(heading, buttons);
    }
    return body;
  }
  _fanCard(device, fan) {
    const wrap = document.createElement("section"); wrap.className = "remote";
    const title = document.createElement("h3"); title.textContent = fan.name || device.name;
    const state = this._hass.states[fan.entity_id];
    if (!state) { wrap.append(title, Object.assign(document.createElement("p"), {textContent: "This RF Fan device is unavailable."})); return wrap; }
    const controls = document.createElement("div"); controls.className = "buttons fan-controls";
    controls.append(this._button("Power", () => this._call("homeassistant", "toggle", fan.entity_id)));
    if (fan.safe_off_enabled) controls.append(this._button("Safe OFF", () => this._call("rf_fan", "safe_off", fan.entity_id), "danger"));
    const speedCommands = fan.commands.filter(c => /^speed_\d+$/.test(c.key)).sort((a,b) => Number(a.key.split("_")[1])-Number(b.key.split("_")[1]));
    const speeds = document.createElement("div"); speeds.className = "buttons speeds";
    speedCommands.slice(0, fan.speed_count).forEach((cmd, index) => speeds.append(this._button(String(index + 1), () => this._call("fan", "set_percentage", fan.entity_id, {percentage: Math.round((index + 1) * 100 / fan.speed_count)}))));
    const modes = fan.commands.filter(c => c.category === "mode" || c.category === "other");
    const lighting = fan.commands.filter(c => c.category === "lighting" && !(fan.light_cycle && ["light", "light_colour"].includes(c.key)));
    const physical = fan.commands.filter(c => c.category === "physical_timer");
    if (fan.light_entity_id && lighting.some(c => c.key === "light")) {
      lighting.find(c => c.key === "light").label = "Light toggle";
    }
    const lightSection = this._section("Lighting", lighting, fan);
    const cycleSection = fan.light_cycle ? document.createElement("div") : null;
    if (cycleSection) {
      cycleSection.className = "section cycle";
      const cycleLabel = document.createElement("div"); cycleLabel.className = "label"; cycleLabel.textContent = "Light toggle sequences (relative)";
      const cycleButtons = document.createElement("div"); cycleButtons.className = "buttons";
      [["cool","Sequence 1"],["neutral","Sequence 2"],["warm","Sequence 3"]].forEach(([mode,label]) => {
        const clicks = fan.light_cycle[mode];
        cycleButtons.append(this._button(`${label} · Send ${clicks}×`, () => this._call("rf_fan", "set_light_cycle", fan.entity_id, {mode})));
      });
      const note = document.createElement("div"); note.className = "diagnostics"; note.textContent = "Relative button presses; the RF light colour is not confirmed.";
      cycleSection.append(cycleLabel, cycleButtons, note);
    }
    const timer = document.createElement("div"); timer.className = "timer section";
    const timerLabel = document.createElement("div"); timerLabel.className = "label"; timerLabel.textContent = "Custom timer";
    const row = document.createElement("div"); row.className = "timer-row";
    const input = document.createElement("input"); input.type="number"; input.min="0.01"; input.step="any"; input.value=this._durations[fan.entity_id] || 30; input.setAttribute("aria-label", "Duration");
    input.oninput = () => this._durations[fan.entity_id] = input.value;
    const unit = document.createElement("select");
    [["minutes","Minutes"],["hours","Hours"]].forEach(([value,label]) => unit.add(new Option(label,value)));
    unit.value = this._units[fan.entity_id] || "minutes"; unit.onchange = () => this._units[fan.entity_id] = unit.value;
    const start = this._button(state.attributes.timer_remaining_seconds ? "Restart" : "Start", () => {
      const value = Number(input.value); if (!(value > 0)) return;
      this._call("rf_fan", "set_timer", fan.entity_id, {minutes: value * (unit.value === "hours" ? 60 : 1)});
    });
    start.dataset.role = "timer-start"; start.dataset.entity = fan.entity_id;
    row.append(input, unit, start);
    const remaining = document.createElement("div"); remaining.className = "remaining";
    remaining.dataset.role = "timer-remaining"; remaining.dataset.entity = fan.entity_id;
    remaining.append(document.createTextNode(`Remaining: ${state.attributes.timer_remaining || "0:00:00"} `));
    remaining.append(this._button("Cancel", () => this._call("rf_fan", "set_timer", fan.entity_id, {minutes: 0}), "link"));
    timer.append(timerLabel, row, remaining);
    wrap.append(title, controls, speeds);
    const modeSection = this._section("Modes", modes, fan); if (modeSection) wrap.append(modeSection);
    if (lightSection) wrap.append(lightSection);
    if (cycleSection) wrap.append(cycleSection);
    wrap.append(timer);
    const physicalSection = this._section("Physical RF timers", physical, fan); if (physicalSection) wrap.append(physicalSection);
    return wrap;
  }
  _render() {
    if (!this.shadowRoot || !this._hass) return;
    this.shadowRoot.innerHTML = `<style>
      :host{display:block;max-width:760px;margin:0 auto;color:var(--primary-text-color)}
      ha-card{display:block;padding:8px;background:transparent;box-shadow:none;border:none}
      .remote{padding:12px;margin:0 0 16px;background:var(--ha-card-background,var(--card-background-color));border:1px solid var(--divider-color);border-left:5px solid var(--primary-color);border-radius:14px;box-shadow:var(--ha-card-box-shadow,0 2px 8px rgba(0,0,0,.14))}
      h3{margin:-12px -12px 11px;padding:11px 12px;text-align:center;font-size:1.15rem;background:var(--secondary-background-color);border-radius:9px 9px 0 0;border-bottom:1px solid var(--divider-color)}
      .buttons{display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));gap:6px}.speeds{grid-template-columns:repeat(auto-fit,minmax(42px,1fr));margin-top:6px}
      button,select,input{box-sizing:border-box;min-height:36px;border:1px solid var(--divider-color);border-radius:8px;background:var(--secondary-background-color);color:var(--primary-text-color);padding:6px 9px;font:inherit}
      button{cursor:pointer}.danger{color:var(--error-color)}.label{font-weight:600;font-size:.9rem;margin:9px 0 5px}.section{margin-top:8px}
      details{border-top:1px solid var(--divider-color);padding-top:7px}summary{cursor:pointer;font-weight:600;font-size:.9rem}.timer-row{display:grid;grid-template-columns:minmax(70px,1fr) minmax(92px,1fr) auto;gap:6px}
      .remaining,.diagnostics{font-size:.85rem;color:var(--secondary-text-color);margin-top:5px}.link{min-height:28px;padding:2px 7px}
      .unavailable{padding:18px;text-align:center;color:var(--secondary-text-color)}
      @media(max-width:480px){ha-card{padding:6px}.remote{padding:10px;margin-bottom:14px}.remote h3{margin:-10px -10px 10px}.buttons{grid-template-columns:repeat(2,1fr)}.speeds{grid-template-columns:repeat(5,1fr)}.timer-row{grid-template-columns:1fr 1fr}.timer-row button{grid-column:1/-1}}
    </style>`;
    const card = document.createElement("ha-card");
    if (this._error) { card.innerHTML = `<div class="unavailable">${this._error}</div>`; this.shadowRoot.append(card); return; }
    const fans = this._selectedFans();
    if (!fans.length) card.innerHTML = `<div class="unavailable">This RF Fan device is unavailable or has been deleted.</div>`;
    else fans.forEach(({device, fan}) => card.append(this._fanCard(device, fan)));
    this.shadowRoot.append(card);
  }
}

class RfFanRemoteCardEditor extends HTMLElement {
  setConfig(config) { this._config = config || {}; this._render(); }
  set hass(hass) { this._hass = hass; if (!this._devices) this._load(); }
  async _load() { this._devices = await this._hass.callWS({type:"rf_fan/devices"}); this._render(); }
  _render() {
    if (!this._hass) return;
    this.innerHTML = `<div style="padding:12px"><label>RF Fan device<br><select style="width:100%;margin-top:6px;padding:8px"></select></label></div>`;
    const select = this.querySelector("select");
    select.add(new Option("All RF Fan devices", "all"));
    (this._devices || []).forEach(device => device.fans.forEach(fan => {
      const label = device.fans.length > 1 ? `${device.name} — ${fan.name}` : device.name;
      select.add(new Option(label, fan.fan_id));
    }));
    const legacyDevice = this._config?.device_id;
    const legacyFan = legacyDevice && legacyDevice !== "all"
      ? (this._devices || []).find(device => device.device_id === legacyDevice)?.fans?.[0]?.fan_id
      : null;
    select.value = this._config?.fan_id || legacyFan || "all";
    select.onchange = () => {
      const config = {...this._config};
      delete config.device_id;
      config.fan_id = select.value;
      this.dispatchEvent(new CustomEvent("config-changed", {detail:{config}, bubbles:true, composed:true}));
    };
  }
}

if (!customElements.get("rf-fan-remote-card")) customElements.define("rf-fan-remote-card", RfFanRemoteCard);
if (!customElements.get("rf-fan-remote-card-editor")) customElements.define("rf-fan-remote-card-editor", RfFanRemoteCardEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === "rf-fan-remote-card")) window.customCards.push({
  type:"rf-fan-remote-card", name:"RF Fan Remote", description:"Automatic remote for RF Fan Enhanced devices", preview:true
});
