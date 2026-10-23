# Setting up Bosch Home Connect appliances

This guide walks through a Bosch dishwasher, washing machine and tumble dryer on the official
Home Connect API, end to end. The three models below are worked examples (sold in Singapore,
with close relatives in most markets); any Home Connect dishwasher, washer or dryer works the
same way, and `chorepilot setup devices` does most of this for you (device-flow login, then it
lists the appliances on your account and maps them).

> The recorded runs in section 5 still show `dishsoaker wash`: that command remains an alias
> of `chorepilot wash` (ChorePilot started as Dishsoaker3000).

| Nickname | Model | What it is | Home Connect |
|---|---|---|---|
| `dishwasher` | **SMV6ZCX42E** | Serie 6 fully-integrated dishwasher, 60 cm, 14 place settings | **Yes**, built in ([SG spec](https://www.bosch-home.com.sg/en/product/SMV6ZCX42E): "Connectivity: Home Connect", "Additional operational options: Remote Start, ...") |
| `washer` | **WAT286H9SG** | Serie 6 front loader, 9 kg, 1400 rpm, i-DOS | **Yes**, built in ([SG spec](https://www.bosch-home.com.sg/en/product/WAT286H9SG): "Connectivity: Home Connect", "Home Connect Features: Remote Monitoring and Control") |
| `dryer` | **WTW876H8SG** | Serie 6 heat-pump tumble dryer, 8 kg | **Yes**, built in ([SG spec](https://www.bosch-home.com.sg/en/product/WTW876H8SG): "Connectivity: Home Connect", "Type of control: Rotary control, touch control, Via app") |

All three example models are sold on Bosch Singapore's site, and all three have Wi-Fi and a **Remote Start**
button. That means the official Home Connect API can *start* each of them, not just watch them.
No ESP32 or internal-bus hacking is needed. (Fallback, should a Wi-Fi module ever die: the
BSH D-Bus route in [CONTROL.md §2.4](CONTROL.md) gives you **status only**, read-only. Starting
the machine still needs Home Connect.)

Research date: 2026-10-21. **[unverified]** marks claims that no primary source confirmed.
The sources are the Bosch SG product pages, the three user manuals (links below) and the
[Home Connect API docs](https://api-docs.home-connect.com/).

---

## 1. What each machine can do remotely

The exact profiles are in `chorepilot/machines.py`. `chorepilot control programs` prints them,
and once you are logged in it also asks each machine which programs it really offers.

### Dishwasher SMV6ZCX42E
Sources: [manual 9001910568](https://media3.bosch-home.com/Documents/9001910568_B.pdf) and
[quick reference guide 9001618009](https://media3.bosch-home.com/Documents/9001618009_B.pdf)
(durations).

| Say | On the machine | Home Connect key | Run time |
|---|---|---|---|
| `auto` (default) | Auto 45-65 °C | `Dishcare.Dishwasher.Program.Auto2` | 1:40-2:40 |
| `eco` | Eco 50 °C | `...Program.Eco50` | 3:55 |
| `intensive` | Intensive 70 °C | `...Program.Intensiv70` | 2:25-2:30 |
| `silence` | Silence 50 | `...Program.NightWash` **[unverified]** | 4:00 |
| `glass` | Glass 40 °C | `...Program.Glas40` | 1:40-1:45 |
| `short` | Express 60 °C (SG spec: "Short 60 °C") | `...Program.Kurz60` **[unverified]** | 1:29 |
| `quick` | Express 45 °C (SG spec: "Quick Wash 45 °C") | `...Program.Quick45` **[unverified]** | 0:35 |
| `machine_care` | Machine Care 70 °C (empty machine) | `...Program.MachineCare` | 2:10 |

- **Options:**
  - `extra_dry` (`Dishcare.Dishwasher.Option.ExtraDry`);
  - `intensive_zone` (`...IntensivZone`);
  - `speed_perfect` (SpeedPerfect+, `...VarioSpeedPlus`);
  - `delay_minutes` (`BSH.Common.Option.StartInRelative`, up to 24 h).
- **Not on this model:** half load and Hygiene Plus. The Favourite program can only be run
  at the machine.
- **Child lock:** none. The SG spec says "Child safety devices: No".
- **Remote start:** select a program, then press the Remote Start button. Basic setting
  `rc:01` (the default) means you press it for every run, and opening the door switches it
  off. With `rc:02` remote start stays on permanently. `rc:00` disables it. The rc:02
  setting only appears after the machine has been paired with the app (manual §16.3).

### Washing machine WAT286H9SG
Sources: [SG spec](https://www.bosch-home.com.sg/en/product/WAT286H9SG) and
[manual 9001259425](https://media3.bosch-home.com/Documents/9001259425_D.pdf). Bosch prints
the run times only in a per-model supplement that is not online, so the simulator's times
are estimates.

| Say | On the machine | Home Connect key |
|---|---|---|
| `cotton` (default) | Cottons, cold-90 °C | `LaundryCare.Washer.Program.Cotton` |
| `easy_care` | Easy-care (synthetics), cold-60 °C | `...Program.EasyCare` |
| `mix` | Mix | `...Program.Mix` |
| `delicates` | Delicates/Silk | `...Program.DelicatesSilk` |
| `wool` | Wool / hand wash | `...Program.Wool` |
| `allergy_plus` | AllergyPlus / Hygiene | `...Program.Sensitive` **[unverified]**: real devices also report `HygienePlus` |
| `curtains` | Curtains | `...Program.Curtains` |
| `duvet` | Duvet | `...Program.DownDuvet.Duvet` |
| `super_quick` | Super 15'/30' | `...Program.Super153045.Super1530` **[unverified]**: could be `Super15` / `Super30` |
| `rinse` | Rinse | `...Program.Rinse.RinseSpinDrain` **[unverified]** |
| `spin_drain` | Spin / Drain | `...Program.Spin.SpinDrain` **[unverified]** |
| `drum_clean` | Drum Clean 90 °C (empty drum) | `...Program.DrumClean` |

- **Options:**
  - `temperature`: `cold, 20, 30, 40, 60, 90` (`LaundryCare.Washer.Option.Temperature`,
    values `...EnumType.Temperature.Cold/GC20...GC90`);
  - `spin`: `0, 400, 600, 800, 1200, 1400` (`...Option.SpinSpeed`, values `...SpinSpeed.Off/RPM400...RPM1400`);
  - `finish_in_minutes`: Bosch's **"Ready in"**, `BSH.Common.Option.FinishInRelative`, set in
    30-minute steps, up to 24 h;
  - `speed_perfect` / `eco_perfect` (`LaundryCare.Common.Option.VarioPerfect`);
  - `i_dos` / `i_dos_softener` (`...IDos1Active` / `IDos2Active`).

  The temperature and spin lists come from the control panel in the manual (p.21). The
  highest temperature and spin each program allows are enforced by the machine itself (it
  answers 409). ChorePilot checks only the two limits the SG spec states: cotton up to
  90 °C, easy-care up to 60 °C.
- **At the machine only:** Water&Rinse+, Easy-Iron, Prewash, Rinse Stop and the memory program.
- **Child lock:** yes; hold the lock key for 3 s. ChorePilot reads it through
  `BSH.Common.Setting.ChildLock` and shows it in status. A program keeps running with the
  lock on.
- **Remote start** (manual p.27):
  - Arming: close the door **and the detergent drawer**, check that the Wi-Fi symbol is lit,
    then briefly press Remote Start. Holding the button 3 s opens the Home Connect settings
    instead.
  - It switches off when the door or the drawer opens, after a power failure, **when a
    program ends**, when you press Start/Pause or Remote Start again, and when the machine
    is switched off.
  - The manual also says: "You cannot make changes in the Home Connect app while the
    appliance is in operation." So whether pause and resume work over the API on this
    model is **[unverified]**.

### Tumble dryer WTW876H8SG
Sources: [SG spec](https://www.bosch-home.com.sg/en/product/WTW876H8SG) and
[manual 9001393265](https://media3.bosch-home.com/Documents/9001393265_A.pdf). The run times
are from p.56: cottons, 8 kg spun at 1400 rpm, take **128 min to cupboard dry and 98 min to
iron dry**.

| Say | On the machine (max load) | Home Connect key |
|---|---|---|
| `cotton` (default) | Cottons (8 kg) | `LaundryCare.Dryer.Program.Cotton` |
| `synthetic` | Synthetics (3.5 kg) | `...Program.Synthetic` |
| `mix` | Daily Wash / Mix (3 kg) | `...Program.Mix` |
| `delicates` | Delicates (2 kg) | `...Program.Delicates` |
| `wool_finish` | Wool Finish (0.5 kg) | `...Program.WoolFinish` (seen on real devices, not in the public docs) |
| `time_cold` / `time_warm` | Time program cold / warm (3 kg) | `...Program.TimeCold` / `TimeWarm` + `duration_minutes` 20-210 in 10-min steps (`BSH.Common.Option.Duration`) |
| `shirts` | Shirts/Blouses (1.5 kg) | `...Program.BusinessShirts` |
| `duvet` | Duvet, synthetic filling (2.5 kg) | `...Program.Blankets` **[unverified]** |
| `down` | Down Wear (1.5 kg) | `...Program.DownFeathers` |
| `sportswear` | Sportswear: membrane/outdoor (1.5 kg) | `...Program.Outdoor` **[unverified]**: could be `Outdoor.Sportswear` |
| `towels` | Towels (6 kg) | `...Program.Towels` |
| `super40` | Super Quick 40' (2 kg) | `...Program.Super40` |
| `allergy_plus` | AllergyPlus (4 kg) | `...Program.Hygiene` **[unverified]** |

- **`dryness`** (`LaundryCare.Dryer.Option.DryingTarget`):
  - `iron` = Iron Dry;
  - `cupboard` = Cupboard Dry;
  - `cupboard_plus` = Cupboard Dry Plus.

  **This model has no "Extra Dry" target** (manual p.20 and p.25). So when you say
  *"extra dry"*, ChorePilot uses `cupboard_plus`, which the API describes as "absolutely dry".
- **`finish_in_minutes`** (Ready in, `FinishInRelative`): up to 24 h.
- **At the machine only:** Anti-Crease 60/120 min, Low Heat, the +1/+2/+3 dryness
  fine-tune and quiet mode. None of these has a documented API option.
- **Child lock:** yes; hold the lock key for 3 s.
- **Remote start** (manual p.25): close the door, then briefly press Remote Start. It switches
  off when the door opens, after a power failure, when a program ends, and when you press
  Start/Pause or Remote Start again.

**About the [unverified] keys.** For each one, the Home Connect description matches the
manual's description, but nobody has confirmed it on these exact machines. After you log in,
run `chorepilot control programs`. It asks each machine (`GET /programs/available`) and marks
every program `[on device]` or `[NOT reported by the device]`. If a key is wrong, fix it in
`control.json` without touching the code:
```json
"washer": {"model": "WAT286H9SG", "program_keys": {"super_quick": "LaundryCare.Washer.Program.Super153045.Super30"}}
```

---

## 2. Home Connect by region (example: Singapore)

- **The app** is available in Singapore. Bosch SG's
  [Home Connect page](https://bosch-home.com.sg/highlights/home-connect) links the App Store
  and Google Play versions. Each spec sheet adds: "Some of the features displayed can be
  accessed only if the device is connected to a Home Connect Account **and a SingleKey ID**
  account via the Internet."
- **The API endpoint** is `https://api-docs.home-connect.com/general/`:
  "api.home-connect.com **or api.home-connect.cn** - This domain has to be used if you want
  to create an application for China." Singapore is **not** China, so you use the default
  `https://api.home-connect.com`. You don't need to set a region.
- **No Home Connect at all?** Bosch SG's customer care hotline is +65 6751 5000.

---

## 3. Setup, step by step

### 3.1 Pair each appliance in the Home Connect app
1. Install **Home Connect** and sign in with a SingleKey ID.
2. Add each appliance:
   - **Dishwasher:** the app shows a QR code / pairing flow (manual §16.1).
   - **Washer and dryer:** hold **Remote Start for 3 s** to open the Home Connect settings,
     then follow the app (WPS or manual pairing). Press Remote Start briefly to leave.
3. In the app, check that each one shows as connected and that **remote control** is
   allowed in the appliance's Home Connect settings. That flag is
   `BSH.Common.Status.RemoteControlActive`; ChorePilot refuses to start anything while it
   is off.

### 3.2 Decide how remote start should work
- **Dishwasher:** leave basic setting `rc:01`. You load it, pick a program and press Remote
  Start, and from then on ChorePilot can start it. Only switch to `rc:02` (permanently on)
  if you understand that the machine can then be started whenever its door is shut.
- **Washer and dryer:** there is no permanent mode. You press Remote Start after loading,
  every time, and opening the door cancels it. That is the safety design, and ChorePilot
  never tries to get around it.

### 3.3 Register a Home Connect developer app (once)
1. Sign in at <https://developer.home-connect.com>, register an application, and choose
   **OAuth flow: Device Flow**.
2. Copy the **Client ID**. Under Applications, use your **own** Home Connect account email
   as the user.

### 3.4 Install ChorePilot and configure the three appliances
The quickest path is the wizard: `chorepilot setup devices`, choose Home Connect, log in, and it
maps every appliance on your account. By hand:
```bash
pipx install chorepilot                            # stdlib only (or `pip install -e .` from a checkout)
chorepilot control init bosch                      # writes ~/.config/chorepilot/control.json
chorepilot control init --backend simulated --force   # (to rehearse first without hardware)
```
`init` writes the `bosch` preset:
```json
{
  "backend": "homeconnect",
  "default": "dishwasher",
  "appliances": {
    "dishwasher": {"model": "SMV6ZCX42E", "aliases": ["dishes", "dish washer"]},
    "washer":     {"model": "WAT286H9SG", "aliases": ["washing machine"]},
    "dryer":      {"model": "WTW876H8SG", "aliases": ["tumble dryer", "drier"]}
  }
}
```
ChorePilot finds each machine on your account by type, and then by model number (the `vib`
field in `GET /api/homeappliances`). If you own two of the same type, pin each one with
`"haId": "BOSCH-WAT286H9SG-..."`. `chorepilot control status --json` shows the haIds.

### 3.5 Log in (device flow)
```bash
export HOME_CONNECT_CLIENT_ID=...                  # from 3.3
chorepilot control login --backend homeconnect
# Open https://api.home-connect.com/security/oauth/device_verify?user_code=... and enter code ABCD-EFGH
```
Approve the scopes `IdentifyAppliance Monitor Control Settings`. The token is saved to
`~/.config/chorepilot/homeconnect-token.json` with mode 0600, outside the repo. It is
refreshed automatically: access tokens last 24 h, and Home Connect allows 100 refreshes a
day.

### 3.6 Check that it can see everything
```bash
chorepilot control status
#   dishwasher: ready, door closed, remote start OFF
#   washer: ready, door closed, remote start OFF, child lock off
#   dryer: inactive, door closed, remote start OFF, child lock off
chorepilot control programs                        # profile vs. what each machine reports
chorepilot control start cotton -a washer --option temperature=40 --dry-run   # exact PUT body
```
`--dry-run` prints the exact request without sending it, for example:
```json
{"method": "PUT", "url": "https://api.home-connect.com/api/homeappliances/{haId}/programs/active",
 "body": {"data": {"key": "LaundryCare.Washer.Program.Cotton", "options": [
   {"key": "LaundryCare.Washer.Option.Temperature", "value": "LaundryCare.Washer.EnumType.Temperature.GC40"}]}}}
```

### 3.7 Talk to it
Run the model on any box on the LAN (`chorepilot serve --model <your-model>.gguf --host 0.0.0.0`),
or let `wash` start one locally:

```bash
chorepilot wash "wash my dishes"
chorepilot wash "start the washer on cotton 40"
chorepilot wash "dry the clothes extra dry when the wash finishes"
```

What happens:

1. **"wash my dishes"** becomes `start_program(appliance=dishwasher, program=auto)`. The
   bridge checks that the dishwasher is online, idle, has its door closed and has remote
   start on. Then it asks *"Start auto on the dishwasher (Bosch SMV6ZCX42E, homeconnect)?
   It uses water and heat. [y/N]"*.
2. **"start the washer on cotton 40"** becomes
   `start_program(appliance=washer, program=cotton, temperature=40)`, with the same checks
   and its own y/N.
3. **"dry the clothes extra dry when the wash finishes"** becomes
   `start_when_done(wait_for=washer, then_start=dryer, program=cotton, dryness=cupboard_plus)`.
   It asks once, now: *"When the washer finishes, start the dryer ... on cotton
   (dryness=cupboard_plus)? It starts only after you load it and press its Remote Start."*
   A detached watcher then takes over:
   - the washer finishes, and you get *"The washing is done. Move it into the dryer, close
     the door and press Remote Start on the dryer: it will then start cotton
     (dryness=cupboard_plus)."*;
   - you move the laundry and press **Remote Start** on the dryer;
   - the watcher sees remote start armed **after** the hand-off began, re-checks every
     interlock and starts the dryer. You get *"The dryer started ..."* and later *"The
     laundry is dry."*

   An empty dryer that was armed earlier doesn't count: loading it means opening the door,
   which disarms it. If the washer is cancelled or fails, the dryer is not started and you
   are told why. If you never press Remote Start, the watcher gives up after 3 h and tells
   you. `chorepilot control chains` lists pending chains; `chorepilot control cancel -a dryer`
   (or asking the model to cancel the dryer) removes one.

Notifications go to the desktop, to `$CHOREPILOT_NOTIFY_URL` (for example an
[ntfy](https://ntfy.sh) topic for your phone) and to HA's `notify.notify` on the `ha` backend.

### 3.8 Rehearse without water
```bash
chorepilot control init --backend simulated --force
CHOREPILOT_SIM_SPEED=120 chorepilot wash "start the washer on cotton 40"
chorepilot wash "dry the clothes extra dry when the wash finishes"
chorepilot control sim -a dryer load      # play the human: open, load, close, press Remote Start
chorepilot control status
```

### 3.9 Chores: who empties the dishwasher

`control.json` works on its own. To add the chore board, describe the house in
`~/.config/chorepilot/house.json`; the same three machines are a preset. If `house.json` doesn't
exist yet, ChorePilot reads `control.json` instead.

```bash
chorepilot setup                                   # you become the owner; members, model, devices
# or by hand:
chorepilot init bosch                              # house.json: the three Bosch machines, Home Connect
chorepilot people add Alex --role owner && chorepilot people set-pin Alex
chorepilot people add Sam --role adult
chorepilot chores add "Dishes" --schedule "daily at 20:00" --rotation Alex,Sam --rotate week \
    --verify dishwasher.finished --followup "Empty the dishwasher"
chorepilot chores add "Empty the dishwasher" --rotation Sam,Alex
chorepilot chores add "Laundry" --schedule "every saturday" --rotation Alex,Sam \
    --verify dryer.finished --followup "Fold the laundry"
chorepilot chores add "Fold the laundry" --rotation Sam,Alex
chorepilot events push &                           # Home Connect event stream (or `events watch -i 300`)
chorepilot ask "who has the dishes this week?" --as Sam
chorepilot ask "wash my dishes" --as Alex          # still asks y/N; remote start rules unchanged
```

Every poll reads each machine (a few requests each), so mind the Home Connect budget of 1000
requests a day: polling three machines every 2 minutes would blow through it, while every 5
minutes or more stays well inside if the machines are idle most of the day. `notify_when_done` already polls the running machine and updates the chores
when it ends. `chorepilot events push` (and `chorepilot run`) use Home Connect's server-sent
event stream instead, with a slow safety poll.

---

## 4. Safety notes

- **A person arms every remote start, at the machine.** On the washer and dryer it lasts for
  one load: it switches off when the door opens and when the program ends. ChorePilot
  refuses to start anything whose remote start is off, and it never tries to get around
  that.
- **A person says yes.** `start_program`, `resume` and `start_when_done` need a *y* at the
  terminal. A run with no terminal and no `--yes` counts as *no*. A chained dryer start is
  confirmed when you schedule it, and then needs your Remote Start press on top of that.
- **Interlocks.** ChorePilot won't start an appliance that is:
  - offline;
  - already running, paused or delayed;
  - open;
  - being operated at the panel (`LocalControlActive`);
  - set to not allow remote control in its Home Connect settings.
- **Loads.** The bridge doesn't know what is inside a machine.
  - Machine Care and Drum Clean are for **empty** machines.
  - Don't use Wool Finish or Time Warm on items whose care label says "do not tumble dry".
  - Stay within the dryer's per-program maximum loads (the table above).
- **Heat-pump dryer.** It contains R290, a flammable refrigerant (manual p.57). Clean the fluff
  filter regularly, and don't run it unattended if the filter or the condenser is
  blocked. (The machine reports both.)
- **No smart plugs as switches.** Bosch: "Never equip the appliance with an external switching
  device, e.g. a timer or remote control." Use smart plugs for power monitoring only.
- **Rate limits.**
  - Home Connect allows 1000 requests a day, 50 a minute and 5 program starts a minute
    ([rate limiting](https://api-docs.home-connect.com/general/#rate-limiting)).
  - ChorePilot's own limit is 6 starts per 10 min across the house, with 60 s between starts
    of the *same* appliance ([CONTROL.md, rate limits](CONTROL.md#31-rate-limits)).
  - Watchers poll rarely while the end is far off (at most every 10 min, then every 30 s
    near the end). A 3 h wash plus the hand-off costs a few hundred requests.
- **Secrets** live only in the environment or in `~/.config/chorepilot` (0600). The model
  sees outcomes, never URLs or tokens.

---

## 5. A real rehearsal

This ran on an M4 Mac on 2026-10-21. The model is a Qwen2.5-1.5B-Instruct Q4_K_S GGUF
(0.94 GB, ctx 8192). The backend is simulated with these three profiles, at
`CHOREPILOT_SIM_SPEED=120`. Each y/N was a real prompt, answered on a pty.

```
$ dishsoaker wash "wash my dishes"
  [tool call ] start_program({"appliance": "dishwasher", "program": "auto"})
  ?? Start auto on the dishwasher (Bosch SMV6ZCX42E, simulated)? It uses water and heat. [y/N] y
  [bridge    ] {"ok": true, "state": "running", "remaining": "2 h 39 min"}
Your dishes are now being washed. They will be ready in approximately 2 hours and 39 minutes.
  [spin      ] 3.8s, 2 model turn(s), 74.5 tok/s generation

$ dishsoaker wash "start the washer on cotton 40"
  [tool call ] start_program({"appliance": "washer", "program": "cotton", "temperature": "40"})
  ?? Start cotton with {'temperature': '40'} on the washer (Bosch WAT286H9SG, simulated)? It uses water and heat. [y/N] y
  [bridge    ] {"ok": true, "state": "running", "remaining": "2 h 49 min"}
The washer is now running on cotton at 40°C. It will take approximately 2 hours and 49 minutes.

$ dishsoaker wash "dry the clothes extra dry when the wash finishes"
  [tool call ] start_when_done({"wait_for": "washer", "then_start": "dryer", "program": "cotton", "extra_dry": true})
  ?? When the washer finishes, start the dryer (Bosch WTW876H8SG) on cotton (dryness=cupboard_plus)? It starts only after you load it and press its Remote Start. It uses heat. [y/N] y
  [bridge    ] {"ok": true, "scheduled": "dryer cotton after the washer", "after_remaining": "2 h 39 min", ...}

# background watcher (watch.log):
  [notify    ] The washing is done. Move it into the dryer, close the door and press Remote Start on the dryer: it will then start cotton (dryness=cupboard_plus).
$ dishsoaker control sim -a dryer load          # the human: open, load, close, Remote Start
  [notify    ] The dryer started: cotton (dryness=cupboard_plus), about 2 h 23 min.
  [notify    ] The laundry is dry.
```
