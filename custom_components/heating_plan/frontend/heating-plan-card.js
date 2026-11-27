/*
 * Heating Plan card for Home Assistant.
 *
 * type: custom:heating-plan-card
 * entity: climate.office_heating   # optional; without it the card shows all rooms
 *
 * A plain Web Component without a build step.
 */

const CARD_VERSION = "0.0.1";

const MIN_TEMP = 5;
const MAX_TEMP = 30;
const MAX_POINTS = 10;
const TIME_STEP = 15; // minutes
const TEMP_STEP = 0.5;
const DAY_NAMES = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"];

const TEXT = {
  loading: "Ładowanie…",
  noRooms: "Brak pokojów. Dodaj pokój w ustawieniach integracji Plan ogrzewania.",
  unknownEntity: "Nie znaleziono pokoju",
  manual: "Ręcznie",
  paused: "Wyłączone",
  until: "do",
  untilCancel: "do odwołania",
  knob: "z pokrętła",
  cancel: "Anuluj",
  next: "Dalej",
  today: "dziś",
  offline: "Głowica offline",
  syncPending: "Plan nie wgrany (czeka na głowicę)",
  syncError: "Błąd wgrania planu",
  duration: "Czas",
  hours: (h) => `${h} h`,
  untilNext: "do następnej zmiany",
  editPlan: "Edytuj plan",
  closeEditor: "Zamknij edycję",
  profile: "Profil",
  addPoint: "Dodaj punkt",
  remove: "Usuń",
  copyProfile: "Kopiuj profil",
  newProfile: "Nowy profil",
  deleteProfile: "Usuń profil",
  profileName: "Nazwa nowego profilu",
  week: "Tydzień",
  save: "Zapisz plan",
  saved: "Plan zapisany",
  saving: "Zapisywanie…",
  errEmpty: "Profil musi mieć co najmniej jeden punkt.",
  errTooMany: `Profil może mieć najwyżej ${MAX_POINTS} punktów.`,
  errDuplicate: "Dwa punkty mają tę samą godzinę.",
  errRange: `Temperatura musi być w zakresie ${MIN_TEMP}–${MAX_TEMP} °C.`,
  errTime: "Godzina musi być w zakresie 00:00–23:59.",
  errName: "Podaj nazwę profilu.",
  errNameExists: "Profil o tej nazwie już istnieje.",
  errProfileUsed: "Tego profilu używa tydzień. Najpierw przypisz dniom inny profil.",
  errLastProfile: "Plan musi mieć co najmniej jeden profil.",
  errWeek: "Każdy dzień tygodnia musi mieć istniejący profil.",
};

const pad = (n) => String(n).padStart(2, "0");
const minuteToTime = (m) => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
const timeToMinute = (t) => {
  const match = /^(\d{1,2}):(\d{2})$/.exec(t || "");
  if (!match) return NaN;
  const h = Number(match[1]);
  const m = Number(match[2]);
  if (h > 23 || m > 59) return NaN;
  return h * 60 + m;
};
const fmtTemp = (t) =>
  t === null || t === undefined ? "–" : `${Number(t).toLocaleString("pl-PL", { maximumFractionDigits: 1 })}°`;
const fmtClock = (iso) => {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const fmtWhen = (iso) => {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return fmtClock(iso);
  const days = ["nd", "pn", "wt", "śr", "cz", "pt", "so"];
  return `${days[d.getDay()]} ${fmtClock(iso)}`;
};

/** Parse "HH:MM/temp HH:MM/temp" into [{minute, temp}]. */
function parseDay(text) {
  return (text || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((item) => {
      const [time, temp] = item.split("/");
      return { minute: timeToMinute(time), temp: Number(String(temp).replace(",", ".")) };
    });
}

function formatDay(points) {
  return [...points]
    .sort((a, b) => a.minute - b.minute)
    .map((p) => `${minuteToTime(p.minute)}/${Number(p.temp.toFixed(1))}`)
    .join(" ");
}

/** The same checks as model.py. Returns an error text or null. */
export function validateDay(points) {
  if (points.length < 1) return TEXT.errEmpty;
  if (points.length > MAX_POINTS) return TEXT.errTooMany;
  const minutes = new Set();
  for (const p of points) {
    if (!Number.isInteger(p.minute) || p.minute < 0 || p.minute > 1439) return TEXT.errTime;
    if (!(p.temp >= MIN_TEMP && p.temp <= MAX_TEMP)) return TEXT.errRange;
    if (minutes.has(p.minute)) return TEXT.errDuplicate;
    minutes.add(p.minute);
  }
  return null;
}

export function validatePlan(profiles, week) {
  const names = Object.keys(profiles);
  if (names.length < 1) return TEXT.errLastProfile;
  for (const name of names) {
    if (!name.trim()) return TEXT.errName;
    const error = validateDay(profiles[name]);
    if (error) return `${name}: ${error}`;
  }
  if (week.length !== 7 || week.some((n) => !(n in profiles))) return TEXT.errWeek;
  return null;
}

/** Colour for a temperature: blue (16 °C and less) to orange-red (23 °C and more). */
function tempColor(temp) {
  if (temp === null || temp === undefined) return "var(--disabled-color, #bdbdbd)";
  const warm = Math.round(Math.min(Math.max((temp - 16) / 7, 0), 1) * 100);
  return `color-mix(in srgb, #ff5722 ${warm}%, #42a5f5)`;
}

/** Blocks [{from, to, temp}] for a day, from the start temperature and the points. */
function dayBlocks(startTemp, points) {
  const sorted = [...points].sort((a, b) => a.minute - b.minute);
  const blocks = [];
  let from = 0;
  let temp = startTemp;
  for (const p of sorted) {
    if (p.minute > from) blocks.push({ from, to: p.minute, temp });
    from = p.minute;
    temp = p.temp;
  }
  blocks.push({ from, to: 1440, temp });
  return blocks;
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .room + .room { border-top: 1px solid var(--divider-color); margin-top: 16px; padding-top: 16px; }
  .head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .name { font-size: 1.2em; font-weight: 500; }
  .temps { font-size: 1.2em; white-space: nowrap; }
  .temps .target { font-weight: 600; }
  .line { margin-top: 6px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; color: var(--secondary-text-color); }
  .override { color: var(--primary-text-color); background: var(--secondary-background-color); border-radius: 8px; padding: 6px 10px; justify-content: space-between; }
  .warn { color: var(--warning-color, #f57c00); }
  .error { color: var(--error-color, #db4437); }
  .ok { color: var(--success-color, #43a047); }
  button { font: inherit; cursor: pointer; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); padding: 6px 12px; min-height: 36px; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  button.round { width: 40px; padding: 0; font-size: 1.3em; }
  button:disabled { opacity: 0.5; cursor: default; }
  select, input { font: inherit; padding: 6px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); min-height: 36px; box-sizing: border-box; }
  .bar { position: relative; display: flex; height: 22px; border-radius: 6px; overflow: hidden; margin-top: 10px; }
  .bar div { height: 100%; }
  .now { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--primary-text-color); }
  .axis { position: relative; height: 16px; font-size: 0.75em; color: var(--secondary-text-color); }
  .axis span { position: absolute; transform: translateX(-50%); }
  .axis span:first-child { transform: none; }
  .axis span:last-child { transform: translateX(-100%); }
  .quick { margin-top: 10px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .quick .value { min-width: 56px; text-align: center; font-size: 1.2em; font-weight: 600; }
  .editor { margin-top: 12px; border-top: 1px dashed var(--divider-color); padding-top: 12px; display: grid; gap: 10px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .point { display: grid; grid-template-columns: minmax(96px, 1fr) auto minmax(56px, auto) auto auto; gap: 6px; align-items: center; }
  .point .temp { text-align: center; }
  .week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }
  .week label { display: grid; gap: 2px; font-size: 0.85em; text-align: center; }
  .week select { padding: 4px 2px; width: 100%; }
  .msg { font-size: 0.9em; }
  @media (max-width: 420px) {
    ha-card { padding: 12px; }
    .point { grid-template-columns: 1fr auto auto auto auto; }
    .week { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  }
`;

class HeatingPlanCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._rooms = null;
    this._error = null;
    this._ui = {}; // per room: {editing, profile, draft, duration, pending, message}
    this._watch = "";
    this._timers = {};
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    this._render();
  }

  static getStubConfig() {
    return {};
  }

  getCardSize() {
    return 4 * Math.max(1, (this._rooms || []).length);
  }

  set hass(hass) {
    this._hass = hass;
    // Reload the rooms when a room or its thermostat changes.
    const ids = (this._rooms || []).map((r) => r.entity_id).filter(Boolean);
    const watch = ids.map((id) => hass.states[id]?.last_updated).join("|");
    if (this._rooms === null || watch !== this._watch) {
      this._watch = watch;
      this._load();
    }
  }

  connectedCallback() {
    this._tick = setInterval(() => this._render(), 60000);
  }

  disconnectedCallback() {
    clearInterval(this._tick);
  }

  async _load() {
    if (!this._hass || this._loading) {
      this._reload = true;
      return;
    }
    this._loading = true;
    try {
      do {
        this._reload = false;
        this._rooms = await this._hass.connection.sendMessagePromise({ type: "heating_plan/rooms" });
        this._error = null;
      } while (this._reload);
      const ids = this._rooms.map((r) => r.entity_id).filter(Boolean);
      this._watch = ids.map((id) => this._hass.states[id]?.last_updated).join("|");
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
    }
    this._render();
  }

  _visibleRooms() {
    if (!this._rooms) return [];
    if (!this._config?.entity) return this._rooms;
    return this._rooms.filter((r) => r.entity_id === this._config.entity);
  }

  _state(room) {
    if (!this._ui[room.entity_id]) {
      this._ui[room.entity_id] = { editing: false, duration: "timed", pending: null, message: null };
    }
    return this._ui[room.entity_id];
  }

  // --- actions -------------------------------------------------------------

  _callService(service, data) {
    return this._hass.callService("heating_plan", service, data).catch((err) => {
      this._error = err?.message || String(err);
      this._render();
    });
  }

  _cancel(room) {
    this._callService("cancel_override", { entity_id: room.entity_id });
  }

  _step(room, delta) {
    const ui = this._state(room);
    const base = ui.pending ?? room.target ?? room.min_temp;
    const value = Math.min(Math.max(Math.round((base + delta) / TEMP_STEP) * TEMP_STEP, room.min_temp), MAX_TEMP);
    ui.pending = value;
    this._render();
    clearTimeout(this._timers[room.entity_id]);
    this._timers[room.entity_id] = setTimeout(() => this._applyOverride(room), 1200);
  }

  async _applyOverride(room) {
    const ui = this._state(room);
    if (ui.pending === null) return;
    const data = { entity_id: room.entity_id, temperature: ui.pending, mode: ui.duration };
    ui.pending = null;
    await this._callService("set_override", data);
  }

  _setDuration(room, value) {
    const ui = this._state(room);
    ui.duration = value;
    if (room.override && room.override.temp !== null) {
      this._callService("set_override", { entity_id: room.entity_id, temperature: room.override.temp, mode: value });
    }
    this._render();
  }

  // --- editor --------------------------------------------------------------

  _openEditor(room) {
    const ui = this._state(room);
    ui.editing = true;
    ui.message = null;
    ui.draft = {
      profiles: Object.fromEntries(
        Object.entries(room.plan.profiles).map(([name, text]) => [name, parseDay(text)])
      ),
      week: [...room.plan.week],
    };
    ui.profile = room.today_profile in ui.draft.profiles ? room.today_profile : Object.keys(ui.draft.profiles)[0];
    this._render();
  }

  _closeEditor(room) {
    this._state(room).editing = false;
    this._render();
  }

  _points(room) {
    const ui = this._state(room);
    return ui.draft.profiles[ui.profile];
  }

  _editPoint(room, index, field, value) {
    const points = this._points(room);
    if (field === "time") {
      const minute = timeToMinute(value);
      points[index].minute = minute;
      points.sort((a, b) => a.minute - b.minute);
    } else {
      points[index].temp = Math.min(Math.max(points[index].temp + value, MIN_TEMP), MAX_TEMP);
    }
    this._state(room).message = null;
    this._render();
  }

  _addPoint(room) {
    const points = this._points(room);
    if (points.length >= MAX_POINTS) {
      this._state(room).message = { error: true, text: TEXT.errTooMany };
      this._render();
      return;
    }
    const used = new Set(points.map((p) => p.minute));
    const last = points.length ? Math.max(...points.map((p) => p.minute)) : 6 * 60 - 60;
    let minute = (last + 60) % 1440;
    while (used.has(minute)) minute = (minute + TIME_STEP) % 1440;
    const temp = points.length ? points[points.length - 1].temp : 20;
    points.push({ minute, temp });
    points.sort((a, b) => a.minute - b.minute);
    this._render();
  }

  _removePoint(room, index) {
    this._points(room).splice(index, 1);
    this._render();
  }

  _newProfile(room, copy) {
    const ui = this._state(room);
    const name = (window.prompt(TEXT.profileName, copy ? `${ui.profile} 2` : "") || "").trim();
    if (!name) return;
    if (name in ui.draft.profiles) {
      ui.message = { error: true, text: TEXT.errNameExists };
    } else {
      ui.draft.profiles[name] = copy
        ? this._points(room).map((p) => ({ ...p }))
        : [{ minute: 6 * 60, temp: 21 }, { minute: 22 * 60, temp: 17 }];
      ui.profile = name;
      ui.message = null;
    }
    this._render();
  }

  _deleteProfile(room) {
    const ui = this._state(room);
    if (Object.keys(ui.draft.profiles).length <= 1) {
      ui.message = { error: true, text: TEXT.errLastProfile };
    } else if (ui.draft.week.includes(ui.profile)) {
      ui.message = { error: true, text: TEXT.errProfileUsed };
    } else {
      delete ui.draft.profiles[ui.profile];
      ui.profile = Object.keys(ui.draft.profiles)[0];
      ui.message = null;
    }
    this._render();
  }

  async _save(room) {
    const ui = this._state(room);
    const error = validatePlan(ui.draft.profiles, ui.draft.week);
    if (error) {
      ui.message = { error: true, text: error };
      this._render();
      return;
    }
    ui.message = { error: false, text: TEXT.saving };
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: "heating_plan/set_plan",
        entity_id: room.entity_id,
        profiles: Object.fromEntries(
          Object.entries(ui.draft.profiles).map(([name, points]) => [name, formatDay(points)])
        ),
        week: ui.draft.week,
      });
      ui.message = { error: false, text: TEXT.saved };
      await this._load();
    } catch (err) {
      ui.message = { error: true, text: err?.message || String(err) };
    }
    this._render();
  }

  // --- render --------------------------------------------------------------

  _render() {
    if (!this.shadowRoot) return;
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = STYLE;
    root.appendChild(style);
    const card = document.createElement("ha-card");
    if (this._config?.title) card.setAttribute("header", this._config.title);
    root.appendChild(card);

    if (this._error && !this._rooms) {
      card.appendChild(el("div", { class: "error" }, this._error));
      return;
    }
    if (!this._rooms) {
      card.appendChild(el("div", {}, TEXT.loading));
      return;
    }
    const rooms = this._visibleRooms();
    if (!rooms.length) {
      card.appendChild(el("div", {}, this._config?.entity ? `${TEXT.unknownEntity}: ${this._config.entity}` : TEXT.noRooms));
      return;
    }
    if (this._error) card.appendChild(el("div", { class: "error msg" }, this._error));
    for (const room of rooms) card.appendChild(this._renderRoom(room));
  }

  _renderRoom(room) {
    const ui = this._state(room);
    const box = el("div", { class: "room" });

    const target = ui.pending ?? room.target;
    box.appendChild(
      el("div", { class: "head" }, [
        el("span", { class: "name" }, room.name),
        el("span", { class: "temps" }, [
          el("span", {}, `${fmtTemp(room.current_temp)} → `),
          el("span", { class: "target" }, room.target === null && ui.pending === null ? TEXT.paused : fmtTemp(target)),
        ]),
      ])
    );

    if (!room.available) box.appendChild(el("div", { class: "line warn" }, TEXT.offline));
    if (room.sync_state === "pending") box.appendChild(el("div", { class: "line warn" }, TEXT.syncPending));
    if (room.sync_state === "error")
      box.appendChild(el("div", { class: "line error", title: room.last_error || "" }, TEXT.syncError));

    if (room.override) {
      const o = room.override;
      const what = o.temp === null ? TEXT.paused : `${TEXT.manual} ${fmtTemp(o.temp)}`;
      const when = o.until ? `${TEXT.until} ${fmtWhen(o.until)}` : TEXT.untilCancel;
      const source = o.source === "knob" ? ` (${TEXT.knob})` : "";
      box.appendChild(
        el("div", { class: "line override" }, [
          el("span", {}, `${what} ${when}${source}`),
          button(TEXT.cancel, () => this._cancel(room), "primary"),
        ])
      );
    }
    if (room.next_change) {
      const nc = room.next_change;
      const temp = nc.temp === null ? TEXT.paused : fmtTemp(nc.temp);
      box.appendChild(el("div", { class: "line" }, `${TEXT.next}: ${fmtWhen(nc.at)} → ${temp}`));
    }

    box.appendChild(this._renderBar(room.day_start_temp, room.today_points, true));

    // Quick override
    const durations = [
      ["timed", TEXT.hours(room.override_hours)],
      ["until_next", TEXT.untilNext],
      ["until_cancel", TEXT.untilCancel],
    ];
    const select = el(
      "select",
      { "aria-label": TEXT.duration },
      durations.map(([value, label]) => {
        const option = el("option", { value }, label);
        if (value === ui.duration) option.selected = true;
        return option;
      })
    );
    select.addEventListener("change", (e) => this._setDuration(room, e.target.value));
    box.appendChild(
      el("div", { class: "quick" }, [
        button("−", () => this._step(room, -TEMP_STEP), "round", "−0,5 °C"),
        el("span", { class: "value" }, fmtTemp(target ?? room.min_temp)),
        button("+", () => this._step(room, TEMP_STEP), "round", "+0,5 °C"),
        select,
        button(ui.editing ? TEXT.closeEditor : TEXT.editPlan, () =>
          ui.editing ? this._closeEditor(room) : this._openEditor(room)
        ),
      ])
    );

    if (ui.editing) box.appendChild(this._renderEditor(room));
    return box;
  }

  _renderBar(startTemp, points, showNow) {
    const wrap = el("div", {});
    const bar = el("div", { class: "bar" });
    const valid = points.filter((p) => Number.isInteger(p.minute) && !Number.isNaN(p.temp));
    for (const b of dayBlocks(startTemp, valid)) {
      const block = el("div", { title: `${minuteToTime(b.from)}–${minuteToTime(b.to % 1440)} ${fmtTemp(b.temp)}` });
      block.style.width = `${((b.to - b.from) / 1440) * 100}%`;
      block.style.background = tempColor(b.temp);
      bar.appendChild(block);
    }
    if (showNow) {
      const now = new Date();
      const marker = el("div", { class: "now" });
      marker.style.left = `${((now.getHours() * 60 + now.getMinutes()) / 1440) * 100}%`;
      bar.appendChild(marker);
    }
    wrap.appendChild(bar);
    const axis = el("div", { class: "axis" });
    for (const h of [0, 6, 12, 18, 24]) {
      const label = el("span", {}, String(h));
      label.style.left = `${(h / 24) * 100}%`;
      axis.appendChild(label);
    }
    wrap.appendChild(axis);
    return wrap;
  }

  _renderEditor(room) {
    const ui = this._state(room);
    const draft = ui.draft;
    const box = el("div", { class: "editor" });

    const profileSelect = el(
      "select",
      { "aria-label": TEXT.profile },
      Object.keys(draft.profiles).map((name) => {
        const option = el("option", { value: name }, name);
        if (name === ui.profile) option.selected = true;
        return option;
      })
    );
    profileSelect.addEventListener("change", (e) => {
      ui.profile = e.target.value;
      ui.message = null;
      this._render();
    });
    box.appendChild(
      el("div", { class: "row" }, [
        el("span", {}, `${TEXT.profile}:`),
        profileSelect,
        button(TEXT.copyProfile, () => this._newProfile(room, true)),
        button(TEXT.newProfile, () => this._newProfile(room, false)),
        button(TEXT.deleteProfile, () => this._deleteProfile(room)),
      ])
    );

    const points = this._points(room);
    const prevIndex = (draft.week.indexOf(ui.profile) + 6) % 7;
    const prevPoints = draft.profiles[draft.week[prevIndex]] || points;
    const startTemp = prevPoints.length ? [...prevPoints].sort((a, b) => a.minute - b.minute).at(-1).temp : null;
    box.appendChild(this._renderBar(startTemp, points, false));

    points.forEach((p, index) => {
      // A list of times (24 h, every 15 minutes) looks the same in every browser language.
      const slots = [];
      for (let m = 0; m < 1440; m += TIME_STEP) slots.push(m);
      if (Number.isInteger(p.minute) && !slots.includes(p.minute)) {
        slots.push(p.minute);
        slots.sort((a, b) => a - b);
      }
      const time = el(
        "select",
        { "aria-label": "Godzina" },
        slots.map((m) => {
          const option = el("option", { value: minuteToTime(m) }, minuteToTime(m));
          if (m === p.minute) option.selected = true;
          return option;
        })
      );
      time.addEventListener("change", (e) => this._editPoint(room, index, "time", e.target.value));
      box.appendChild(
        el("div", { class: "point" }, [
          time,
          button("−", () => this._editPoint(room, index, "temp", -TEMP_STEP), "round", "−0,5 °C"),
          el("span", { class: "temp" }, fmtTemp(p.temp)),
          button("+", () => this._editPoint(room, index, "temp", TEMP_STEP), "round", "+0,5 °C"),
          button("✕", () => this._removePoint(room, index), "round", TEXT.remove),
        ])
      );
    });
    const add = button(TEXT.addPoint, () => this._addPoint(room));
    if (points.length >= MAX_POINTS) add.disabled = true;
    box.appendChild(el("div", { class: "row" }, [add]));

    const week = el("div", { class: "week" });
    DAY_NAMES.forEach((day, index) => {
      const select = el(
        "select",
        { "aria-label": day },
        Object.keys(draft.profiles).map((name) => {
          const option = el("option", { value: name }, name);
          if (name === draft.week[index]) option.selected = true;
          return option;
        })
      );
      select.addEventListener("change", (e) => {
        draft.week[index] = e.target.value;
        ui.message = null;
        this._render();
      });
      week.appendChild(el("label", {}, [el("span", {}, day), select]));
    });
    box.appendChild(el("div", {}, [el("div", { class: "msg" }, `${TEXT.week}:`), week]));

    const error = validatePlan(draft.profiles, draft.week);
    const message = ui.message || (error ? { error: true, text: error } : null);
    const row = el("div", { class: "row" }, [button(TEXT.save, () => this._save(room), "primary")]);
    if (message) row.appendChild(el("span", { class: `msg ${message.error ? "error" : "ok"}` }, message.text));
    box.appendChild(row);
    return box;
  }
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function button(label, onClick, cls = "", title = "") {
  const node = el("button", { type: "button" }, label);
  if (cls) node.className = cls;
  if (title) {
    node.title = title;
    node.setAttribute("aria-label", title);
  }
  node.addEventListener("click", onClick);
  return node;
}

if (!customElements.get("heating-plan-card")) {
  customElements.define("heating-plan-card", HeatingPlanCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "heating-plan-card",
    name: "Heating Plan",
    description: "Plan ogrzewania: temperatura, nadpisania i edycja planu.",
  });
  console.info(`%c HEATING-PLAN-CARD %c ${CARD_VERSION} `, "background:#e65100;color:#fff", "");
}
