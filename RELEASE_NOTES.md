RF Fan Enhanced 2.5.0 adds an automatically discovered, reusable visual remote card that works on desktop and in the Android Companion app. The compact layout separates each fan clearly, shows physical RF timer buttons directly and removes the unnecessary Advanced section.

Safe OFF sends the existing Speed 1 command, waits about one second, then sends the existing power/off command. Custom software timers now provide a live backend countdown and expire through Safe OFF when enabled. Toggle-only lights can use configurable Cool/Neutral/Warm press-count presets, defaulting to 1×/3×/5× from a physically off starting point.

Existing learned signals, replay modes and entity identities are preserved. No RF command is changed or relearned by this upgrade.

Use the two installation shortcuts in README: Open in HACS, then Add RF Fan Enhanced after downloading and restarting Home Assistant.

Software validation covered Home Assistant 2026.9.2 and a 400-pixel mobile layout. RF remains one-way; Safe OFF and light colours are assumed until independently observed. Google Home/Alexa behavior depends on your bridge. TCL IR controls are outside this integration.
