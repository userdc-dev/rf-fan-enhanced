RF Fan Enhanced 2.5.1 is a stability update for 2.5.0.

- Serializes state-changing operations for every fan belonging to the same learned remote, so speed, power, timer and remote-card actions cannot interleave with Safe OFF.
- Makes software timer cancellation and reload handling race-safe. A timer missed while Home Assistant was offline is reported as missed and does not cause a surprise shutdown on startup.
- Keeps the custom timer input intact while Home Assistant state updates refresh only the live countdown and Start/Restart label.
- Enforces command ownership between primary and sub-fans, updates known optimistic states after remote-card commands, and lets the card editor select an individual sub-fan.
- Removes duplicate light controls and presents multi-click light actions as relative sequences rather than guaranteed colour presets.
- Skips identical generated-dashboard rewrites, protects externally changed dashboard files, and retains at most five generated-dashboard snapshots.

Existing RF packets, replay modes, entity unique IDs, learning, reassignment, Broadlink resources and user dashboards are unchanged. RF remains one-way, so state is optimistic unless the installation has separate feedback. This release was validated without transmitting to physical fans.
