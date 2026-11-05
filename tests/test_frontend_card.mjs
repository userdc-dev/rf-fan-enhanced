import assert from "node:assert/strict";

globalThis.HTMLElement = class {};
globalThis.document = {createElement: (tag) => ({tag})};
const elements = new Map();
globalThis.customElements = {
  get: (name) => elements.get(name),
  define: (name, value) => elements.set(name, value),
};
globalThis.window = {customCards: []};

await import("../custom_components/rf_fan/frontend/rf-fan-remote-card.js");

const Card = customElements.get("rf-fan-remote-card");
assert.deepEqual(Card.getStubConfig(), {fan_id: ""});

const card = new Card();
card._devices = [
  {device_id: "device-a", fans: [{fan_id: "entry-a:primary", name: "Living Room"}]},
  {device_id: "device-b", fans: [{fan_id: "entry-b:primary", name: "Bedroom"}]},
];

card._config = {};
assert.equal(card._selectedFans().length, 0, "a normal new card must not select every fan");

card._config = {fan_id: "entry-a:primary"};
assert.deepEqual(card._selectedFans().map(({fan}) => fan.name), ["Living Room"]);

card._config = {fan_id: "missing:primary"};
assert.equal(card._selectedFans().length, 0, "deleted selections remain unavailable");

card._config = {device_id: "all"};
assert.equal(card._selectedFans().length, 2, "the generated sidebar keeps its all-fans view");

console.log("RF Fan Remote card selection tests passed");
