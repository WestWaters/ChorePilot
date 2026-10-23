# UI Research: Smart-Home, Chore and Agent-Chat Interfaces

Competitive research for the ChorePilot web dashboard redesign (device tiles by room, "Ask the house"
command bar, family chore board, approvals queue, activity timeline, browser onboarding). Goal: beat the
best interfaces on design, style and ease of use, with phones as the primary target.

Method: official product pages, documentation, release notes and App Store listings, plus review and
forum complaints as evidence of weaknesses. Where a number comes from a third-party extraction rather
than an official spec, it is marked "(extracted)". Observations read off official marketing screenshots
are marked "(observed)". Research date: 2026-10-22.

Reference screenshots (third-party images, kept outside the repo) live in the session scratchpad under
`competitors/`; see the final section.

---

## 1. Apple Home (iOS 16 redesign, refined through iOS 18 and iOS 26)

### Patterns
- **Home tab = one scrolling page**: title ("My Home"), then a horizontal row of **category pills that
  double as status** ("Climate 68-72", "Lights 3 On", "Security 1 Unlocked", "Speakers & TVs 1 Playing",
  "Water", "Energy"), then Cameras, Scenes, then one block per room. Categories open a filtered view
  grouped by room. https://www.apple.com/home-app/ (observed), https://www.macstories.net/stories/everything-new-coming-to-the-home-app-in-ios-16-and-macos-ventura/
- **Tiles are oblong, two per row on iPhone**, not the old 3-up squares; each tile = round icon chip +
  name + one state line ("Sconces / On", "Shades / Closed", "Front Door / Locked"). https://www.macstories.net/?p=70314
- **Two sizes per tile**: small (short rectangle) and large (taller rectangle); toggled in "Edit Home
  View" via a resize handle. https://macrumors.com/how-to/reorganize-home-view-home-app
- **Active state = fill change**: an "on" tile turns opaque light/white with a coloured icon (warm yellow
  for lights, teal for fans); "off" tiles stay translucent dark glass over the wallpaper. One accent per
  device category, never a page-wide brand colour. (observed on apple.com/home-app)
- **Split hit targets**: tapping the icon toggles; tapping the rest of the tile opens the control sheet.
- **Rooms reorder by drag** and sync across iPhone/iPad/Mac; iPad and Mac use a sidebar (Home,
  Automation, rooms). https://www.macstories.net/?p=70314
- **iOS 26**: Liquid Glass floating tab bar with three items (Home, Automation, Discover); a header strip
  carries passive context ("Grid Forecast: Cleaner"). https://bgr.com/2176790/
- **Glanceable "now"**: the category pills are the status bar; no separate summary card needed.
- **Onboarding**: camera scan of a setup code; iOS 26.2 adds pairing several Matter accessories from one
  code. https://bgr.com/2176790/

### Weaknesses
- The icon-toggles / body-opens split is invisible; owners discover it by accident and call it
  "non-obvious". https://forums.macrumors.com/threads/homekit-light-switching.2373369/
- Names over roughly 9-10 characters truncate; passive sensors are "always gray"; navigation needs
  "more tapping and swiping than most apps"; zones/groups are hidden. https://www.macstories.net/stories/its-time-for-a-complete-home-app-makeover/
- Settings buried behind long-press then extra taps. (same source)
- No conversational control in the app itself; Siri is a separate surface with no typed transcript of
  what it did.

---

## 2. Google Home (Gemini-era redesign, rolled out from 2025-09-29)

### Patterns
- **Three tabs**: Home, Activity, Automations (down from five: Favorites, Devices, Automations, Activity,
  Settings). Settings and Inbox moved into the profile avatar menu. https://9to5google.com/2025/09/21/google-home-ai-revamp-nervous-optimistic/,
  https://chromeunboxed.com/the-new-google-home-app-is-finally-here-faster-smarter-and-redesigned-for-gemini/
- **"Ask Home" bar pinned in the header of every screen**: a full-width pill with a home icon and
  placeholder "Ask <home name>", plus a "+" and the avatar beside it. Natural-language control, automation
  creation and history search all start here. (observed, App Store screenshots) https://apps.apple.com/us/app/google-home/id680819774
- **Swipeable chips under the bar** on the Home tab: Favorites (pill with label when active), then
  icon-only chips for Devices, Cameras, Lights, Wi-Fi. (observed)
- **Tiles**: mixed sizes in a 2-column grid. Live camera = full-width; small 2-up tiles for lock and
  light; thermostat = full-width tall tile with large number ("68 / Comfort") and -/+ buttons. **Active
  tiles are filled with a category tint** (lock blue, light yellow, heat peach); inactive are neutral.
  Weather and air quality sit as a footer row. (observed)
- **Activity tab = daily "Home Brief"**: an AI-written paragraph summarising the day, with "See more",
  thumbs up/down, then a chronological event list with camera thumbnails; filter chips (Devices, Events,
  Date) at the top. (observed)
- **Automations tab**: "Upcoming" horizontally scrolling cards with large times ("10:00 AM",
  "7:30 PM"), then "Your automations" list rows each with a play button. (observed)
- Performance claims: 70% faster startup, 80% fewer crashes, live view 30% faster. https://chromeunboxed.com/the-new-google-home-app-is-finally-here-faster-smarter-and-redesigned-for-gemini/

### Weaknesses
- The Ask Home field sits at the top but opens a chat whose input is at the bottom: reviewers call it
  "terrible design" (the input jumps). https://9to5google.com/2025/09/21/google-home-ai-revamp-nervous-optimistic/
- AI-first focus worries owners whose basic control was already unreliable; LLM responses are slow,
  especially on speakers. https://9to5google.com/2025/09/21/google-home-ai-revamp-nervous-optimistic/,
  https://www.webpronews.com/google-finally-fixes-the-annoying-little-things-about-its-smart-home-and-gemini-gets-the-keys/
- Long history of crashes, slowness and scattered features that forced a public apology before the
  rebuild. https://www.phonearena.com/news/the-google-home-app-is-getting-a-much-needed-gemini-powered-redesign_id174538
- Cloud dependent: nothing works locally when the internet drops.

---

## 3. Samsung SmartThings (One UI 8/9-era redesign, 2026)

### Patterns
- **Floating pill tab bar with five items**: Home, Devices, Life, Routines, Menu. https://www.sammobile.com/news/massive-samsung-smartthings-update-one-ui-9-early-look/
- **Compact device cards by default** (more per screen), user-resizable; tapping the card's icon toggles
  power without opening controls. https://www.techadvisor.com/article/3207738/samsung-smartthings-app-getting-huge-free-update.html
- **Device detail = big state slab**: the light page leads with a full-width coloured "On" bar and a
  brightness slab ("26%") as the slider itself, then colour presets, temperature, timers. (observed,
  App Store screenshots) https://apps.apple.com/us/app/smartthings/id1222822904
- **Time-of-day live wallpaper** behind Home (morning/day/evening/night) and a "SmartThings Now" suggestion
  card at the top. https://www.sammobile.com/news/massive-samsung-smartthings-update-one-ui-9-early-look/
- Energy view: money first ("$4.14 this month") then kWh chart. (observed)

### Weaknesses
- Version 1.8.51.30 (Oct 2026): tapping a device no longer opens full controls; users must long-press
  then "Expand". 84 of 271 negative reviews traced to it; also power buttons removed from cards, names
  truncated instead of wrapping, offline-devices tile removed, AI suggestions that cannot be turned off.
  https://unstar.app/blog/smartthings-update-cant-open-devices-tv-remote-gone-reviews-2026
- With many devices it is hard to find the right one ("no names, only icons"); slow load/refresh.
  https://community.smartthings.com/t/why-so-many-negative-reviews/10665
- Promotional "Now"/Explore content competes with the user's own devices.

---

## 4. Home Assistant (sections view, tile card, badges, Assist; 2024.7 through 2026)

### Patterns
- **Sections view is the default** (since 2024.11). Each section is a 4-column grid; "precise mode"
  gives **12 columns** so 2, 3, 4 or 6 controls align side by side. https://www.home-assistant.io/blog/2024/11/06/release-202411/
- **Grid metrics**: row height **56px** (down from 70px), gutter **8px**, tile icon **36px** (from 40px),
  feature row **42px**. https://www.home-assistant.io/blog/2024/07/26/dashboard-chapter-2/
- **Tile card anatomy**: icon (round chip), name, state ("Floor lamp / 70%", "Blinds / Open - 100%",
  "Downstairs / Comfort - 20.8 C"), optional badge on the icon, optional **features** (brightness bar,
  -/+ target temperature, toggles) either below or inline. https://www.home-assistant.io/dashboards/tile/
- **Interaction defaults**: tap card body = more-info dialog; tap icon = toggle (if togglable) else
  more-info; hold = configurable. Since 2025.3 **a circular background behind the icon signals that the
  icon is actionable**; info-only tiles get no circle. Hover animation, tap feedback, Tab/Shift+Tab
  keyboard support. https://www.home-assistant.io/blog/2025/03/05/release-20253/
- **Colour**: tile colour defaults to the entity's state/domain/device class (lights warm yellow/orange,
  climate orange, covers purple); a light's icon chip takes the bulb's actual colour. (observed on tile
  docs image) https://www.home-assistant.io/dashboards/tile/
- **Section headers carry room context**: "Living room" with temperature and humidity inline on the right.
  (observed)
- **View header + badges**: title and welcome text (Markdown/templates) with badge chips above, beside or
  below; badges have per-user, per-screen-size and per-state visibility. Badges were rebuilt because the
  2015 round design was "bulky" and users preferred Mushroom chips. https://www.home-assistant.io/blog/2024/07/26/dashboard-chapter-2/
- **Design principles stated by the team**: every control card has one clear primary action; interactive
  elements show affordance; features beat standalone icon buttons; avoid clutter. (same source)
- **New default Overview (2026)**: Favorites (about 8 most-used devices), Summaries by type (Lights,
  Climate, Security, Media) plus weather, and Areas for rooms. https://www.pcworld.com/article/3053761/home-assistants-default-dashboard-got-a-major-glow-up.html
- **Assist**: chat dialog opened from the dashboard header; streamed LLM responses (watch the answer
  arrive); "Ask question" action lets automations ask the user a follow-up. https://alternativeto.net/news/2025/7/home-assistant-2025-7-brings-ask-question-to-assist-redesigned-area-card-and-sub-entries
- **Area card** redesigned to look like a tile: compact (icon + area name) or detailed with camera and
  light/fan controls. (same source)

### Weaknesses
- The old default dashboard "dumps every single entity" on one page; building good dashboards is
  "daunting", so many households run workarounds. https://home-assistant.io/blog/2024/11/15/roadmap-2024h2/
- The person who built it understands it; family members "just want to turn on the lights". Making it
  usable for family and guests is "a continued source of frustration"; fine-grained per-person access is
  a long-standing request. (same source)
- Editing a default dashboard means "taking control", after which it stops auto-updating, an irreversible
  fork. https://www.home-assistant.io/dashboards/tile/
- Admin-panel heritage: sidebar of developer tools, entity IDs, YAML; Assist is a side feature, not the
  primary way in.

---

## 5. Homey (Athom)

### Patterns
- **Home screen**: home name + weather line ("It's 22 C and sunny") + avatar, a big weather card, then
  "Favorite Devices" as small square tiles (icon, name, state), "Moods"/Flows as pill buttons with a play
  icon, and a media card. Five-item bottom bar. (observed) https://apps.apple.com/us/app/homey/id1435800024
- **Flows read as sentences**: When / And / Then stacked cards ("<person> comes home", "It's after sunset",
  "Send Welcome!"). Approachable for non-technical members. (observed)
- **Family & Friends**: per-person access, "Control who gets access, and when". (observed)
- **Dashboards** (public beta 2024-09, then GA): custom panels with 25 Homey widgets plus third-party
  widgets. https://t3.com/home-living/smart-home/homey-debuts-new-dashboards-feature-but-not-all-homey-users-can-try-it

### Weaknesses
- Dashboards "got shipped and then kind of forgotten": no new widget types, layout flexibility or visual
  improvements; Advanced Flows still web-only (no phone/tablet); icons do not animate with state (a
  shutter icon that does not show the blind moving). https://community.homey.app/t/is-homey-going-a-bit-quiet-on-the-ui-side-am-i-the-only-one-feeling-this/155452
- Reviews cite community-maintained apps breaking and devices dropping support. https://apps.apple.com/us/app/homey/id1435800024

---

## 6. Sweepy

### Patterns
- **Rooms with "dirtiness" bars**: every task row shows a horizontal bar (green to red) plus a playful
  label ("Good job!", "What's that smell?") and "Last cleanup: <date> / Every 7 days". (observed)
  https://apps.apple.com/us/app/sweepy-home-cleaning-schedule/id1498897320
- **Daily schedule** with an "Effort points" progress bar ("0 out of 5"); **leaderboard** (Week / Month /
  All time) with a podium of three avatars. https://sweepy.com/
- **Work approval** for kids: kid marks done, adult reviews and awards points (badge count on "Work
  Approval" row). https://www.commonsensemedia.org/app-reviews/sweepy-home-cleaning-schedule
- Cute, illustrated, saturated blue; FAB "+" for new task.

### Weaknesses
- Effort points feel unfair (a long outdoor job scores like a quick wipe); cannot assign tasks to a
  specific person day by day; no editing of completed tasks; only a "done" button, no quality rating;
  family sharing behind premium. https://justuseapp.com/en/app/1498897320/sweepy-home-cleaning-schedule/reviews
- Notifications interrupt music playback. (same source)

---

## 7. OurHome

### Patterns
- Points per chore, rewards unlocked at point thresholds, plus shared grocery and household-needs lists
  with sortable categories. https://justuseapp.com/en/app/879717020/ourhome-chores-and-rewards/reviews

### Weaknesses
- Must toggle back and forth for chores to display correctly; manual sort order resets daily; reminders
  disappear; generally "buggy". (same source)

---

## 8. Skylight (Calendar + Chore Chart)

### Patterns
- **Per-person columns**, each with a colour and avatar; chores listed under each person; kids check off
  their own tasks on the wall display or phone. https://myskylight.com/how-to-manage-chores-and-family-tasks-with-skylight-calendar
- **Emoji as chore names for pre-readers**; full-list completion triggers an emoji burst celebration.
  https://www.cubbyathome.com/skylight-calendar-review-80042154
- Stars accumulate toward a parent-defined reward. https://greenlight.com/learning-center/reviews/skylight-calendar-review

### Weaknesses
- Parents must manually grant rewards, and many stop after a few days; stars do not connect to real
  allowance; rewards require a ~$79/yr subscription; only works if someone keeps the list fed.
  https://greenlight.com/learning-center/reviews/skylight-calendar-review

---

## 9. Cozi and Tody

### Patterns
- **Cozi**: one colour per family member across calendar, lists and to-dos. https://www.cozi.com/
- **Tody**: room-by-room progress bars that visualise dirtiness rising over time and dropping on
  completion. https://mobilesyrup.com/2019/07/14/tody-cleaning-review/

### Weaknesses
- **Cozi**: free tier ads, including intrusive audio ads; clunky undo; missing search and colour coding
  options. https://kimola.com/reports/explore-in-depth-user-feedback-analysis-on-cozi-family-organizer-app-store-us-148390
- **Tody**: single-purpose (cleaning only); no device or calendar context.

---

## 10. ChatGPT mobile

### Patterns
- **Composer pinned at the bottom**: rounded pill with "+" (attachments/tools) on the left, placeholder
  "Ask ChatGPT", mic and a voice-mode button on the right; the send arrow replaces voice when text exists.
  Top bar: menu (history drawer) left, new-chat and overflow right. (observed) https://apps.apple.com/us/app/chatgpt/id6448311069
- User messages are right-aligned tinted bubbles; assistant replies are full-width plain text with a
  small action row (copy, thumbs, read aloud, regenerate, share). (observed)
- Distinct visual states for waiting, streaming, tool use and done, with task-specific labels instead of
  a generic spinner. https://screensdesign.com/articles/chatgpt-mobile-app-design/
- **Confirmation before consequential actions** (anything hard to undo or with a financial, legal or
  account commitment); login steps hand control back to the user ("Take over browser").
  https://help.openai.com/articles/11752874

### Weaknesses
- Costly-action button placed where it is easy to hit by accident (Deep Research) with no "are you sure".
  https://community.openai.com/t/the-deep-research-button-in-the-chatgpt-app-is-poorly-placed-causing-accidental-presses-and-needs-a-design-fix/1134075
- Voice input sends immediately when you stop talking, no chance to review the transcript.
  https://community.openai.com/t/new-voice-input-feature-feels-like-massive-downgrade/1227243
- Android Enter key sends, so no multiline prompts. https://piunikaweb.com/2026/03/16/chatgpt-android-app-enter-key-sends-message-fix/
- Cluttered for screen-reader users; send button hard to locate. https://www.applevis.com/comment/172339

---

## 11. Claude mobile

### Patterns
- Minimal chrome: menu left, new-chat right; warm neutral palette with a single coral accent; serif
  display type in marketing, sans in-app. (observed) https://apps.apple.com/us/app/claude-by-anthropic/id6473753684
- **Tool results render as inline cards** inside the reply (for example a map card with place rows)
  rather than raw text. (observed)
- **Permission card with three choices**: "Allow once", "Always allow", "Deny"; saved approvals are
  revocable in Settings; connectors default to "Always ask". https://code.claude.com/docs/en/desktop
- Voice mode is a single waveform button in the composer.

### Weaknesses
- Bug reports where "Always allow" from the Android app breaks the call while "Allow once" works, i.e.
  persistent approvals are fragile. https://claudeissues.com/issue/45942-bug-remote-control-always-allow-permission-from-android-app-breaks-tool-calls
- Toast approval buttons that do nothing ("permission must still be granted elsewhere").
  https://claudeissues.com/issue/56306-bug-toast-notification-allow-deny-buttons-are-no-ops-permission-must-still-be-gr

---

## 12. Linear (polish reference)

### Patterns
- **Colour from three inputs**: base, accent, contrast (30-100), computed in LCH so equal lightness looks
  equal across hues; replaced 98 per-theme variables. Very little chrome colour; neutral, high-contrast
  text. https://linear.app/now/how-we-redesigned-the-linear-ui
- **Type**: Inter Display for headings, Inter for body. Weights 400/510/590 (510 is the signature
  "medium") (extracted). Scale 12/13/14/16/18/24/32/48 px; UI body sits at 13-14px (extracted). Tight
  negative tracking only at display sizes. https://open-design.ai/plugins/design-system-linear-app/
- **Spacing**: 4px base, scale 4/8/12/16/20/24/32/48 (extracted). (same source)
- Principles: reduce visual noise, keep strict alignment, raise hierarchy and density of navigation.
  Activity feed = one line per event, small avatar/icon, actor + verb + object + relative time
  ("moved from Todo to In Progress - just now"). (observed on linear.app)

### Weaknesses
- Density tuned for desktop pointers; 13px text and 28-32px rows are below comfortable phone touch
  sizes (Apple asks for 44pt targets). Copy the hierarchy and restraint, not the density, on phones.

---

## Patterns we adopt

1. **Status pills as the "right now" bar** (Apple categories, HA badges): one horizontally scrollable row
   at the top of Home, each pill = icon + label + live count ("Lights 3 on", "Doors 1 unlocked",
   "Chores 4 left", "Approvals 2"). Tapping a pill filters. No separate summary card.
2. **Tile anatomy**: 36px round icon chip, name (wraps to 2 lines, never truncates at 10 chars), one state
   line. Two sizes: compact (1 row, half width on phones) and large (2 rows, adds one primary control:
   slider, -/+ or toggle). HA's 56px row / 8px gap grid is a proven baseline.
3. **Visible affordance for the toggle target**: icon chip gets a filled circle only when tapping it acts
   (HA 2025.3). Tap icon = toggle, tap body = detail sheet, long-press = same detail sheet (never the only
   path to controls; SmartThings proved that).
4. **Active state by fill, one hue per category**: on = tinted fill + coloured icon; off = neutral surface.
   Accent count: one brand accent for primary actions and focus, plus fixed category hues (light amber,
   climate orange, lock/security blue, media purple). Red reserved for alerts and pending approvals.
5. **Ask the house bar**: on phones, the input lives at the bottom above the tab bar (thumb zone) and
   never jumps when it opens; on desktop it sits in the header with Cmd/Ctrl+K. The conversation opens as a
   sheet anchored to the same input (fixes Google's top-bar-to-bottom-input jump).
6. **Agent progress as named steps** (ChatGPT/HA streaming): "Checking kitchen lights", "Turning off 3
   lights", then a result card with the devices it touched and an Undo button.
7. **Confirmation tiers** (ChatGPT, Claude): reversible actions run immediately with Undo; locks, garage,
   alarms, purchases and anything affecting another person require an inline confirm card ("Allow once" /
   "Always for this" / "Cancel"), with saved rules listed and revocable in Settings.
8. **Approvals queue as cards with one-tap Approve/Decline**, the requester's avatar and colour, what was
   asked, and points/cost; badge count on the tab. Approval directly grants the reward (Skylight's manual
   reward step is why families stop).
9. **Chore board per person** (Skylight/Cozi colours): each member has a colour + avatar used everywhere
   (chores, approvals, timeline). Emoji/icon chores for pre-readers. Completion celebration once per list,
   short and skippable.
10. **Fair points** (fixes Sweepy): points scale with an estimated minutes field, not just a difficulty
    tag; adults can adjust after approval; rotations assign named people per day.
11. **Activity timeline in Linear style**: one line per event, actor avatar, verb, object, relative time;
    a short AI "Today at home" summary at the top (Google Home Brief) that is local and dismissible; filter
    chips (People, Devices, Chores, Agent).
12. **Three to four tabs on phones** (Google cut 5 to 3; Apple uses 3): Home, Chores, Activity, plus
    Approvals only for adults (badge). Settings in the avatar menu.
13. **Smart default dashboard** (HA 2026 Overview): Favorites (about 8), Summaries by type, Rooms. Works
    with zero configuration; editing never forks or breaks auto-updates.
14. **Type and spacing**: Inter (or system UI) with a 4px spacing base; phone body 15-16px, labels 13px,
    tile name 15px/600, state line 13px/400, section titles 13px uppercase or 17px/600, page title 28px.
    44px minimum touch targets on phones; desktop may tighten toward Linear density.
15. **Browser onboarding in under five steps**: name the home, add people (name, colour, adult/kid), scan
    devices (auto-discovered list with checkboxes and suggested rooms), pick starter chores, done. Each
    step skippable; finishing lands on a populated Home.

---

## How ChorePilot 0.6 answers each (and where it does not yet)

What we built is in the last two columns; anything not shipped is listed as a gap, not a claim.

| Product | Their strength | Their weakness | What the 0.6 dashboard does | Where we still fall short |
|---|---|---|---|---|
| Apple Home | Category status, calm tiles, active tiles light up | Invisible icon-vs-tile tap split, truncated names, no assistant you can type to | Actionable icons get a visible circle (others don't), the rest of the tile opens the sheet; names wrap to two lines; "right now" line; Ask the house on Home | No tile sizes or custom ordering; no scenes; no camera tiles |
| Google Home | Three tabs, Ask Home, tinted active tiles | Ask bar jumps from the top to a bottom chat; cloud-only; slow replies | The ask bar never moves (top of Home on desktop, docked above the tab bar on phones) and answers in place; local model; every step shown | Ask only on Home, not on every screen; no deterministic fast path yet, so a small model adds latency |
| SmartThings | Compact cards, big device detail | Controls behind long-press + Expand; promo content | One tap opens full controls; no promotional content anywhere | No widgets or per-user layouts |
| Home Assistant | Precise grid, tile features, Assist | Built for tinkerers; weak per-person roles; editing forks the default | Roles (owner / adult / kid / guest) drive every control; kids' water/heat requests become approvals; no layout to fork | Far fewer device types and no dashboard editing; the registry decides the controls |
| Homey | Sentence-style flows, per-person access | Stagnant dashboards; advanced flows web-only | Same app on phone and desktop; setup in the browser | No automations UI at all |
| Sweepy | Leaderboard, kid work approval | Unfair points; no per-person-per-day assignment; family behind paywall | Per-person week ring, points, streaks, rotations with this week / next week, approvals for kids, reassign in one tap; all free and local | No rewards / allowance; points are the chore's fixed value |
| OurHome | Points to rewards, lists | Buggy ordering, lost reminders | Server-ordered board; every completion in the activity timeline | No shopping lists, no rewards |
| Skylight | Per-person colour columns, celebration | Manual reward granting, subscription | Person colours and initials everywhere; a small check-off animation; device-verified badges | No calendar; no wall-display mode |
| Cozi / Tody | Member colours; decay bars | Ads; cleaning-only | No ads; chores sit next to the devices that verify them | No decay / "how dirty" view; no undo for a mis-tapped chore |
| ChatGPT | Bottom composer, tool states, confirms consequential actions | Voice auto-sends; accidental costly taps | Voice fills the box for review, never auto-sends; water, heat and locks always confirm inline; PIN for security | No streaming tokens (steps update by polling) |
| Claude | Inline tool cards; Allow once / Deny | Fragile saved approvals | Each tool step is shown with its outcome; approvals are records in a queue and the timeline | No "always allow" rules (deliberately: tiers are household policy) |
| Linear | Token discipline, type scale, calm density | Too dense for touch | Tokens for colour, radius, spacing on an 8 px grid; 44 px targets; 15-17 px body on phones | System font only (no Inter); no command palette |

---

## Round 2: a technical, high-performance design language (NVIDIA-inspired)

Inspiration only. This round studies how one hardware company's 2026 web properties make
compute feel fast, precise and trustworthy, so ChorePilot can borrow the *grammar* (surfaces,
geometry, type rhythm, readouts, motion budget) and none of the *identity*. Nothing in this
section authorises using that company's name, logo, wordmark, imagery, icons, copy, colour
value or font files.

Method: pages loaded in headless Chrome at 1440x900 and 390x844, values read with
`getComputedStyle` over the live DOM (counts of every visible element's background, text colour,
border and radius), CSS custom properties read from `:root`, and `@keyframes` / `:hover` rules read
from the loaded stylesheets. Values below are measured unless marked "(observed)", which means read
off a screenshot. Research date: 2026-10-22.

Pages studied:
- DGX Spark product page: https://www.nvidia.com/en-us/products/workstations/dgx-spark/
- RTX Spark product page: https://www.nvidia.com/en-us/products/rtx-spark/
- RTX AI PC page: https://www.nvidia.com/en-us/ai-on-rtx/
- Model and agent catalog: https://build.nvidia.com/ and https://build.nvidia.com/blueprints
- Developer home: https://developer.nvidia.com/
- Event site and keynote page: https://www.nvidia.com/gtc/ and https://www.nvidia.com/gtc/keynote/

Two systems coexist. The marketing pages (nvidia.com, GTC) are an AEM component system that is
mostly **light** with full-bleed black bands. The product surfaces (build.nvidia.com, the developer
hero) are a newer Tailwind-based system that is **dark first**. The second is the closer analogue for
a dashboard, so it is weighted more heavily below.

### 1. Surfaces: black and graphite, few steps

| Token (as observed) | Value | Where |
|---|---|---|
| Page canvas (app) | `#0c0c0c` | build.nvidia.com body and the fixed backdrop layer |
| Pure black band | `#000000` | Footer, full-bleed feature bands, search field fill, primary card on developer page |
| Sticky sub-nav / dark band | `#1a1a1a` | 72 px in-page tab bar on every product page; GTC keynote section |
| Raised card (dark) | `#161616` | Blueprint cards, filter panel, developer cards |
| Card (dark, alt) | `#222222` | Spec card on the AI PC page |
| Mid graphite | `#333333` | Promo band on GTC |
| Light canvas | `#ffffff`, `#f7f7f7`, `#eeeeee`, `#e1e4ea` (cool grey hero wash) | Marketing pages |

- Only four or five neutral steps are used per page. Elevation on dark is a border, not a shadow:
  cards are `#161616` on `#0c0c0c` with a 1 px white-at-20% edge.
- The app backdrop adds a single atmospheric layer: a fixed `linear-gradient(80deg, lime -> sky
  blue)` at **12% opacity** behind a faint particle/node network (observed). It reads as "energy"
  without any surface becoming coloured.
- Text on dark: `#ffffff` for headings and values, `#eeeeee` / `#e0e0e0` for body, `#a7a7a7` and
  `#898989` for secondary and counts, `#636363` for tertiary on light.

### 2. The accent green: one hue, narrow job

The brand green is one value (`--color-brand`, rgb 118,185,0), with a full 12-step ramp published
as CSS variables (`--color-green-025` `#dafb7d` through `--color-green-950` `#0d1a00`). We will not
use that value; what matters is **where** it appears. Across ~190 green-styled elements per page:

- **Primary button fill** (black text on green, never white text). Hover moves one step darker on the
  ramp (`green-400`), with the border staying on the base step.
- **Secondary button outline**: transparent fill, 2 px green border (marketing) or 1 px (app), text
  in the page's foreground colour, not green.
- **Active-tab indicator**: a solid green underline bar under the current item of the dark sticky
  sub-nav; carousel "current dot".
- **Chevrons** after text links ("Learn More >"): only the arrow glyph is green, the words stay black
  or white.
- **Footer link text** on black, and one emphasised phrase in a hero headline ("Start building." in
  green after a white clause).
- **Full-width announcement strip** (green bar, black bold text, black pill button) at the top of
  most pages.

Never used for: body text, card fills, section backgrounds (other than the thin promo strip), icons
in general, or borders of ordinary cards. Category chips use *other* hues (teal `#1dbba4` outline
pills for model tags, sky `#10b1fb`, amber `#f9c500`), so green stays reserved for "act / you are
here".

### 3. Geometry: low radius, hairlines, technical grid

Radius values counted on live elements (most to least frequent):

| Element | Radius |
|---|---|
| Buttons, inputs, search field, small tags on marketing | **4 px** |
| List-row cards (model rows in the catalog) | **8 px** |
| Feature / blueprint / product cards | **16 px** (with `14px 14px 0 0` on the image top) |
| Category chips | 32 px (full pill), 26 px tall |
| Count badges ("+4") | 10 px |

- Buttons are nearly square-cornered (4 px) even when cards are 16 px; the contrast between soft
  containers and sharp controls is a big part of the "engineered" feel.
- Borders are hairlines: **1 px white at 20%** on dark (`rgba(255,255,255,.2)`), **2 px black at 10%**
  on light cards, **1 px `#4b4b4b`** on the compact header search. FAQ rows are separated by 1 px rules
  with no boxes; footer columns sit under a 1 px rule.
- Light cards add a small shadow (`0 4px 6px rgba(0,0,0,.12)`); dark cards add none.
- Grid: content max-width **1320 px** (inner text column 1290 px; reading column 1070 px; hero copy
  column 520-630 px). At 1440 the outer margin is 75 px. Gutters between cards 30 px (observed).
  The catalog uses a 1376 px wide shell with two 512 px list columns. Phones: 16 px side gutter on
  the marketing pages (observed), 24 px in the app.
- Section rhythm on the marketing system uses fixed utility classes: **75 px** top/bottom is the
  default band (`p-t-75 p-b-75`), 60 px and 45 px for tighter bands, 90 px for showcase bands; 45/15
  and 30 px on phones. The developer site uses 44 / 64 / 80 px.
- The event site layers a light grey triangular lattice behind the hero, cut by one diagonal green
  rule (observed): a "technical drawing" motif.

### 4. Typography

The proprietary family appears as `"NVIDIA Sans"` (app, with a metric-matched `"NVIDIA Sans
Fallback"`) and as `NVIDIA-NALA, Arial, Helvetica` on the marketing system. Loaded weights:
**400, 500, 700** (300 declared, unused). The developer site's code uses JetBrains Mono.

Observed metrics at 100 px (canvas `measureText`):

| Metric | Value | Reading |
|---|---|---|
| Cap height | 70 | |
| x-height | 51 (x/cap 0.73) | Large x-height, readable at 12-14 px |
| Descender | 20 | |
| "Hamburgefonstiv 0123456789" width, 400 | 1447 | Moderately wide, not condensed |
| Same string, 700 | 1465 (+1.2%) | Weights are near width-stable: bolding a label does not reflow it |
| Digits 0-9, 400 and 700 | 600 and 600 | **Tabular figures by default**, 0.60 em each, at every weight |

Shape (observed): squarish rounded bowls, flat horizontal terminals, open apertures, single-storey
feel overall, low stroke contrast. A sober grotesk, not a display face.

Type scale (desktop / phone):

| Role | Size / line-height | Weight | Tracking |
|---|---|---|---|
| Product H1 | 50 / 62.5 px (marketing), 48 / 60 (app hero) ; 28 / 35 and 32 / 40 on phones | 700 | normal |
| Section H2 | 36 / 45 | 700 | normal (one carousel face uses -0.01 em) |
| Card / sub H3 | 28 / 35, 24 / 30, 20 / 25 | 700 | normal |
| Eyebrow / kicker | 20 / 25 (e.g. "Workloads", "RTX Spark Superchip" above the H2) | 700 | normal, **sentence case** |
| Lead paragraph | 22 / 38.5 (1.75) marketing; 18 / 27 app | 400 | normal |
| Body | 16 / 24 | 400 | normal |
| Nav / tabs | 15 / 25 (sticky sub-nav), 14-16 / 16 (headers) | 400 (500 dev) | normal |
| Button label | 14 / 14 (12 / 12 compact) | 700 | normal |
| Chip | 12 / 18 | 500 | normal, `lowercase` |

Notes against the brief's assumptions:
- **Eyebrows are not uppercase.** No measured eyebrow used `text-transform: uppercase` or positive
  tracking on any page. Kickers are bold, sentence-case, a step smaller than the heading, centred above
  centred headings or left above left headings. The only transforms found were `lowercase` on chips.
- **Headline tracking is "normal"**, not tightened, at 36-50 px. The tight, dense look comes from the
  font's own spacing and 1.25 line-height on headings (62.5/50, 45/36), not from negative tracking.
- Headings are pure black (`#000`) or pure white; body is a half-step softer (`#1a1a1a` / `#eeeeee`).

### 5. Big numeric readouts and dense spec panels

- **Spec row**: four equal white tiles under the product shot, each "Up to" (16 / 400, muted) over the
  value ("6,144-core", "20-core", "1 petaflop", "128 GB") at **36 / 45 / 700**, over a unit caption
  ("Blackwell RTX GPU", 16 / 400). Radius 16, the same 2 px 10% border as other light cards. On phones
  the value drops to 28 / 35.
- **Performance multipliers**: on a `#1a1a1a` band, four columns each with a 20 px bold label ("Develop",
  "Game", "Create", "Chat") over a giant **80 / 120 px, 700** value ("4x", "12x", "10x"), over a one-line
  description, with a 10 px footnote paragraph of test conditions under the row.
- **Cluster table** (DGX page, observed): five columns, bold configuration name over three stacked
  lines of value ("128 GB / Up to 200B / parameters"), no cell borders, just alignment.
- The catalog list rows are the app equivalent of a spec panel: 64 px tall, `#0c0c0c` fill, 1 px 20%
  border, radius 8, padding 8; logo at left, two-line "org / model-name" (16 / 24), outline chip and
  "+N" count right-aligned, a "216 skills >" count in muted grey on skill rows.
- Because digits are tabular in the base font, numbers line up in columns without special styling.

### 6. Product-hero composition

- Split hero at 1440: copy column 520-630 px on the left (H1, one-line lead, then CTAs), product render
  on the right, on a soft cool-grey wash (`#e1e4ea`-ish) or full-bleed photography. The render is
  isolated hardware floating in light, often in a grid of variants (DGX page shows the partner
  boxes as an isometric 3x3).
- DGX hero puts "Where to Buy" in a flat grey inset box (no radius) holding a primary + secondary
  button pair and a pipe-separated link row ("Datasheet | Specifications | Forum").
- Directly under the hero sits the **sticky dark sub-nav** (`#1a1a1a`, 72 px): section anchors at 15 px
  white, a green underline on the current one, and the primary CTA repeated at the right end, so the
  buy action is always one tap away while scrolling.
- App hero (catalog): centred 48 px headline on near-black, a 680 x 48 search field (black fill, 1 px
  20% border, radius 4) with a square green search button inside its right edge, then a single
  announcement card, then two list columns. No illustration; the backdrop texture carries the mood.
- Phones: everything stacks; the sub-nav collapses to a single "Overview v" dropdown with the CTA kept
  at its right (observed).

### 7. CTA styling

| Variant | Fill / text | Border | Radius | Padding | Height | Type |
|---|---|---|---|---|---|---|
| Primary | brand green / black | 1 px transparent | 4 px | 12 px all round | **40 px** | 14 / 700 |
| Secondary | transparent / foreground | **2 px** brand green | 4 px | 12 px | 42 px | 14 / 700 |
| Compact header CTA | green / black | none | 4 px | 8 px | 30 px | 12 / 700 |
| App primary | green / near-black | 1 px green | 4 px | 7 x 11 px | 40 px | 14 / 700 |
| App ghost on dark | transparent / white | 1 px `#a7a7a7` | 4 px | 7 x 11 px | 40 px | 14 / 700 |
| Inverse (on green strip) | `#000` or `#1a1a1a` / white | none | 4 px (0 on developer) | 9 x 13 px | 38-40 px | 14-16 / 700 |
| Text link CTA | none / foreground | none | - | - | - | 14 / 700 + green chevron |

Buttons are short (40 px) and tight-padded, sized to the label rather than to a fixed width; button
labels are Title Case, never uppercase.

### 8. Motion

The motion budget is deliberately small. No scroll-reveal library was loaded (no AOS, ScrollTrigger
or similar), and no below-the-fold element was found waiting at `opacity: 0` for an
IntersectionObserver.
- Default interaction transition: `color, background-color, border-color` at **150 ms** (links, buttons)
  to **200-250 ms** (cards, inputs), easing **`cubic-bezier(0.4, 0, 0.2, 1)`** almost everywhere.
- Button hover: one step darker fill; arrow chevrons nudge right via `margin-left / padding-right
  100 ms ease-out`; app chevrons use `opacity, translate 250 ms`.
- Popovers, tooltips and menus enter with `opacity 0->1` plus a short `translate` (keyframes
  `nv-popover-in`, `nv-tooltip-in`, `select-in`); side panels slide fully in from the edge; modals fade
  in 200 ms.
- Content swaps: tab panels cross-fade at `opacity 0.8s ease-in-out`; carousels slide at `transform
  0.55s` with the same standard easing; image teasers fade at `opacity 1s ease`.
- Ambient motion only on showcase bands: endless marquee rows of app logos (`slideRow1/2/3`, 40-70 s
  linear, alternating direction) and the slowly drifting particle network behind the app.
- Loading: a 1.5 s indeterminate progress bar and a `pulse` (opacity 50%) skeleton.

### What we take

1. A dark-first shell for the dashboard with five neutral steps only (canvas, card, raised, hairline,
   muted text), elevation by 1 px light-at-20% hairline instead of shadow, and an optional
   low-opacity (around 10%) two-hue gradient wash behind the canvas, using our own hues.
2. One accent hue with one job: primary action fill (dark text on it), outline for the secondary
   action, the current-tab underline, and the chevron on text links. Status and categories get other
   hues. Ship a full 10-12 step ramp for the accent and use the next-darker step on hover.
3. Sharp controls inside softer containers: 4 px buttons and inputs, 8 px list rows, 16 px cards,
   full pills only for chips.
4. A sticky in-page section bar on long screens (room list, chore board) with the primary action
   pinned at its right end.
5. Big readouts as a first-class component: label over value (36 px desktop / 28 px phone, bold) over
   unit, and a hero multiplier style (80 px) for the one number that matters (for example energy saved
   this week). Tabular figures everywhere so columns align.
6. Headline rhythm of 1.25 line-height at 700 with normal tracking; sentence-case bold kickers above
   headings instead of tiny uppercase eyebrows; body 16 / 24; lead 18-22 with generous 1.5-1.75
   leading.
7. Dense, borderless spec layouts: alignment and rules rather than boxes; 64 px list rows with a
   right-aligned chip + count.
8. Restrained motion: 150-250 ms colour transitions on one standard easing, popovers that fade-and-
   shift a few pixels, cross-fades for tab content, and no scroll-triggered reveals. Respect
   `prefers-reduced-motion` by dropping the ambient layer entirely.
9. Section spacing on a fixed ladder (for our 8 px grid: 40 / 56 / 72 / 88 px desktop, 24-32 px phone).

### What we must not copy

- No name, logo, wordmark, product names, event names, or copy, in the UI or the docs that ship.
- No images, renders, photography, the particle-network artwork, the triangular lattice, the diagonal
  green rule, or icon sets (they use Font Awesome Pro/Sharp and custom pictograms; we keep our own
  icons).
- No font files: the proprietary family and its fallback are theirs. Use an OFL family (see below).
- Not the exact accent: the brand green (rgb 118,185,0) and its published ramp are a trademark colour.
  Our accent must be visibly different in hue (for example a cooler mint/teal or an electric
  chartreuse shifted toward yellow) and pass contrast on its own terms.
- Not the full-width green announcement strip or the "green bar under a black sub-nav" combination as
  a unit; that pairing is too recognisable. Use the pattern (sticky section bar with an accent
  indicator) in our own palette.
- No marketing-page density on the phone app; the hero/marketing composition informs the onboarding
  page only.

### Font choice (OFL, Google Fonts)

Candidates were measured the same way as the reference (at 100 px; reference x/cap 0.73, width per
cap 20.7, bold growth +1.2%, digits 0.60 em at both weights) and set side by side on the same dark
canvas:

| Family | x/cap | Width / cap | Bold growth | Digits 400/700 | Notes |
|---|---|---|---|---|---|
| IBM Plex Sans | 0.74 | 19.9 | +2.9% | **600 / 600** | Closest overall feel: flat terminals, squarish curves, sober; tabular digits identical to the reference |
| Saira | 0.74 | 20.8 | +1.4% | 608 / 614 | Closest numbers, but its squared "t", "g" and heavy bold read more sci-fi than the reference |
| Sora | 0.73 | 21.0 | +2.7% | 619 / 642 | Similar proportions, rounder and friendlier |
| Inter | 0.75 | 19.6 | +4.3% | 592 / 620 | Neutral, slightly larger x-height and rounder; safe fallback |
| Space Grotesk | 0.70 | 20.7 | +0.3% | 588 / 591 | Quirky "G", "1" and "y"; too characterful |
| Barlow | 0.73 | 18.1 | +3.4% | 503 / 525 | Matching x/cap, but narrow and DIN-like; good only for big numerals |
| Albert Sans, Archivo, Manrope, DM Sans | 0.71-0.77 | 19.2-19.8 | +0.7-6.7% | non-tabular | Further from the reference |

Recommendation: **IBM Plex Sans** (400 / 500 / 700) for UI and headings, with `font-variant-numeric:
tabular-nums` on readouts, and **JetBrains Mono** or IBM Plex Mono for code and device IDs. If the
readouts need more "instrument panel" character, Saira at 600-700 for the big numbers only is the one
alternative that stays close to the reference metrics.

### Applied in ChorePilot 0.7

- **Typeface:** IBM Plex Sans (OFL, variable, self-hosted under `chorepilot/webui/fonts/`), chosen on
  the measurements above: the same x-height to cap ratio, flat terminals, squarish curves and
  tabular digits at 0.60 em in every weight, so the big readouts don't jitter when they count up.
  Albert Sans and Barlow were tried in the real dashboard first; Barlow read too narrow and
  Albert Sans has proportional digits.
- **Accent:** #C8F031, an electric chartreuse (hue 73 degrees against the reference's 82, and much
  lighter), used for primary fills with near-black text (15:1), active underlines, live dots,
  progress and the lit-tile edge. Text on white uses #4E6600 (6.5:1). Never body text on dark.
- **Where we chose differently from the measurements:** uppercase, tracked eyebrow labels and
  slightly tighter headline tracking (the brief asked for that technical "spec" voice; the
  reference sites use sentence-case kickers and normal tracking), 2-6 px radii instead of 4/8/16,
  and staggered entry reveals on first paint only (the reference has none); all motion is off
  under prefers-reduced-motion.
