# ChorePilot: controlling a real appliance (dishwasher, washer, dryer)

> The program-appliance bridge from Dishsoaker3000. Other device types (lights, thermostats,
> sprinklers, coffee makers, ovens, fridges, vacuums, locks) are in [DEVICES.md](DEVICES.md).

The joke is running the model *on* the dishwasher. The useful version runs the model somewhere
sensible on your LAN and lets it *drive* the dishwasher through tool calls:

```
 "hey, wash my dishes"
        |
        v
 LLM on any LAN box  ---- OpenAI-style tool call ---->  chorepilot bridge  ----> backend ----> appliance
 (chorepilot serve,       start_program{"program":       schema check            simulated
  llama-server --jinja)    "auto"}                       interlocks              ha   (HA REST)
                                                         confirmation            mqtt (ESPHome)
        ^                                                rate limit              homeconnect (cloud API)
        |                                                dry-run
        +------------------- tool result: {"ok": true, "state": "running", "remaining": "2 h 29 min"}
```

The model never touches the appliance. It can only ask for six tools:
`get_status`, `start_program(program, options)`, `pause`, `resume`, `cancel` and
`notify_when_done`, plus `start_when_done(wait_for, then_start, program, ...)` when more than one
appliance is configured. The bridge decides whether each request goes through.

**Several appliances.** `~/.config/chorepilot/control.json` can list any number of appliances,
each with a nickname, a model and, optionally, its own backend, haId, aliases and
`program_keys` fixes. Every tool then takes `appliance`, and options are flat arguments
(`temperature`, `spin`, `dryness`, `finish_in_minutes`, `delay_minutes`, ...), validated
against that model's own lists in `chorepilot/machines.py`. A config without `appliances` is
the original single dishwasher. A worked example for three Bosch machines is in
[SETUP-bosch-home-connect.md](SETUP-bosch-home-connect.md).

Research date: 2026-10-20. Sources are linked inline. **[unverified]** marks claims I could
not confirm from a primary source.

---

## 1. Which path works for your brand

| Brand | Official path | Can it *start* a program remotely? | Local/community path | ChorePilot backend |
|---|---|---|---|---|
| **Bosch, Siemens, Neff, Gaggenau, Thermador** (BSH) | [Home Connect API](https://api-docs.home-connect.com/) (cloud, OAuth2 incl. Device Flow) | **Yes**, if *Remote Start* is enabled on the machine ([best practices](https://api-docs.home-connect.com/general/#best-practices)) | HA [Home Connect integration](https://www.home-assistant.io/integrations/home_connect/); local Wi-Fi-module protocol via [hcpy](https://github.com/osresearch/hcpy) / [openHAB Home Connect Direct](https://www.openhab.org/addons/bindings/homeconnectdirect/); internal D-Bus via [hn/bsh-home-appliances](https://github.com/hn/bsh-home-appliances) (monitoring) | `homeconnect`, `ha`, `mqtt` |
| **Samsung** | [SmartThings API](https://developer.smartthings.com/docs/getting-started/quickstart) | Partly: HA exposes `dishwasherOperatingState` as a select, and "some capabilities require remote control to be enabled on the device" ([HA SmartThings docs](https://github.com/home-assistant/home-assistant.io/blob/current/source/_integrations/smartthings.markdown)). Per-model values **[unverified]** | Wi-Fi module swap to ESPHome ([HA thread](https://community.home-assistant.io/t/convert-samsung-smartthings-devices-to-real-smart-esphome-devices-new-project/1016898)) | `ha`, `mqtt` |
| **LG** | [ThinQ Connect API](https://thinq.developer.lge.com/en/cloud/docs/thinq-connect) (personal access token) | **Yes** with remote start: HA's dishwasher "Operation" entity has Start / Pause ([HA LG ThinQ](https://www.home-assistant.io/integrations/lg_thinq/)) | LAN cloud emulators [rethink](https://github.com/anszom/rethink), [ponder](https://github.com/arrudagates/ponder) | `ha` |
| **Miele** | Miele 3rd-party API | **Yes** with mobile start: HA (since 2025.5) has start/pause/stop buttons and `miele.set_program`. "Most appliances require you to manually activate mobile start or remote control mode." ([HA Miele](https://www.home-assistant.io/integrations/miele/)) | none needed | `ha` |
| **Matter dishwashers** | [Matter 1.2](https://csa-iot.org/newsroom/matter-1-2-arrives-with-nine-new-device-types-improvements-across-the-board/) (Oct 2023) defines a Dishwasher device type | **Only if the device implements Start.** Operational State is mandatory, but its Start command is *optional*, so a certified dishwasher can be monitor-only ([device type](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/device_types/Dishwasher.xml), [cluster](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.4/clusters/OperationalState.xml)). HA creates Start/Pause/Stop buttons only for commands the device accepts ([matter/button.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/button.py)) | BSH's first Matter appliances are fridges, not dishwashers ([BSH](https://www.home-connect.com/global/help-support/matter_1)); I found no primary evidence of Matter dishwashers shipping widely **[unverified]** | `ha` |
| **Anything else** | none | no | Non-invasive monitoring only (section 4) | `ha` / `mqtt` (status + notify) |

**The rule every vendor shares:** a human has to arm remote start *at the machine*. BSH:
`RemoteControlStartAllowed` "must be set on the appliance manually by the user"
([Home Connect best practices](https://api-docs.home-connect.com/general/#best-practices)).
On a Bosch SBI8ECS01A, "If you open the appliance door, the function is automatically
deactivated" ([manual §15.4](https://media3.bosch-home.com/Documents/9001967977_A.pdf)). That
interlock is the main reason this is safe: nobody starts an empty or open machine from the
sofa. **ChorePilot never tries to bypass it.** When status reports remote start off, the bridge
refuses and the model tells you to press the button.

---

## 2. The backends

### 2.1 `simulated` (default)
A dishwasher stored in `~/.cache/chorepilot/sim-dishwasher.json`, with time compressed
(`CHOREPILOT_SIM_SPEED`, default 600x, so a 2.5 h program takes 15 s). It has realistic states,
door, remote-start flag and per-program durations. Everything in this repo is tested against it.

### 2.2 `ha`: Home Assistant REST (recommended for real appliances)
If HA already integrates your appliance, this is the shortest path, and it covers every brand
in the table.

- Reads: `GET /api/states/<entity_id>`. Writes: `POST /api/services/<domain>/<service>`, sent
  with `Authorization: Bearer $HA_TOKEN`
  ([HA REST API](https://developers.home-assistant.io/docs/api/rest/)). Use service calls
  only: posting to `/api/states` "will not communicate with the actual device".
- **Home Connect defaults** follow the HA integration:
  - Start: the *Active program* select ("selecting an option will start the program"), as
    `select.select_option` with e.g. `dishcare_dishwasher_program_eco_50`.
  - Pause, resume and stop: the *Pause / Resume / Stop program* buttons.
  - Interlock: the *Remote start* binary sensor.
  - Source: [docs](https://www.home-assistant.io/integrations/home_connect/),
    [strings.json](https://github.com/home-assistant/core/blob/dev/homeassistant/components/home_connect/strings.json).
- **Entity IDs** depend on your device's name in HA. Override any of them in
  `~/.config/chorepilot/control.json`:

```json
{
  "backend": "ha",
  "ha": {
    "device": "kitchen_dishwasher",
    "entities": {"finish_time": "sensor.kitchen_dishwasher_program_finish_time"},
    "programs": {"eco": "dishcare_dishwasher_program_eco_50"}
  }
}
```

For SmartThings, LG, Miele or Matter devices, map `active_program`, `pause`, `resume`, `stop`,
`operation_state`, `door` and `remote_start` to the entities that integration created
(**Developer tools > States** lists them). `HA_URL` defaults to
`http://homeassistant.local:8123`.

### 2.3 `homeconnect`: the official BSH cloud API
Bosch, Siemens, Neff, Gaggenau and Thermador, with no Home Assistant needed.

1. Register an application at developer.home-connect.com and choose the **Device Flow**
   ("The Home Connect API supports the OAuth2 Device Flow",
   [authorization](https://api-docs.home-connect.com/authorization/)).
2. Log in:
   ```bash
   export HOME_CONNECT_CLIENT_ID=...
   chorepilot control login --backend homeconnect
   ```
   Open the URL it prints, enter the code, and approve. The token is saved to
   `~/.config/chorepilot/homeconnect-token.json` with mode 0600, never inside the repo.
   Alternatively, put an access token in `HOME_CONNECT_TOKEN`. Access tokens last 24 h and
   are refreshed automatically.
3. `chorepilot control status --backend homeconnect`

The calls it makes (from the [OpenAPI spec](https://apiclient.home-connect.com/hcsdk-production.yaml)
and [programs-and-options](https://api-docs.home-connect.com/programs-and-options/)):

| Tool | Request |
|---|---|
| get_status | `GET /api/homeappliances` (finds the dishwasher), `GET …/{haId}/status`, `GET …/programs/active` (RemainingProgramTime) |
| start_program | `PUT …/{haId}/programs/active` `{"data":{"key":"Dishcare.Dishwasher.Program.Eco50","options":[{"key":"BSH.Common.Option.StartInRelative","value":1800}]}}` |
| pause / resume | `PUT …/commands/BSH.Common.Command.PauseProgram` (or `ResumeProgram`) with `{"data":{"key":…,"value":true}}`. Whether pause is supported depends on the model **[unverified per model]** |
| cancel | `DELETE …/{haId}/programs/active` |

- Content type: `application/vnd.bsh.sdk.v1+json`.
- Program map: auto→`Auto2`, eco→`Eco50`, quick→`Quick45`, intensive→`Intensiv70`,
  normal→`Normal65`, glass→`Glas40`, night→`NightWash`, prerinse→`PreRinse`,
  machine_care→`MachineCare`. Not every model has every program; the API answers 409 for the
  ones yours lacks.
- To test without hardware, set `HOME_CONNECT_URL=https://simulator.home-connect.com`
  ([general](https://api-docs.home-connect.com/general/)).

**Rate limits** ([rate limiting](https://api-docs.home-connect.com/general/#rate-limiting)):

- 1000 requests per client and user per day;
- 50 per minute;
- 5 program starts per minute;
- 10 consecutive errors trigger a 10-minute block.

ChorePilot's own limiter is stricter: 6 writes per 10 min and 60 s between starts. Status
polling for `notify_when_done` runs every 30 s, about 300 requests for a 2.5 h program.

### 2.4 `mqtt`: ESPHome devices, including a BSH internal-bus adapter
This backend speaks MQTT 3.1.1 directly (a stdlib client, so it runs on a Pi Zero). It uses
ESPHome's default topics: state on `<prefix>/<component>/<name>/state`, commands on
`…/command`, and availability (`online`/`offline` last will) on `<prefix>/status`
([ESPHome MQTT](https://esphome.io/components/mqtt/)). Set `MQTT_HOST`, plus `MQTT_USER` and
`MQTT_PASSWORD` if needed. Topics and payloads are all configurable:

```json
{
  "backend": "mqtt",
  "mqtt": {
    "prefix": "dishwasher",
    "state_topics": {"remaining_s": "dishwasher/sensor/remaining_time/state"},
    "remaining_unit": "min",
    "commands": {"start": [["dishwasher/select/program/command", "{program}"],
                           ["dishwasher/button/start/command", "PRESS"]]},
    "programs": {"eco": "Eco 50"}
  }
}
```

**BSH D-Bus: what the ESP32 route really gives you**
([hn/bsh-home-appliances](https://github.com/hn/bsh-home-appliances)):

- **The bus.** BSH's own 3-wire bus (GND, VBUS, DATA) between the boards inside the
  appliance. VBUS is "9V for washing machines, **13.5V for dishwashers**". DATA is 5 V TTL
  UART, 8N1 at 9600 baud.
- **Frames.** `LL DS MM… CRC16-XMODEM`, and every frame is ACKed by its receiver.
- **Dishwasher messages.** The decoded messages include program select (`17.10-10`),
  option bits (`17.10-00`) and remaining minutes (`25.20-08`)
  ([DISHWASHER.md](https://github.com/hn/bsh-home-appliances/blob/master/DISHWASHER.md)).
  Contributed configs cover SBV43M10EU, SHXM78W56N, SN66P080EU and SX66T096EU.
- **Read-only in ESPHome.** The ESPHome component `bshdbus` only listens: it provides
  sensors and binary/text sensors, but has *no send action*. Sending exists only in the
  separate `open-dbus2` stack, whose authors say "this can not be guaranteed". **I found no
  one starting a dishwasher by frame injection [unverified].** Treat D-Bus as a very good
  *status* source (state, program, remaining time, door), and start the machine through Home
  Connect.
- **Hardware.** You need an ESP32, an open-drain level shifter and a DC-DC converter from
  VBUS. Ready-made boards:
  - [Bouni/BSH-Board](https://github.com/Bouni/BSH-Board): ESP32-C6, plugs into the RAST
    connector;
  - [kiu/BaSHi](https://github.com/kiu/BaSHi): ESP32-C3/C6/S3, with an isolated development
    mode;
  - [tubist2004/ESP32_D-Bus_Adapter](https://github.com/tubist2004/ESP32_D-Bus_Adapter).
  The connector is a 3-pin RAST 2.5. On dishwashers the bus is reached "via PCB traces well
  hidden inside the power connector". Background: the
  [39C3 talk](https://media.ccc.de/v/39c3-hacking-washing-machines).

> ⚠️ **Read this before opening anything.** From the project README: *"Even if parts of the
> appliances operate at low voltages, depending on your specific model they may or may not be
> isolated from earth. This is a good chance of death or serious injury. Under no circumstances
> should you connect external devices, such as your computer, directly to the appliance's
> electronics. … It is not guaranteed that the D-Bus is isolated from earth."*
>
> - Unplug the appliance from mains before opening it.
> - Never connect USB (laptop or charger) to an ESP32 that is attached to the bus, unless the
>   adapter is galvanically isolated. BaSHi: "Do not connect the ESP32 USB port while BaSHi is
>   connected" in non-isolated mode.
> - Expect water, heat and vibration inside the case.
> - This voids warranties.
>
> If any of that is unfamiliar, use the Home Connect API or Home Assistant instead.

---

## 3. Safety policy in the bridge

| Guard | Default | Why |
|---|---|---|
| Schema validation | always | The model can only pick from the program enum and five known options; `delay_minutes` is limited to 0..1440. Anything else is rejected and reported back to the model |
| Interlocks | always | The bridge refuses to start when the appliance is offline, a program is already running, paused or delayed, the door is open, or **remote start is off**. Pause, resume and cancel are checked against the current state |
| Confirmation | `--confirm heat` | `start_program` and `resume` open the water valve and the heater, so a human must answer *y*. Non-interactive runs (no TTY) count as **no**, unless `--yes`. `--confirm all` also asks for pause and cancel; `--confirm never` asks for nothing |
| Rate limit | 6 writes / 10 min, 60 s between starts | Stops a looping model from hammering the machine or the vendor API. Persisted in `~/.cache/chorepilot/control-ratelimit.json` across runs |
| Chains | `start_when_done` | Confirmed when scheduled. The target starts only once its remote start is armed *after* the first appliance finished (loading opens the door, which disarms it), and every interlock is re-checked at that moment. If the first appliance stops early, nothing starts. Gives up after 3 h. `control cancel -a <target>` removes it |
| Dry run | `--dry-run` | Reads are real; writes return the exact HTTP requests or MQTT publishes they would have sent (`would_send`) |
| Secrets | env or `~/.config/chorepilot` (0600) | `HA_TOKEN`, `HOME_CONNECT_TOKEN`, `HOME_CONNECT_CLIENT_ID`/`_SECRET`, `MQTT_USER`/`MQTT_PASSWORD`. Never the repo. Tool results sent back to the model carry outcomes only, not request URLs or headers |
| Audit | in memory | `Bridge.audit` lists every tool call, its arguments and its result |

---

## 4. No API at all? Monitor, don't switch

**A smart plug with power metering can tell you when a cycle ended.** The heater draws power
in phases, and the cycle is done when the load stays near idle. The same goes for a vibration
or door contact sensor. In HA they feed `notify_when_done` through the `ha` backend.

**Do not use the plug to switch the dishwasher.** The manufacturers say so explicitly.
Bosch's manual, under *WARNING – Risk of electric shock!*: **"Never equip the appliance with an
external switching device, e.g. a timer or remote control."** Under *Risk of fire!*: "Do not
use extension cables or multiple socket strips."
([SBI8ECS01A manual §1.4](https://media3.bosch-home.com/Documents/9001967977_A.pdf)).

Load is the other problem. One measured dishwasher peaked around 2 kW, about 9 A at 230 V
([TP-Link FAQ](https://www.tp-link.com/support/faq/4324/)), right at the limit of many
10 A plugs. Cutting power mid-cycle also leaves water in the tub and doesn't count as
"stop".

---

## 5. How to run it

```bash
# 1. a model that can call tools, anywhere on the LAN (Qwen2.5 >= 1.5B, Qwen3, Llama 3.1+,
#    Mistral Nemo, gpt-oss... llama.cpp's --jinja parsers; it is on by default in new builds)
chorepilot serve --model Qwen2.5-1.5B-Instruct-Pollard-Q4_K_S.gguf --host 0.0.0.0 --port 8080

# 2. talk to the dishwasher (the simulator, until you configure a backend)
chorepilot wash "hey, wash my dishes" --url http://192.168.1.20:8080
chorepilot control ask "pause it" --url http://192.168.1.20:8080
chorepilot control status
chorepilot control start eco --option half_load=true --dry-run   # no model: direct tool call
chorepilot control tools                                         # the JSON tool schemas

# 3. the real machine
export HA_TOKEN=...      # or HOME_CONNECT_CLIENT_ID + `chorepilot control login --backend homeconnect`
chorepilot wash "eco wash, tell me when it's done" --backend ha --dry-run   # look first
chorepilot wash "eco wash, tell me when it's done" --backend ha             # asks y/N, then starts
```

Without `--url`, `wash` starts a local server with the fastest instruct model that fits
(preferring ≥1B; 0.5B models often fumble the schema) and unloads it afterwards.
`notify_when_done` detaches a `chorepilot control wait` watcher. It sends a desktop
notification, a POST to `$CHOREPILOT_NOTIFY_URL` (e.g. an ntfy topic) and, on the `ha`
backend, `notify.notify`. A run that was cancelled or failed is reported as *stopped*, never
as *done*.
