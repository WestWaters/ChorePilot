# ChorePilot: devices, APIs and safety tiers

ChorePilot is a self-hosted, stdlib-Python assistant (Apache-2.0, formerly "Dishsoaker3000") that
tracks household chores and controls devices on the LAN through a local LLM's tool calls. The
model only *asks* for an action. ChorePilot's bridge checks the request against the device type's
schema, the caller's role and the action's safety tier, then decides whether it reaches a device.

This document covers which devices exist in a home or small business, how each one is reached,
which exact service names and API keys ChorePilot uses, and which tier each action belongs to.

Research date: **2026-10-21**. Every claim links to its source. **[unverified]** marks anything
I could not confirm from a primary source (vendor docs, the standard or its reference SDK, or the
Home Assistant source and docs).

Contents

1. [How ChorePilot reaches devices](#1-how-chorepilot-reaches-devices)
2. [Coverage table](#2-coverage-table)
3. [Vendor APIs](#3-vendor-apis)
4. [Home Assistant services and states ChorePilot uses](#4-home-assistant-services-and-states-chorepilot-uses)
5. [Risk tiers](#5-risk-tiers)
6. [Adding a device type](#6-adding-a-device-type)

---

## 1. How ChorePilot reaches devices

```
 LLM tool call --> ChorePilot bridge --(tier + role + schema + interlock check)--> adapter --> device
                                                                                 |
     ha          Home Assistant REST: GET /api/states, POST /api/services/<domain>/<service>
     homeconnect BSH Home Connect cloud API (OAuth2), for appliances HA does not cover well
     mqtt        MQTT topics, mainly ESPHome firmware
     matter      Matter devices, always through Home Assistant's Matter Server
     simulated   a JSON-file simulator per device, for tests and demos
```

**Home Assistant as the universal adapter (`ha`).** HA already integrates almost every brand, so
one adapter covers most devices. HA serves its REST API under `/api/` on port 8123 by default (80
under the Supervisor). Every request carries `Authorization: Bearer <long-lived token>`
([HA REST API](https://contributors.home-assistant.io/core/internals/rest-api/)). ChorePilot uses:

- `GET /api/states` and `GET /api/states/<entity_id>` for status.
- `POST /api/services/<domain>/<service>` with a JSON body (`entity_id` plus data fields) to act.
  This returns the states that changed. Add `?return_response` only for actions that return data.
  Adding it to an action without response data, or leaving it off an action that only returns
  data, gives a 400 ([same page](https://contributors.home-assistant.io/core/internals/rest-api/)).
- ChorePilot never writes `POST /api/states/<entity_id>`. That endpoint only changes HA's record of
  the state. It does not send anything to the device.

**Home Connect direct (`homeconnect`).** This is BSH's official cloud API (Bosch, Siemens, Neff,
Gaggenau and others) at `api.home-connect.com`. A simulator runs at `simulator.home-connect.com`
([endpoints](https://api-docs.home-connect.com/general/)). ChorePilot calls it directly when
there is no HA instance, or when it needs keys HA does not expose. See [3.1](#31-home-connect-bsh).

**MQTT / ESPHome (`mqtt`).** ESPHome's MQTT client publishes state to
`<TOPIC_PREFIX>/<COMPONENT_TYPE>/<COMPONENT_NAME>/state` and listens on `.../command`. Its HA
discovery prefix defaults to `homeassistant`
([ESPHome MQTT](https://esphome.io/components/mqtt/)). ESPHome's native API (port 6053,
protobuf, optional encryption key) is used by "the ESPHome tool, Home Assistant and ioBroker"
([ESPHome native API](https://esphome.io/components/api/)). ChorePilot therefore talks to
native-API devices through HA, and to MQTT devices directly.

**Matter via Home Assistant (`matter` maps onto `ha`).** HA runs its own Matter controller as a
separate process, the Matter Server app. "Home Assistant OS with the Home Assistant Matter Server
app is the supported path", and a standalone Docker Matter Server is unsupported
([HA Matter](https://www.home-assistant.io/integrations/matter/)). Matter devices therefore appear
to ChorePilot as ordinary HA entities. HA's discovery schemas decide which Matter cluster becomes
which entity, for example Robotic Vacuum Cleaner to `vacuum`, Water Valve to `valve` and Water
Heater to `water_heater`
([matter/vacuum.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/vacuum.py),
[valve.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/valve.py),
[water_heater.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/water_heater.py)).
ChorePilot does not ship its own Matter controller.

**Simulated (`simulated`).** Every device type has a fake implementation with realistic states and
interlocks (remote-start flag, door, operation state), so tool calls and tier policy can be tested
without hardware.

---

## 2. Coverage table

Columns:

- **HA domain**: links to the HA domain page. Service names and fields are checked against each
  domain's `services.yaml` on HA `dev` (section 4).
- **Matter device type**: the type and the Matter release that first carries it in the CSA
  reference SDK's per-version data model
  ([connectedhomeip/data_model](https://github.com/project-chip/connectedhomeip/tree/master/data_model)).
  Release announcements:
  [1.2](https://csa-iot.org/newsroom/matter-1-2-arrives-with-nine-new-device-types-improvements-across-the-board/),
  [1.3](https://csa-iot.org/newsroom/matter-1-3-specification-released/),
  [1.4](https://csa-iot.org/newsroom/matter-1-4-enables-more-capable-smart-homes/),
  [1.5](https://csa-iot.org/newsroom/matter-1-5-introduces-cameras-closures-and-enhanced-energy-management-capabilities/).
  "1.0" means the type is already in the 1.0 data model folder.
- **Tier**: the *highest* tier of any action on that class. Per-action tiers are in [5.2](#52-per-class-per-action-tiers).

### 2.1 Home

| Device class | HA domain: key services / attributes | Matter device type (first release) | Vendor / other APIs | Tier |
|---|---|---|---|---|
| Thermostat / central HVAC | [`climate`](https://www.home-assistant.io/integrations/climate/): `set_temperature`, `set_hvac_mode`, `turn_off`; attrs `current_temperature`, `hvac_action`, `target_temp_low/high` | Thermostat 0x0301 (1.0); Thermostat Controller (1.4.2 folder) | ecobee, Google Nest SDM, Home Connect AC, LG ThinQ, SmartThings | 2 |
| Room AC / mini-split | `climate` (same) | Room Air Conditioner 0x0072 (1.2) | LG ThinQ, SmartThings, Home Connect `HeatingVentilationAirConditioning.AirConditioner.*` ([programs](https://api-docs.home-connect.com/programs-and-options/#air-conditioner)) | 2 |
| Heat pump | `climate` and/or `water_heater` | Heat Pump 0x0309 (1.4) | vendor-specific; Matter via HA | 2 |
| Water heater / boiler | [`water_heater`](https://www.home-assistant.io/integrations/water_heater/): `set_temperature`, `set_operation_mode`, `set_away_mode`; Matter boost via `matter.water_heater_boost` ([HA Matter](https://www.home-assistant.io/integrations/matter/)) | Water Heater 0x050F (1.4) | LG ThinQ (water heater, system boiler) ([HA lg_thinq](https://www.home-assistant.io/integrations/lg_thinq/)) | 2 |
| Irrigation / sprinklers | [`valve`](https://www.home-assistant.io/integrations/valve/) or `switch` per zone; vendor actions `rachio.start_watering`, `rainbird.start_irrigation` | Water Valve 0x0042 (1.3); Irrigation System 0x0040 and Soil Sensor 0x0045 (1.5 folder) | Rachio public API, Rain Bird (local, via HA), OpenSprinkler **[unverified]** | 2 |
| Soil / rain sensor | `sensor` / `binary_sensor` | Soil Sensor (1.5); Rain Sensor 0x0044 (1.3) | via HA | 0 |
| Lawn mower | [`lawn_mower`](https://www.home-assistant.io/integrations/lawn_mower/): `start_mowing`, `pause`, `dock`, `stop` | none found in data model 1.0 to 1.7 | e.g. [Husqvarna Automower](https://www.home-assistant.io/integrations/husqvarna_automower/) | 1 |
| Robot vacuum / mop | [`vacuum`](https://www.home-assistant.io/integrations/vacuum/): `start`, `pause`, `stop`, `return_to_base`, `clean_area`, `set_fan_speed` | Robotic Vacuum Cleaner 0x0074 (1.2) | iRobot via HA [`roomba`](https://www.home-assistant.io/integrations/roomba/) (local); Home Connect `ConsumerProducts.CleaningRobot.*`; SmartThings `robotCleaner*` | 1 |
| Light | [`light`](https://www.home-assistant.io/integrations/light/): `turn_on` (`brightness_pct`, `color_temp_kelvin`, `rgb_color`, `transition`), `turn_off` | On/Off, Dimmable, Color Temperature, Extended Color Light (1.0) | Philips Hue (local, via HA [`hue`](https://www.home-assistant.io/integrations/hue/)) | 1 |
| Switch / smart plug | [`switch`](https://www.home-assistant.io/integrations/switch/): `turn_on`, `turn_off`, `toggle` | On/Off Plug-in Unit 0x010A (1.0); Mounted On/Off Control (1.4) | ESPHome, Shelly etc. via HA | 1 (load decides; see [5.3](#53-never-do)) |
| Fan / ceiling fan | [`fan`](https://www.home-assistant.io/integrations/fan/): `turn_on` (`percentage`, `preset_mode`), `set_percentage`, `oscillate`, `set_direction` | Fan 0x002B (1.2 as a certifiable type) | LG ThinQ ceiling fan | 1 |
| Blinds / shades / awnings | [`cover`](https://www.home-assistant.io/integrations/cover/): `open_cover`, `close_cover`, `set_cover_position`, `stop_cover`, tilt services | Window Covering 0x0202 (1.0); Closure / Closure Panel (1.5) | via HA | 1 |
| Garage door / gate | `cover` (device class garage / gate) | Closure (1.5) | via HA | 3 to open, 1 to close |
| Door lock | [`lock`](https://www.home-assistant.io/integrations/lock/): `lock`, `unlock`, `open` (`code`) | Door Lock 0x000A (1.0) | via HA | 3 (unlock / open) |
| Alarm panel | [`alarm_control_panel`](https://www.home-assistant.io/integrations/alarm_control_panel/): `alarm_arm_home/away/night/vacation/custom_bypass`, `alarm_disarm` (`code`) | none found in data model | via HA | 3 (disarm) |
| Camera (read-only) | [`camera`](https://www.home-assistant.io/integrations/camera/): `snapshot` (`filename`); also `turn_on/off`, `enable/disable_motion_detection`, `record` | Camera, Snapshot Camera, Floodlight Camera (1.5) | via HA | 0 for snapshot; 3 for privacy changes |
| Doorbell | `event` / `binary_sensor` + `camera` (e.g. [Ring](https://www.home-assistant.io/integrations/ring/), [DoorBird](https://www.home-assistant.io/integrations/doorbird/)) | Doorbell, Audio Doorbell, Video Doorbell, Chime (1.5) | via HA | 0 (read) |
| Media player / speaker / TV | [`media_player`](https://www.home-assistant.io/integrations/media_player/): `media_play`, `media_pause`, `media_stop`, `volume_set`, `select_source`, `play_media` | Speaker, Basic/Casting Video Player (1.0); Streaming/Casting Audio Player (1.7 folder) | via HA | 1 |
| Oven / range | `select` (program), `number` (setpoint), `button` (start/stop), `switch` (power) from vendor integrations; Matter oven via `select` (Oven Mode), `number` (Temperature Control), `button` (Operational State) | Oven 0x007B (1.3), built from Temperature Controlled Cabinet endpoints | Home Connect, LG ThinQ, Miele, SmartThings | 2 (preheat) |
| Cooktop / hob | read-only status; off only | Cooktop 0x0078 and Cook Surface 0x0077 (1.3), **On/Off cluster with mandatory OffOnly feature** ([Cooktop.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.3/device_types/Cooktop.xml)) | Home Connect (no cooktop programs), LG ThinQ, Miele (no start, no power-on) | turn-on never allowed |
| Microwave | `number` cook time, `select` mode, `button` start (Matter: `MicrowaveOvenControl.SetCookingParameters`, [matter/number.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/number.py)) | Microwave Oven 0x0079 (1.3) | Home Connect `Cooking.Oven.Program.Microwave.*`, LG ThinQ | 2 |
| Extractor hood | `fan` / `select` / `light` | Extractor Hood 0x007A (1.3) | Home Connect `Cooking.Common.Program.Hood.*`, LG ThinQ, Miele | 1 |
| Coffee maker / espresso | `select` (program) + `button`, or Home Connect action `home_connect.set_program_and_options` | none found in data model | Home Connect `ConsumerProducts.CoffeeMaker.*`, Miele Coffee System (power on/off only, no START in the Miele table) | 2 |
| Refrigerator / freezer | `sensor` (temperature), `binary_sensor` (door), `number` (setpoint), `switch` (super/eco modes) | Refrigerator 0x0070 and Temperature Controlled Cabinet 0x0071 (1.2) | Home Connect `Refrigeration.*`, LG ThinQ, Miele, SmartThings `refrigerationSetpoint` | 2 (setpoint) |
| Dishwasher | `select` / `button` / `binary_sensor` (remote start) | Dishwasher 0x0075 (1.2) | Home Connect, Miele, LG ThinQ, SmartThings | 2 |
| Washer / dryer / washer-dryer | same as dishwasher | Laundry Washer 0x0073 (1.2); Laundry Dryer 0x007C (1.3) | Home Connect, Miele, LG ThinQ, SmartThings | 2 |
| Air purifier | `fan` + `switch` + `sensor` (filter, AQI) | Air Purifier 0x002D (1.2) | LG ThinQ, via HA | 1 |
| Humidifier / dehumidifier | [`humidifier`](https://www.home-assistant.io/integrations/humidifier/): `set_humidity`, `set_mode`, `turn_on/off` | Humidity Conditioner 0x007D (1.7 folder; release status **[unverified]**) | LG ThinQ, via HA | 1 |
| EV charger | `switch` (charging enabled), `number` (current limit), `sensor` | EVSE 0x050C (1.3); HA maps `EnergyEvse.EnableCharging` / `Disable` to a switch ([matter/switch.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/switch.py)) | OCPP ([Open Charge Alliance](https://openchargealliance.org/protocols/open-charge-point-protocol/)) via the community [ocpp](https://github.com/lbbrhzn/ocpp) integration (not HA core); HA [Wallbox](https://www.home-assistant.io/integrations/wallbox/), [Tesla Wall Connector](https://www.home-assistant.io/integrations/tesla_wall_connector/), [Ohme](https://www.home-assistant.io/integrations/ohme/) | 2 |
| Pool / spa (pump, heater) | `climate` / `water_heater` (heater), `switch` / `select` (pump) | Pump 0x0303 (1.0) | via HA **[unverified]** per brand | 2 |
| Energy meter / solar / battery | `sensor` (power, energy); battery modes via `select` / `number` | Electrical Sensor (1.3); Device Energy Management 0x050D (1.3); Solar Power 0x0017 and Battery Storage 0x0018 (1.4); Electrical Meter / Utility Meter / Energy Tariff (1.5) | vendor integrations via HA | 0 read; 2 for battery / DEM mode changes |
| Smoke / CO alarm | `binary_sensor` (`smoke`, `carbon_monoxide`); Matter self-test `button` | Smoke CO Alarm 0x0076 (1.2) | via HA | 0 |
| Leak / freeze sensor | `binary_sensor` (`moisture`, `cold`) | Water Leak Detector 0x0043, Water Freeze Detector 0x0041 (1.3) | via HA | 0 |
| Water main shutoff | `valve` (`device_class: water`) | Water Valve 0x0042 (1.3) | via HA | close = 1, reopen = 3 |
| Gas valve | `valve` (`device_class: gas`) ([valve/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/valve/const.py)) | none specific | via HA | close = 1, open = 3 |
| Contact / motion / temp sensors | `binary_sensor`, `sensor` | Contact, Occupancy, Temperature, Humidity Sensor (1.0) | via HA, ESPHome | 0 |

### 2.2 Small business

ChorePilot does **not** do point of sale. These are the small-business devices it does cover:

| Device class | How | Matter | Notes | Tier |
|---|---|---|---|---|
| Walk-in cooler / freezer / reach-in temperature | `sensor` temperature + `binary_sensor` door (ESPHome probe, Matter sensor or vendor) | Temperature Sensor 0x0302, Contact Sensor 0x0015 (1.0); Temperature Controlled Cabinet (1.2) for appliance-type units | Monitor only. Alert on threshold plus duration, and on door left open. ChorePilot never changes commercial refrigeration setpoints. | 0 |
| Commercial dishwasher | Read-only: power or current clamp on ESPHome, or a door contact. A cycle is counted as "done" when power drops. | none | I found no public consumer-style control API for commercial warewashers **[unverified]** | 0 |
| Door access (staff doors, back door) | `lock` via HA; access-control systems via their HA integrations | Door Lock (1.0) | Unlock is tier 3. Locking at close of day is tier 1. | 3 |
| Shop lights, signage, HVAC schedules | `light`, `switch`, `climate` | as above | as home | 1 / 2 |
| Freezer / cooler alarms | `binary_sensor`, Home Connect `Refrigeration.FridgeFreezer.Event.TemperatureAlarmFreezer` | Refrigerator alarm (HA reads `RefrigeratorAlarm.State`, [matter/binary_sensor.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/binary_sensor.py)) | Escalate to owner. | 0 |

---

## 3. Vendor APIs

### 3.1 Home Connect (BSH)

Covers dishwasher, washer, dryer, washer-dryer, oven, microwave, warming drawer, cooktop, hood,
fridge / freezer / wine cooler, coffee machine, cleaning robot, air conditioner and cook processor
([appliance list](https://api-docs.home-connect.com/programs-and-options/)). It is a cloud API over
HTTPS with JSON, using OAuth2 (authorization code or device flow).

**Endpoints ChorePilot uses**
([best practices](https://api-docs.home-connect.com/general/#best-practices)):

| Purpose | Request |
|---|---|
| list appliances, `connected` flag | `GET /api/homeappliances` |
| status (remote control, remote start, local control, operation, door) | `GET /api/homeappliances/{haId}/status` |
| settings (power, fridge setpoints) | `GET` / `PUT /api/homeappliances/{haId}/settings/{key}` |
| start a program with options | `PUT /api/homeappliances/{haId}/programs/active` with `{"data":{"key":"<program>","options":[{"key":"<option>","value":...,"unit":...}]}}` ([example](https://api-docs.home-connect.com/programs-and-options/)) |
| available options and constraints for a program | `GET /api/homeappliances/{haId}/programs/available/{programKey}` |
| live events (SSE) | `GET /api/homeappliances/{haId}/events` ([events](https://api-docs.home-connect.com/events/)) |

**Remote-start interlock: all four conditions must hold before any program start**
([best practices](https://api-docs.home-connect.com/general/#best-practices)):

1. The appliance is connected (`connected` flag in `GET /api/homeappliances`).
2. `BSH.Common.Status.RemoteControlActive` is true. This "must be set once on the appliance manually
   by the user".
3. `BSH.Common.Status.RemoteControlStartAllowed` is true. This "must be set on the appliance
   manually by the user". The flag can be cleared by a programmatic change (disable only), by the
   user, or "automatically after a certain duration - usually 24 hours"
   ([states](https://api-docs.home-connect.com/states/)).
4. `BSH.Common.Status.LocalControlActive` is false, and `BSH.Common.Status.OperationState` is
   `BSH.Common.EnumType.OperationState.Ready`. After the start, read the state again and confirm
   `...OperationState.Run`, because the start can still fail (the docs give "the water tank is empty"
   as an example).

ChorePilot reads all four before every tier-2 start and refuses if any is false. It never tries to
set the remote-start flag. Operation-state values are `Inactive`, `Ready`, `DelayedStart`, `Run`,
`Pause`, `ActionRequired`, `Finished`, `Error`, `Aborting`. `BSH.Common.Status.DoorState` is
`Open`, `Closed` or `Locked` ([states](https://api-docs.home-connect.com/states/)).

**Rate limits** ([rate limiting](https://api-docs.home-connect.com/general/#rate-limiting)): 1000
requests per client and user per day; 50 per minute; 10 per second on average (burst 20); 5 program
starts and 5 program stops per minute; 10 event-monitoring channels. The docs say "do not query for
state information but use the monitoring function instead". ChorePilot subscribes to events and
caches state.

**Coffee machine**
([programs and options](https://api-docs.home-connect.com/programs-and-options/#coffee-machine)):

- Beverage programs: `ConsumerProducts.CoffeeMaker.Program.Beverage.` + `Ristretto`, `Espresso`,
  `EspressoDoppio`, `Coffee`, `XLCoffee`, `CaffeGrande`, `EspressoMacchiato`, `Cappuccino`,
  `LatteMacchiato`, `CaffeLatte`, `MilkFroth`, `WarmMilk`, `HotWater`. Regional drinks are under
  `ConsumerProducts.CoffeeMaker.Program.CoffeeWorld.*` (`Americano`, `FlatWhite`, `Cortado`,
  `WienerMelange`, ...).
- Options:
  - `ConsumerProducts.CoffeeMaker.Option.BeanAmount` (enum
    `ConsumerProducts.CoffeeMaker.EnumType.BeanAmount.` `VeryMild`, `Mild`, `MildPlus`, `Normal`,
    `NormalPlus`, `Strong`, `StrongPlus`, `VeryStrong`, `VeryStrongPlus`, `ExtraStrong`,
    `DoubleShot`, `DoubleShotPlus`, `DoubleShotPlusPlus`, ...).
  - `ConsumerProducts.CoffeeMaker.Option.FillQuantity` (integer, ml; min, max and step come from
    the program's constraints).
  - `ConsumerProducts.CoffeeMaker.Option.CoffeeTemperature` (enum `...EnumType.CoffeeTemperature.88C`
    to `96C`).
  - Also `BeanContainerSelection`, `FlowRate`, `MultipleBeverages`, `CoffeeMilkRatio`,
    `HotWaterTemperature`.
- The docs advise selecting the program first and then reading its available options, because
  appliances update the option list dynamically.
- Events ([events](https://api-docs.home-connect.com/events/)): `...Event.WaterTankEmpty`,
  `BeanContainerEmpty`, `DripTrayFull`, `DeviceShouldBeDescaled`, `DeviceShouldBeCleaned`,
  `KeepMilkTankCool`. HA lists "Remote start" as available for coffee makers
  ([HA home_connect](https://www.home-assistant.io/integrations/home_connect/)).

**Oven** ([programs](https://api-docs.home-connect.com/programs-and-options/#oven)):

- Programs: `Cooking.Oven.Program.HeatingMode.` + `PreHeating`, `HotAir`, `HotAirEco`,
  `HotAirGrilling`, `TopBottomHeating`, `TopBottomHeatingEco`, `BottomHeating`, `PizzaSetting`,
  `SlowCook`, `IntensiveHeat`, `KeepWarm`, `PreheatOvenware`, `FrozenHeatupSpecial`, `Desiccation`,
  `Defrost`, `Proof`, `GrillLargeArea`, `GrillSmallArea`, `HotAir30Steam` to `HotAir100Steam`,
  `SabbathProgramme`, `WarmingDrawer`.
- Options:
  - `Cooking.Oven.Option.SetpointTemperature` (double; for example 30 to 250 °C or 150 to 480 °F).
  - `BSH.Common.Option.Duration` (integer seconds; for example 1 to 86340).
  - `Cooking.Oven.Option.FastPreHeat` (boolean; only takes effect at a setpoint of at least
    100 °C / 212 °F).
  - `Cooking.Oven.Option.Level` (grill level).
  - `BSH.Common.Option.StartInRelative`.
- Preheat recipe: `PreHeating` "starts up a hot air program with the fast pre-heat option".
  Use a duration of at least 10 minutes, wait for `Cooking.Oven.Event.PreheatFinished`, and do not
  rely on `Cooking.Oven.Status.CurrentCavityTemperature` because it "might not provide the necessary
  accuracy" ([best practices](https://api-docs.home-connect.com/general/#best-practices)).
- Oven remote start follows the same four-condition rule. HA lists "Remote control", "Remote start"
  and "Local control" binary sensors for ovens
  ([HA home_connect](https://www.home-assistant.io/integrations/home_connect/)).
- Microwaves can report `type = Oven`. They are recognised by `Cooking.Oven.Program.Microwave.*`
  programs (`90Watt` to `1000Watt`, `Max`).

**Cooktop / hob.** The cooktop section of the program list has no programs at all
([programs](https://api-docs.home-connect.com/programs-and-options/#cooktop)). The only cooktop
setting documented is `Cooking.Hob.Setting.Ventilation` for venting hobs
([settings](https://api-docs.home-connect.com/settings/)). ChorePilot reads cooktop status and
never commands heat.

**Hood.** Programs `Cooking.Common.Program.Hood.Automatic`, `Venting` and `DelayedShutOff`, with
options `Cooking.Common.Option.Hood.VentingLevel` and `IntensiveLevel`
([programs](https://api-docs.home-connect.com/programs-and-options/#hood)). Filter events are
`Cooking.Common.Event.Hood.GreaseFilterMaxSaturationNearlyReached` and `...Reached`.

**Refrigeration.** Fridges and freezers have no programs; they are integrated through status,
settings and events ([programs](https://api-docs.home-connect.com/programs-and-options/#fridge-freezer)).

| Kind | Key | Notes |
|---|---|---|
| Setting | `Refrigeration.FridgeFreezer.Setting.SetpointTemperatureRefrigerator` | double; for example 2 to 8 °C; read the range per appliance ([settings](https://api-docs.home-connect.com/settings/)) |
| Setting | `Refrigeration.FridgeFreezer.Setting.SetpointTemperatureFreezer` | double; for example -24 to -16 °C |
| Setting | `Refrigeration.FridgeFreezer.Setting.SuperModeRefrigerator` / `SuperModeFreezer` | boolean; cools to the lowest temperature until the user turns it off or it times out |
| Setting | `Refrigeration.Common.Setting.EcoMode`, `VacationMode`, `FreshMode`, `SabbathMode` | boolean |
| Setting | `Refrigeration.Common.Setting.{BottleCooler,ChillerLeft,ChillerRight,ChillerCommon,WineCompartment}.SetpointTemperature` | multi-zone models |
| Status | `Refrigeration.Common.Status.Door.Refrigerator`, `.Freezer`, `.Refrigerator2`, `.Refrigerator3`, `.FlexCompartment`, `.BottleCooler`, `.Chiller*`, `.WineCompartment` | enum `Refrigeration.Common.EnumType.Door.States.Open` / `.Closed` ([states](https://api-docs.home-connect.com/states/)) |
| Event | `Refrigeration.FridgeFreezer.Event.DoorAlarmFreezer` | "freezer door was left open too long" ([events](https://api-docs.home-connect.com/events/)) |
| Event | `Refrigeration.FridgeFreezer.Event.DoorAlarmRefrigerator` | refrigerator door left open too long |
| Event | `Refrigeration.FridgeFreezer.Event.TemperatureAlarmFreezer` | "freezer temperature is too high" |

**Dishwasher, washer, dryer.** These use program keys such as
`Dishcare.Dishwasher.Program.Eco50`, `LaundryCare.Washer.Program.Cotton` and
`LaundryCare.Dryer.Program.Cotton`, with options such as `BSH.Common.Option.StartInRelative`,
`BSH.Common.Option.FinishInRelative`, `LaundryCare.Washer.Option.Temperature` / `SpinSpeed` and
`LaundryCare.Dryer.Option.DryingTarget`
([programs](https://api-docs.home-connect.com/programs-and-options/)). The full per-machine lists
are in `chorepilot/machines.py` and in [CONTROL.md](CONTROL.md).

**Home Connect through HA.** HA uses the same cloud API
([HA home_connect](https://www.home-assistant.io/integrations/home_connect/)). It provides these
actions ([services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/home_connect/services.yaml)):

- `home_connect.set_program_and_options`: `device_id`, `affects_to` (`active_program` or
  `selected_program`), `program`, plus per-type option fields. Values are snake_case, for example
  program `consumer_products_coffee_maker_program_beverage_espresso` or
  `cooking_oven_program_heating_mode_pre_heating`, and option fields such as
  `consumer_products_coffee_maker_option_bean_amount`,
  `consumer_products_coffee_maker_option_fill_quantity`,
  `cooking_oven_option_setpoint_temperature`, `b_s_h_common_option_duration` and
  `cooking_oven_option_fast_pre_heat`.
- `home_connect.change_setting`: `device_id`, `key`, `value`.
- `home_connect.start_selected_program`: `device_id`, optional
  `b_s_h_common_option_start_in_relative` / `..._finish_in_relative`.

### 3.2 LG ThinQ

The official API is ThinQ Connect: a cloud API that authenticates with a personal access token
(PAT) and needs an internet connection
([HA lg_thinq](https://www.home-assistant.io/integrations/lg_thinq/)). LG's developer portal
(`thinq.developer.lge.com`) did not resolve from here, so the device-profile details below come
from HA's docs and LG's official Python SDK,
[thinq-connect/pythinqconnect](https://github.com/thinq-connect/pythinqconnect).

- HA exposes a "Remote start" binary sensor for the cooktop, dishwasher, oven and the laundry
  family. It exposes Start / Pause buttons for the dishwasher and laundry, number entities for oven
  and refrigerator temperatures, select entities for the oven's cook mode and operation, and
  switches for the refrigerator's express and quick-freeze modes
  ([HA lg_thinq](https://www.home-assistant.io/integrations/lg_thinq/)).
- The SDK's oven and cooktop profiles carry `remoteControlEnable.remoteControlEnabled`. The oven
  has `set_cook_mode`, `set_target_temperature_c/f`, `set_target_time` and `set_timer`; the cooktop
  has `set_power_level` and `set_remain_hour/minute`
  ([oven.py](https://github.com/thinq-connect/pythinqconnect/blob/main/thinqconnect/devices/oven.py),
  [cooktop.py](https://github.com/thinq-connect/pythinqconnect/blob/main/thinqconnect/devices/cooktop.py)).
- Which cooktop power levels the cloud actually accepts (for example lowering only) **[unverified]**.
  ChorePilot exposes only cooktop status and power-level *reduction to off*, and only when
  `remoteControlEnabled` is true.

### 3.3 Samsung SmartThings

SmartThings models devices as capabilities. The capability reference
([developer.smartthings.com](https://developer.smartthings.com/docs/devices/capabilities/capabilities-reference))
renders client-side, and `api.smartthings.com/v1/capabilities/...` needs a token (HTTP 401
unauthenticated). The capability, attribute and command names below therefore come from
[pysmartthings](https://github.com/pySmartThings/pysmartthings/blob/main/src/pysmartthings/capability.py),
the library HA's integration uses:

- Capabilities: `ovenOperatingState`, `ovenMode`, `ovenSetpoint`, `remoteControlStatus`,
  `dishwasherOperatingState`, `washerOperatingState`, `dryerOperatingState`,
  `refrigerationSetpoint`, `thermostatCoolingSetpoint`, `valve`, `lock`, `robotCleanerMovement`,
  `evseState`, plus Samsung-specific `samsungce.*` variants.
- Attributes: `remoteControlEnabled`, `machineState`, `ovenSetpoint`, `ovenJobState`,
  `completionTime`.
- Commands: `setMachineState`, `setOvenSetpoint`, `setOvenMode`
  ([attribute.py](https://github.com/pySmartThings/pysmartthings/blob/main/src/pysmartthings/attribute.py),
  [command.py](https://github.com/pySmartThings/pysmartthings/blob/main/src/pysmartthings/command.py)).
- Interlock: HA refuses a dishwasher start when `remoteControlStatus.remoteControlEnabled` is
  `"false"`, and requires `machineState == "stop"` first
  ([smartthings/switch.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/smartthings/switch.py)).
  ChorePilot applies the same check to every SmartThings appliance start.
- Whether Samsung ovens accept `setMachineState` / `setOvenSetpoint` to *start* heating remotely
  **[unverified]**. Users report that these commands return 200 without effect, but that comes from
  community forums, not a primary source. ChorePilot treats a Samsung oven start as tier 2 and
  confirms the effect by reading `machineState` back.

### 3.4 Miele 3rd Party API

This is a cloud REST API with `GET /devices`, `/devices/{id}/state` and `/devices/{id}/actions`,
and `PUT /devices/{id}/actions` with bodies such as `{"processAction": 2}`
([general concept](https://developer.miele.com/docs/general-concept)). Miele's capability table
([PDF, API 1.1.0](https://developer.miele.com/docs/third-party-api/API_V1.1.x_capabilities_by_device.pdf))
shows:

- `processAction` START for washing machine (1), tumble dryer (2), dishwasher (7), ovens
  (12, 13, 15, 16), washer-dryer (24), steam oven combi (31), steam oven micro (45) and Dialogoven
  (67). START_/STOP_SUPERFREEZING and SUPERCOOLING exist for fridges and freezers.
- **Hobs (14 Highlight, 27 Induction, 74 with vapour extraction): no `processAction`, no `powerOn`
  and no `powerOff`.** `deviceName` is the only listed action. The hob plate state (`plateStep`)
  is read-only.
- Coffee System (17): `powerOn`, `powerOff` and `light`, but no `processAction` START.
- Footnote: availability varies by model and must be checked with `GET actions`.

Remote start condition: the `remoteEnable` state object carries `fullRemoteControl`, `smartGrid`
and `mobileStart`. HA shows "Full remote control" and "Mobile start" sensors and says "Most
appliances require you to manually activate mobile start or remote control mode", and that hob
plates "can only be monitored from Home Assistant, not controlled"
([HA miele](https://www.home-assistant.io/integrations/miele/)). The exact field semantics on
Miele's own pages are rendered client-side and could not be read here **[unverified]**.

### 3.5 Irrigation: Rachio, Rain Bird, OpenSprinkler

- **Rachio** (public cloud API): base `https://api.rach.io/1`, `Authorization: Bearer <API key>`.
  The key is copied from the app under Profile, "API key -> Copy"
  ([getting started](https://rachio.readme.io/reference/getting-started),
  [authentication](https://rachio.readme.io/reference/authentication)).
  - Start a zone: `PUT /public/zone/start` with `{"id": "<zoneId>", "duration": <seconds>}`;
    "Range is 0 - 10800 (3 Hours)" ([zone start](https://rachio.readme.io/reference/zonestart)).
  - Also `PUT /public/zone/start_multiple`, `/public/device/stop_water`,
    `/public/device/rain_delay`, `/public/device/pause_zone_run` and `/resume_zone_run`.
  - Rate limit: 1700 calls per day, per
    [HA rachio](https://www.home-assistant.io/integrations/rachio/). HA recommends a failsafe zone
    duration so the controller still stops the zone if a script or HA fails. HA actions:
    `rachio.start_watering`, `rachio.start_multiple_zone_schedule`.
- **Rain Bird**: local control with no cloud requirement (newer firmware serves HTTPS). Default zone
  run is 6 minutes; the action is `rainbird.start_irrigation`. The LNK WiFi module handles one
  request at a time ([HA rainbird](https://www.home-assistant.io/integrations/rainbird/)).
- **OpenSprinkler**: no HA core integration (the HA doc page returns 404). Community integrations
  and OpenSprinkler's own HTTP API exist **[unverified]**.
- **Matter**: Water Valve (1.3) maps to the HA `valve` domain; Irrigation System and Soil Sensor
  are in the 1.5 data model, and CSA describes soil sensors "combined with Matter-based water valves
  or irrigation systems"
  ([Matter 1.5](https://csa-iot.org/newsroom/matter-1-5-introduces-cameras-closures-and-enhanced-energy-management-capabilities/)).
  HA reads `SoilMeasurement.SoilMoistureMeasuredValue` and `ValveConfigurationAndControl` attributes
  ([matter/sensor.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/sensor.py)).

ChorePilot always sends irrigation with an explicit duration, capped by the zone's configured
maximum, and never as an open-ended "on".

### 3.6 Thermostats: ecobee and Google Nest SDM

- **ecobee**: functions are sent as `POST /thermostat` with
  `{"selection": {"selectionType": "registered", "selectionMatch": ""}, "functions": [...]}`
  ([using functions](https://www.ecobee.com/home/developer/api/documentation/v1/functions/using-functions.shtml)).
  - `setHold` takes `heatHoldTemp`, `coolHoldTemp`, `holdType` (`dateTime`, `nextTransition`,
    `indefinite`, `holdHours`) and `holdHours`
    ([SetHold](https://www.ecobee.com/home/developer/api/documentation/v1/functions/SetHold.shtml)).
  - Read `Runtime.desiredHeatRange` / `desiredCoolRange` first, so the server does not adjust the
    hold ([Runtime](https://www.ecobee.com/home/developer/api/documentation/v1/objects/Runtime.shtml)).
  - Temperatures are integers in tenths of °F **[unverified]**: the pages read here did not state
    the unit.
- **Google Nest SDM**: `POST /enterprises/{project}/devices/{device}:executeCommand`.
  - `sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat` (`heatCelsius`), `.SetCool`
    (`coolCelsius`) and `.SetRange` (both). Each needs the matching mode (HEAT / COOL / HEATCOOL),
    and "All Set* commands will be rejected if the thermostat is in Eco mode"
    ([setpoint trait](https://developers.google.com/nest/device-access/traits/device/thermostat-temperature-setpoint)).
  - `sdm.devices.commands.ThermostatMode.SetMode` with `HEAT`, `COOL`, `HEATCOOL` or `OFF`
    ([mode trait](https://developers.google.com/nest/device-access/traits/device/thermostat-mode)).
- Through HA both appear as `climate` entities ([ecobee](https://www.home-assistant.io/integrations/ecobee/),
  [nest](https://www.home-assistant.io/integrations/nest/)), which is what ChorePilot uses.

### 3.7 Philips Hue

Hue's API v2 (CLIP v2) is local and HTTPS-only ("The V2 API does not support HTTP"), and it pushes
state changes as events ([new Hue API](https://developers.meethue.com/new-hue-api/)). The detailed
reference sits behind a developer login. The resource paths (`/clip/v2/resource/light/{id}`), the
`hue-application-key` header and the `/eventstream/clip/v2` stream are therefore **[unverified]**
here. ChorePilot drives Hue through HA's [`hue`](https://www.home-assistant.io/integrations/hue/)
integration and `light.*` services.

### 3.8 iRobot (Roomba)

I found no official public cloud control API for consumer Roombas **[unverified]**. HA's
[`roomba`](https://www.home-assistant.io/integrations/roomba/) integration works locally:

- It discovers the robot's BLID and fetches a robot password (not the iRobot account password) by
  holding Home, or Home + Spot, on the robot. Some models, including the j7, do not hand the
  password out locally.
- The robot's MQTT server "only allows a single connection". HA's default continuous mode pushes
  the iRobot app onto the cloud.

ChorePilot uses the resulting `vacuum` entity.

### 3.9 EV chargers

- **Matter EVSE (1.3)**: HA exposes a charging switch backed by `EnergyEvse.EnableCharging` /
  `Disable`. It also shows sensors for state, supply state, fault state, circuit capacity, min/max
  and user-max charge current, and state of charge
  ([switch.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/switch.py),
  [sensor.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/sensor.py)).
- **OCPP** ([Open Charge Alliance](https://openchargealliance.org/protocols/open-charge-point-protocol/))
  is the charger-to-backend protocol. A local OCPP central system for HA exists as a community
  integration ([lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp)), not in HA core.
- Brand integrations in HA core include [Wallbox](https://www.home-assistant.io/integrations/wallbox/),
  [Tesla Wall Connector](https://www.home-assistant.io/integrations/tesla_wall_connector/) and
  [Ohme](https://www.home-assistant.io/integrations/ohme/).
- ChorePilot's EV actions: start charging and set a current limit within the charger's
  configured min/max (tier 2); stop charging (tier 1, it only reduces load).

### 3.10 Matter via Home Assistant: what becomes what

From HA's Matter discovery schemas (`dev` branch):

| Matter | HA entity | Source |
|---|---|---|
| Thermostat, Room Air Conditioner | `climate` | [climate.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/climate.py) |
| Water Heater (WaterHeaterManagement boost) | `water_heater` + `matter.water_heater_boost` | [water_heater.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/water_heater.py) |
| Water Valve | `valve` | [valve.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/valve.py) |
| Robotic Vacuum Cleaner (RvcRunMode, RvcOperationalState, ServiceArea) | `vacuum` | [vacuum.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/vacuum.py) |
| Operational State Start / Stop / Pause / Resume (dishwasher, laundry, oven) | `button`, created only for commands in `AcceptedCommandList` | [button.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/button.py) |
| Dishwasher / Laundry / Oven / Refrigerator / RVC / EVSE / Water Heater / Microwave modes | `select` | [select.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/select.py) |
| Temperature Control setpoint, Microwave cook time | `number` | [number.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/number.py) |
| OnOff on Air Purifier, Cooktop, Cook Surface, Dishwasher, Extractor Hood, Laundry, Oven, Pump, Refrigerator, RVC, Room AC | `switch` "power" | [switch.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/switch.py) |
| Smoke CO Alarm, Water Leak / Freeze, Rain, Dishwasher / Refrigerator alarms | `binary_sensor` | [binary_sensor.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/binary_sensor.py) |

Two consequences:

- **Matter start is optional.** In the Operational State cluster, `Start` is optional while `Stop`,
  `Pause` and `Resume` follow other conformance
  ([OperationalState.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.5.1/clusters/OperationalState.xml)).
  The oven cavity variant *disallows* `Pause` and `Resume`
  ([OperationalState_Oven.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.5.1/clusters/OperationalState_Oven.xml)).
  A certified appliance can therefore be monitor-only. ChorePilot offers "start" only when the HA
  start button exists.
- **The cooktop power switch is off-only.** The Cooktop and Cook Surface device types require the
  On/Off cluster with the **OffOnly** feature
  ([Cooktop.xml](https://github.com/project-chip/connectedhomeip/blob/master/data_model/1.3/device_types/Cooktop.xml),
  [SDK device XML](https://github.com/project-chip/connectedhomeip/blob/master/src/app/zap-templates/zcl/data-model/chip/matter-devices.xml)).
  In the On/Off cluster, `On` and `Toggle` are mandatory only when OffOnly is *not* set
  ([onoff-cluster.xml](https://github.com/project-chip/connectedhomeip/blob/master/src/app/zap-templates/zcl/data-model/chip/onoff-cluster.xml)).
  Under Matter conformance rules that makes them disallowed for OffOnly devices. HA still creates a
  generic "power" switch for cooktops, so ChorePilot maps only `switch.turn_off` for a Matter
  cooktop or cook surface and never `turn_on`.

---

## 4. Home Assistant services and states ChorePilot uses

Every call is `POST /api/services/<domain>/<service>` with `{"entity_id": "...", <fields>}`. Field
names are taken from each domain's `services.yaml` on HA `dev`
(`https://github.com/home-assistant/core/blob/dev/homeassistant/components/<domain>/services.yaml`).
State values are taken from the domain's `const.py`.

### 4.1 Services

| Service | Data fields | Source |
|---|---|---|
| `light.turn_on` | `brightness_pct`, `brightness_step_pct`, `color_temp_kelvin`, `rgb_color`, `transition`, `effect` | [light/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/light/services.yaml) |
| `light.turn_off` | `transition` | same |
| `climate.set_temperature` | `temperature` **or** `target_temp_low` + `target_temp_high` (range); optional `hvac_mode` | [climate/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/climate/services.yaml) |
| `climate.set_hvac_mode` | `hvac_mode` (`off`, `heat`, `cool`, `heat_cool`, `auto`, `dry`, `fan_only`) | same; modes in [climate/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/climate/const.py) |
| `climate.turn_off` / `turn_on` | none | same |
| `climate.set_fan_mode` / `set_preset_mode` / `set_humidity` | `fan_mode` / `preset_mode` / `humidity` | same |
| `water_heater.set_temperature` | `temperature`, optional `operation_mode` | [water_heater/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/water_heater/services.yaml) |
| `water_heater.set_operation_mode` | `operation_mode` (`off`, `eco`, `electric`, `performance`, `high_demand`, `heat_pump`, `gas`) | same; modes in [water_heater/\_\_init\_\_.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/water_heater/__init__.py) |
| `water_heater.set_away_mode` | `away_mode` | same |
| `valve.open_valve` / `close_valve` / `stop_valve` / `toggle` | none | [valve/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/valve/services.yaml) |
| `valve.set_valve_position` | `position` (0 to 100) | same |
| `switch.turn_on` / `turn_off` / `toggle` | none | [switch/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/switch/services.yaml) |
| `lawn_mower.start_mowing` / `pause` / `dock` / `stop` | none | [lawn_mower/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/lawn_mower/services.yaml) |
| `vacuum.start` / `pause` / `stop` / `return_to_base` / `locate` / `clean_spot` | none | [vacuum/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/vacuum/services.yaml) |
| `vacuum.clean_area` | `cleaning_area_id` (required) | same |
| `vacuum.set_fan_speed` | `fan_speed` | same |
| `lock.lock` / `unlock` / `open` | `code` (optional) | [lock/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/lock/services.yaml) |
| `cover.open_cover` / `close_cover` | `speed` (optional, recent `dev`) | [cover/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/cover/services.yaml) |
| `cover.set_cover_position` / `set_cover_tilt_position` / `stop_cover` | `position` / `tilt_position` | same |
| `fan.turn_on` | `percentage`, `preset_mode` | [fan/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/fan/services.yaml) |
| `fan.set_percentage` / `oscillate` / `set_direction` / `set_preset_mode` | `percentage` / `oscillating` / `direction` / `preset_mode` | same |
| `humidifier.set_humidity` / `set_mode` | `humidity` / `mode` | [humidifier/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/humidifier/services.yaml) |
| `alarm_control_panel.alarm_arm_home` / `_away` / `_night` / `_vacation` / `_custom_bypass` / `alarm_disarm` / `alarm_trigger` | `code` | [alarm_control_panel/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/alarm_control_panel/services.yaml) |
| `camera.snapshot` | `filename` | [camera/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/camera/services.yaml) |
| `camera.turn_on` / `turn_off` / `enable_motion_detection` / `disable_motion_detection` | none | same |
| `media_player.media_play` / `media_pause` / `media_stop` / `media_next_track` / `volume_set` / `select_source` / `play_media` | `volume_level`, `source`, `media_content_id` + `media_content_type` as applicable | [media_player/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/media_player/services.yaml) |
| `select.select_option` | `option` | [select/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/select/services.yaml) |
| `number.set_value` | `value` | [number/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/number/services.yaml) |
| `button.press` | none | [button/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/button/services.yaml) |
| `home_connect.set_program_and_options` / `change_setting` / `start_selected_program` | see [3.1](#31-home-connect-bsh) | [home_connect/services.yaml](https://github.com/home-assistant/core/blob/dev/homeassistant/components/home_connect/services.yaml) |

### 4.2 States and attributes read for status

| Domain | State values | Attributes ChorePilot reads | Source |
|---|---|---|---|
| `climate` | the HVAC mode (`off`, `heat`, `cool`, `heat_cool`, `auto`, `dry`, `fan_only`) | `current_temperature`, `temperature`, `target_temp_low`, `target_temp_high`, `hvac_action` (`off`, `preheating`, `heating`, `cooling`, `drying`, `fan`, `idle`, `defrosting`), `hvac_modes`, `min_temp`, `max_temp`, `target_temp_step`, `current_humidity` | [climate/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/climate/const.py) |
| `water_heater` | operation mode | `current_temperature`, `temperature`, `operation_list`, `min_temp`, `max_temp` | [water_heater/\_\_init\_\_.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/water_heater/__init__.py) |
| `valve` | `open`, `opening`, `closed`, `closing` | `current_position`; device class `water` / `gas` | [valve/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/valve/const.py) |
| `vacuum` | `cleaning`, `docked`, `idle`, `paused`, `returning`, `error` | `fan_speed`, `fan_speed_list` | [vacuum/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/vacuum/const.py) |
| `lawn_mower` | `mowing`, `docked`, `paused`, `returning`, `idle`, `error` | none | [lawn_mower/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/lawn_mower/const.py) |
| `lock` | `locked`, `unlocked`, `locking`, `unlocking`, `open`, `opening`, `jammed` | `code_format`, `changed_by` | [lock/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/lock/const.py) |
| `cover` | `open`, `opening`, `closed`, `closing` | `current_position`, `current_tilt_position` | [cover/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/cover/const.py) |
| `alarm_control_panel` | `disarmed`, `armed_home`, `armed_away`, `armed_night`, `armed_vacation`, `armed_custom_bypass`, `pending`, `arming`, `disarming`, `triggered` | `code_format`, `code_arm_required`, `changed_by` | [alarm_control_panel/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/alarm_control_panel/const.py) |
| `light`, `switch`, `fan`, `humidifier` | `on` / `off` | `brightness`, `color_temp_kelvin`; `percentage`; `humidity` | domain pages |
| any | `unavailable`, `unknown` | none | ChorePilot treats both as "cannot act": no command is sent |

---

## 5. Risk tiers

### 5.1 Tier definitions (ChorePilot policy)

| Tier | Meaning | Who may trigger | Confirmation | Schedules / external agents |
|---|---|---|---|---|
| **0 read** | status, temperatures, snapshots, alarms | any role, including kids and guests | none | allowed |
| **1 low** | lights, vacuum, mower, fans, media, blinds closing, **locking** a door, closing a water or gas valve, arming the alarm | adults; kids if the household allows it per device | none, or a one-tap undo | allowed |
| **2 water / heat / large energy** | dishwasher / washer / dryer start; oven preheat; microwave start; coffee brew; water-heater changes; irrigation; climate setpoints outside the comfort band (configured per home, for example 18 to 25 °C); EV charging start or current change; pool heater; fridge / freezer setpoints; battery or energy-management modes | adults with explicit confirmation of the exact action; a kid's request becomes a pending approval for a parent | explicit "yes" naming the device and action; vendor interlock must be satisfied | a schedule is allowed only if an adult created it; an external agent alone may *propose*, not execute |
| **3 security / safety** | unlock or open a door; open a garage door or gate; disarm the alarm; reopen the water main after a leak shutoff; open a gas valve; camera privacy changes (turn off, disable motion detection, enable recording); delete or change lock codes | owner only, plus a second factor (PIN / TOTP / passkey on the owner's device) | second factor every time | **never** from an external agent alone; **never** on a schedule unless the owner armed that schedule with a second factor |
| **blocked** | turn a cooktop / hob / cook surface on; set heat on an unattended appliance that the vendor does not allow remotely; bypass any vendor interlock | nobody | none | none |

### 5.2 Per-class, per-action tiers

| Class | Action | Tier |
|---|---|---|
| any | read status, attributes, history | 0 |
| light | on / off / brightness / colour | 1 |
| switch / plug | on / off (load must be declared; see [5.3](#53-never-do)) | 1 (2 if the declared load is a heat or motor load) |
| fan, air purifier, hood fan, humidifier | on / off / speed / mode / target humidity | 1 |
| vacuum | start / pause / stop / dock / clean area / fan speed | 1 |
| lawn mower | start / pause / dock / stop | 1 |
| media player | play / pause / volume / source | 1 |
| cover (blinds, shades) | close / stop / position | 1 |
| cover (blinds, shades) | open | 1 |
| cover (garage door, gate) | close | 1 |
| cover (garage door, gate) | open | 3 |
| lock | lock | 1 |
| lock | unlock / open (unlatch) | 3 |
| alarm panel | arm (home / away / night / vacation) | 1 |
| alarm panel | disarm | 3 |
| camera | snapshot | 0 |
| camera | turn off / disable motion / record on demand | 3 |
| climate | setpoint inside the comfort band; `hvac_mode` change between heat / cool / auto | 1 |
| climate | setpoint outside the comfort band; `turn_off` while the outdoor temperature is extreme | 2 |
| water heater | setpoint / operation mode / away mode / Matter boost | 2 |
| valve (irrigation zone) | open for a duration | 2 |
| valve (irrigation zone) | close | 1 |
| valve (water main) | close | 1 |
| valve (water main) | open / reopen | 3 |
| valve (gas) | close | 1 |
| valve (gas) | open | 3 |
| dishwasher / washer / dryer | start / delayed start | 2 |
| dishwasher / washer / dryer | pause / stop | 1 |
| oven / range | preheat or program start | 2 |
| oven / range | stop / turn off | 1 |
| microwave | start | 2 |
| microwave | stop | 1 |
| cooktop / hob / cook surface | turn on, raise power | **blocked** |
| cooktop / hob / cook surface | turn off, lower power | 1 |
| coffee maker | brew a beverage | 2 |
| coffee maker | power on / off, cup warmer | 1 |
| refrigerator / freezer | setpoint / super mode / vacation mode | 2 |
| refrigerator / freezer | door and temperature alarms | 0 |
| EV charger | start charging, current limit (clamped to the configured min/max amps) | 2 |
| EV charger | stop charging | 1 |
| pool / spa | pump on / off | 1 |
| pool / spa | heater setpoint / on | 2 |
| solar / battery / device energy management | mode or schedule change | 2 |
| smoke / CO alarm | self-test | 1 (siren) |
| smoke / CO alarm | mute | 3 |
| walk-in cooler (business) | read / alert | 0 |
| walk-in cooler (business) | setpoint changes | not exposed |
| business door access | lock | 1 |
| business door access | unlock / hold open | 3 |

### 5.3 Never do

- **Never turn a cooktop or hob on remotely.**
  - Matter cooktops and cook surfaces are OffOnly by device-type definition ([3.10](#310-matter-via-home-assistant-what-becomes-what)).
  - Home Connect publishes no cooktop programs ([3.1](#31-home-connect-bsh)).
  - Miele hobs have no start or power-on action
    ([capability table](https://developer.miele.com/docs/third-party-api/API_V1.1.x_capabilities_by_device.pdf)).
  - The appliance safety standard IEC 60335-2-6 (stationary cooking ranges, hobs, ovens) added
    "requirements for automatic regulation systems and remote automatic regulation systems for
    induction hobs (… 22.40, … Annex AA)" and "requirements for remote operation of ovens (22.51)" in
    its 7th edition, 2024 ([IEC 60335-2-6:2024 preview](https://cdn.standards.iteh.ai/samples/103407/f2993b324e2443e48bd1c7d23420eeef/IEC-60335-2-6-2024.pdf)).
    The normative text of 22.40 / 22.51 / Annex AA is paywalled, so the exact rule that a hob
    cannot be switched on remotely, and the conditions for remote oven operation, are
    **[unverified]**. ChorePilot follows the strictest reading: hob on is blocked, and an oven
    start is tier 2 and only through the vendor's own remote-start interlock.
- **Never bypass a vendor interlock.** ChorePilot never sets or simulates Home Connect
  `RemoteControlStartAllowed`, LG / SmartThings `remoteControlEnabled` or Miele `mobileStart`. It
  never sends a start while `LocalControlActive` is true or the door is open. If the interlock is
  off, the answer is "press Remote Start on the machine".
- **Never use a smart plug to power heat or water appliances.** That includes space heaters,
  ovens, kettles, irons, dishwashers, washers, dryers and water heaters. Cutting power mid-cycle
  can leave water in the machine or a heating element in an undefined state, and switching power
  *on* skips the appliance's own start logic. A plug's declared load is part of its device config.
  Plugs declared as `heater`, `appliance` or `unknown` are read-only, apart from tier-1 *off* for
  an emergency.
- **Never open an irrigation valve without a duration and a hard cap.** Rachio's API itself caps a
  zone run at 10800 s ([zone start](https://rachio.readme.io/reference/zonestart)).
- **Never reopen a water main or gas valve automatically** after a leak or gas event. That needs
  the owner plus a second factor (tier 3).
- **Never disarm, unlock or open on behalf of an external agent** (another bot, an MCP client or a
  webhook) without the owner's second factor, and never from a schedule the owner did not arm.
- **Never lower a water-heater setpoint below the household's configured floor.** Commonly cited
  Legionella guidance is to keep stored hot water at or above about 60 °C **[unverified]**: no
  primary source was checked for this document.
- **Never write HA state directly** (`POST /api/states/...`). It does not move the device.

---

## 6. Adding a device type

Device types are small plugins in `chorepilot/devtypes/`, one module per type: `light.py`,
`climate.py`, `valve.py` (sprinklers and irrigation), `coffee_maker.py`, `oven.py`, `fridge.py`,
`vacuum.py`, `lock.py`, `cover.py` (blinds / shades, and garage doors / gates), `alarm.py`
(alarm panels), `ev_charger.py`, `water_heater.py`, `air.py` (air purifiers, humidifiers and
dehumidifiers), `media_player.py`, and `program.py` for the dishwasher, washer and dryer (whose
programs and exact Bosch profiles stay in `machines.py`). Each module subclasses `DeviceType` and
declares:

- its capabilities, Home Assistant domain and Matter device types;
- its actions, each with typed parameters, limits and a safety tier from [section 5](#5-risk-tiers)
  (a tier can depend on the parameters: a thermostat setpoint inside the comfort band is tier 1,
  outside it tier 2);
- the status fields it reports (`state`, plus type-specific fields such as `target`, `current`,
  `remaining`, `door`, `remote_start_allowed`, `alarms`);
- its interlocks (door open, remote start not armed, already running, tank empty, jammed);
- the device events it emits (`started`, `finished`, `door_alarm` ...), which verify chores;
- its mappings to Home Assistant (domain, services and data fields from
  [section 4](#4-home-assistant-services-and-states-chorepilot-uses)), MQTT topics, Home Connect
  keys ([section 3.1](#31-home-connect-bsh)) and the built-in simulator.

Transport lives in `chorepilot/adapters/` (`simulated`, `ha`, `matter` = HA's Matter server,
`mqtt`, `homeconnect`). A new device type needs no new adapter when HA already exposes it. A new
adapter is only worth writing for a vendor API that HA cannot reach. The House reads the tier and
interlocks from the device type, and the policy engine decides, before any adapter is called. A
user plugin can also live in `~/.config/chorepilot/devtypes/<name>.py`.
