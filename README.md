# RF Remote Enhanced

Learn and control RF ceiling fans and remote buttons from Home Assistant using an RF-capable Broadlink device.

## Install

[![Open RF Remote Enhanced in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=userdc-dev&repository=rf-fan-enhanced&category=integration)

Download in HACS, restart Home Assistant, then add your RF remote:

[![Add RF Remote Enhanced](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=rf_fan)

**Requirements:** Home Assistant 2026.9.2 or newer, HACS, and a configured RF-capable Broadlink RM Pro / RM4 Pro. You may try using ESP home or another RF remote however my testing was done using Broadlink RM 4 pro. Your transmitter must appear as a `radio_frequency` entity. RF commands need to be learned from your own remote. Try to keep your RF transmitter close to furthest away devices (by trying to place around center of place or any other ways).

My Use cases- Ceiling Fans, Black Goldair 132cm DC, Easyhome (Aldi) 132cm DC fan with LED Light. (both came with remotes and I used this project to replace them). Contact- divy@dcgogna.com or via Github messages for any extra information.

If HACS does not discover the repository automatically, add `https://github.com/userdc-dev/rf-fan-enhanced` under **HACS → Custom repositories**, category **Integration**. Availability as a custom repository does not mean approval in the default HACS catalogue.

## What is new in 2.5.0

- **Reusable RF Fan Remote card:** add it from the normal card picker, select a fan and save. The dedicated sidebar dashboard discovers configured fans automatically on desktop and in the Android Companion app.
- **Compact mobile remote:** clearly separated fan panels, responsive controls, visible physical RF timer buttons and no unnecessary Advanced section.
- **Safe OFF:** optionally sends Speed 1, waits about one second, then sends the existing power/off command. Overlapping shutdown sequences are prevented.
- **Custom software timer:** arbitrary minutes or hours, live backend countdown, cancel/restart and sensible restart restoration. Expiry uses Safe OFF when enabled.
- **Light-cycle presets:** for toggle-only lights, configurable Cool/Neutral/Warm presets replay the existing light command 1×/3×/5× by default. Start with the physical light off; RF state is still assumed.
- **Automatic cleanup:** removing an RF Fan device uses Home Assistant's config-entry APIs and never removes a shared Broadlink or user-created automation/dashboard.

Previous 2.3 features remain available:

- **Remote dashboard:** Configure → Create / refresh remote dashboard → Submit. Controls are generated from your own registered devices and added to the sidebar. No dashboard YAML editing is needed.
- **Alphabetical Configure menu:** Save changes stays at the bottom, with a review before saving.
- **Command manager:** inspect learned buttons, rename labels, test commands and copy/reassign signals without deleting the source.
- **Per-command replay:** Original Broadlink packet, Captured timings or Mercator filtering. Existing integration behavior remains available for compatibility. Test a mode and confirm the physical response before keeping it.
- **Batch relearning:** select several buttons; test, retry or skip each one. Optional selections start empty. Existing commands and playback selections are retained until you replace them and save.
- **Command library:** reuse learned commands between remotes on the same RF carrier frequency.
- **Backup / export / import:** private snapshots before options commits, checksummed command exports, validation and explicit overwrite protection.
- **State correction:** unknown/assumed states, configurable fan startup behavior, and correction actions that do not transmit.
- **Light toggle button:** a stateless single press that works independently of the optimistic light entity.
- **Fan controls and timers:** 1–8 speed levels, selectable power-button styles, optional DC buttons, native direction/oscillation where learned, and cancellable software timers alongside learned physical timers.
- **Diagnostics:** pulse counts, replay modes, fingerprints and transmission results without exposing replayable packets in diagnostics.

## Everyday use

Open **Settings → Devices & services → RF Remote Enhanced → Configure** next to your remote. Menu choices are alphabetical.

Open **RF Fan remotes** in the sidebar. It updates automatically when fans, names, speed counts or commands change. On another dashboard, choose **Add card → RF Fan Remote → Select Fan → Save**.

For command edits, choose **Remote command manager**. Testing sends RF immediately; saving a replay selection only changes the pending configuration. Finish with **Save changes**.

For several buttons, choose **Batch learn / relearn buttons**, select only the ones you want, and work through the queue. Retry or skip without erasing the previously saved signal.

## Replay modes

| Mode | Behavior |
|---|---|
| Original Broadlink packet | Replays the saved packet byte-for-byte through the selected Broadlink. Available when an original packet was captured. |
| Captured timings | Replays captured pulse timings with idle-edge trimming and no Mercator filter. |
| Mercator filtered | Sends a consensus frame with repeats; reports when no compatible frame is found. |
| Original integration behavior | Preserves the legacy processing selected by the entry's learning configuration. |

RF is one-way. A successful transmission does not prove that the fan responded. External remote use can make the displayed state inaccurate. Confirm the response yourself or use independent feedback.

## Actions

- `rf_fan.set_timer`: target a fan and set `minutes` to a delay, such as `120`; `0` cancels. Default restored-state startup behavior also restores the timer.
- `rf_fan.safe_off`: send Speed 1, wait about one second, then send the existing power/off command.
- `rf_fan.set_light_cycle`: starting with the physical light off, select `cool`, `neutral` or `warm` to replay the configured number of light-toggle presses.
- `rf_fan.set_assumed_state`: set fan `state` to `on`, `off` or `unknown`, with optional `percentage` and `direction` (`forward`, `reverse` or `unknown`). Does not transmit. Confirm direction before using an absolute direction action on a toggle-only remote.
- `rf_fan.set_light_assumed_state`: correct the displayed light `state` without transmitting.

Standard fan/light entities can be exposed by Matter Hub or other Home Assistant bridges. Individual bridge capabilities vary; this release does not claim end-to-end Google Home or Alexa certification.

## Upgrades and backups

The domain remains `rf_fan`; do not remove your configured devices to upgrade. Existing command data and entity identities are preserved. Back up Home Assistant before changing integration sources.

Private automatic snapshots are stored under `rf_fan_safety` in your Home Assistant configuration folder. Command exports contain replayable RF signals; keep them private. Diagnostics omit those signals. The generated dashboard stores entity controls only.

This integration and the original `rf_fan` integration share a domain, so install one implementation at a time.

## Validation

The release was checked against Home Assistant 2026.9.2, including regression checks using existing learned records and an isolated startup test of fans, lights, actions and dashboard creation. Hardware confirmation is remote-specific; alternate replay modes, new learning and physical timer expiry require testing with your own devices.

Repository workflows run HACS validation, Home Assistant hassfest and portable command-exchange tests.

## Credits and licence

Maintained by [@userdc-dev](https://github.com/userdc-dev), originally inspired and forked out of base project by [clevrdavid/rf_fan](https://github.com/clevrdavid/rf_fan). MIT licence; original attribution is retained in [LICENSE](LICENSE).
