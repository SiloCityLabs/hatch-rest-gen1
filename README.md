# Hatch Rest (1st gen) – Home Assistant

Custom Home Assistant integration for the **Bluetooth-only Hatch Rest** night light and sound machine (1st generation / FCC `2AFYZ-HBREST`).

**Maintained by [SiloCityLabs](https://github.com/SiloCityLabs/hatch-rest-gen1).**  
Forked from [jcgoette/hatch_rest_homeassistant](https://github.com/jcgoette/hatch_rest_homeassistant), with BLE protocol work based on [kjoconnor/pyhatchbabyrest](https://github.com/kjoconnor/pyhatchbabyrest).

> Not for Hatch Rest+ / Rest 2nd gen Wi‑Fi models (`2AFYZ-HBREST2`). Those use a different stack.

## Features

- **Local BLE** — no Hatch cloud or phone app required once set up
- **Light** — RGB + brightness (including gradient mode)
- **Switch** — master power
- **Media player** — sound track + volume
- **On-device programs (schedules)** — read / write / enable / clear slots 1–10
  - Calendar entity for enabled schedules
  - Sensor with full program attributes
  - Per-slot enable switches
  - Sync clock + refresh programs buttons
- Services: `refresh_programs`, `set_program`, `enable_program`, `clear_program`, `sync_clock`

## Requirements

- Home Assistant with Bluetooth (USB adapter and/or **ESPHome `bluetooth_proxy` with `active: true`**)
- Hatch Rest must advertise manufacturer ID **1076** (`0x0434`)
- Phone BLE often holds an exclusive connection — forget/disconnect the Rest in the Hatch app while pairing or controlling from HA

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories**
2. URL: `https://github.com/SiloCityLabs/hatch-rest-gen1`  
   Category: **Integration**
3. Download **Hatch Rest** → restart Home Assistant

Manual install: copy `custom_components/hatch_rest` into `/config/custom_components/`, then restart.

## Setup

1. **Settings → Devices & Services → Add Integration → Hatch Rest**
2. Pick the discovered device (or enter the Bluetooth address)
3. Prefer a nearby active BLE proxy for reliability

After setup you should see light, switch, media player, programs sensor/calendar, program enable switches, and sync/refresh buttons.

## Programs (schedules)

The Rest stores up to **10 on-device programs**. HA can replace the Hatch app for this.

| Goal | How |
|------|-----|
| See schedules | `sensor.*_programs` attributes, or the calendar entity |
| Enable / disable | Toggle `switch.*_program_N_enabled` (empty slot creates a default) |
| Edit / create | Service `hatch_rest.set_program` (merges by default; `replace: true` for a full rewrite) |
| Delete | `hatch_rest.clear_program` |
| Device clock | `button.*_sync_clock` or `hatch_rest.sync_clock` — needed so schedules fire on time |
| Re-read slots | `button.*_refresh_programs` or `hatch_rest.refresh_programs` |

### Example: set Nap Time

```yaml
action: hatch_rest.set_program
target:
  device_id: YOUR_HATCH_DEVICE_ID
data:
  index: 2
  name: Nap Time
  enabled: true
  time: "13:00:00"
  duration_seconds: 7230
  track: noise
  volume: 51
  days:
    - sunday
    - monday
    - tuesday
    - wednesday
    - thursday
    - friday
    - saturday
  color:
    r: 235
    g: 142
    b: 72
    a: 127
```

Sound `track` accepts a name (`noise`, `ocean`, `rain`, …) or the numeric id used by the device.

## Bluetooth behavior

- Connects on demand, queues commands, disconnects when idle
- Retries via `bleak_retry_connector`
- Program I/O uses TX writes + RX notifications (`OK` / payload)

## Development

```bash
git clone git@github.com:SiloCityLabs/hatch-rest-gen1.git
cd hatch-rest-gen1
```

Protocol notes from APK reverse engineering live in [`notes.md`](notes.md).

## Credits

- [Justin Goette](https://github.com/jcgoette) — original Home Assistant integration
- [kjoconnor/pyhatchbabyrest](https://github.com/kjoconnor/pyhatchbabyrest) — early BLE command reference
- SiloCityLabs — ongoing maintenance, on-device program support, HA 2026 updates

## License

MIT — see [`LICENSE`](LICENSE).
