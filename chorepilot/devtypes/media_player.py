"""Media players: speakers, TVs, streamers. Reading is tier 0; everything else is tier 1.

Volume is capped by house.json "max_volume" (percent, default 100): set it lower for a kids'
room speaker so "turn it up to 100" can't happen at 2 a.m. Quiet hours are not enforced here;
use the device's own night mode for that.

HA: media_player.media_play / media_pause / media_stop / media_next_track /
    media_previous_track / volume_set {volume_level 0..1} / volume_mute {is_volume_muted} /
    select_source {source} / turn_on / turn_off; states playing, paused, idle, on, off,
    standby, buffering; attributes volume_level, is_volume_muted, source, source_list,
    media_title, media_artist  https://www.home-assistant.io/integrations/media_player/
Matter: Speaker, Basic / Casting Video Player. Vendors: Sonos, Google Cast, Apple TV / AirPlay,
Roku, Samsung / LG TVs, Spotify Connect -- through HA.
MQTT: <prefix>/command PLAY|PAUSE|STOP|NEXT|PREVIOUS|ON|OFF|MUTE|UNMUTE,
      <prefix>/volume/command 0-100, <prefix>/source/command; <prefix>/state, .../volume/state.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param

VOL = Param("volume", "int", "volume in percent (0-100)", lo=0, hi=100)
SRC = Param("source", "string", "input or source to switch to, e.g. TV, Spotify, HDMI 1")
SIMPLE = {"play": "media_play", "pause": "media_pause", "stop": "media_stop",
          "next": "media_next_track", "previous": "media_previous_track",
          "turn_on": "turn_on", "turn_off": "turn_off"}


@register
class MediaPlayer(DeviceType):
    name = "media_player"
    title = "speaker"
    room = "Living room"
    tool = "media_player"
    tool_desc = ("Speakers and TVs: play, pause, stop, next, previous, set_volume (percent), "
                 "mute, unmute, select_source, turn_on, turn_off.")
    ha_domain = "media_player"
    matter = ("Speaker", "Basic Video Player", "Casting Video Player")
    capabilities = ("transport", "volume", "mute", "source", "power")
    status_fields = ("state", "volume", "muted", "source", "title")
    keywords = ("music", "speaker", "speakers", "tv", "television", "volume", "song", "playlist",
                "sonos", "spotify", "podcast", "louder", "quieter", "mute", "unmute", "pause",
                "play")
    aliases = ("tv", "television", "sonos", "music")
    actions = {
        "play": Action("play", "Play / resume", (), tier=1),
        "pause": Action("pause", "Pause", (), tier=1),
        "stop": Action("stop", "Stop playback", (), tier=1),
        "next": Action("next", "Next track", (), tier=1),
        "previous": Action("previous", "Previous track", (), tier=1),
        "set_volume": Action("set_volume", "Set the volume", (VOL,), tier=1),
        "mute": Action("mute", "Mute", (), tier=1),
        "unmute": Action("unmute", "Unmute", (), tier=1),
        "select_source": Action("select_source", "Switch the input / source", (SRC,), tier=1),
        "turn_on": Action("turn_on", "Turn it on", (), tier=1),
        "turn_off": Action("turn_off", "Turn it off", (), tier=1),
    }
    events_emitted = ("started", "stopped")

    def action_aliases(self):
        return {"resume": "play", "skip": "next", "next_track": "next", "back": "previous",
                "previous_track": "previous", "volume": "set_volume", "volume_set": "set_volume",
                "source": "select_source", "input": "select_source", "on": "turn_on",
                "off": "turn_off", "silence": "mute"}

    def param_aliases(self):
        return {"volume_level": "volume", "level": "volume", "input": "source"}

    def default_action(self, args):
        if args.get("volume") not in (None, ""):
            return "set_volume"
        if args.get("source"):
            return "select_source"
        return "play"

    def limits(self, param, cfg):
        if param == "volume":
            return 0, int((cfg or {}).get("max_volume", 100))
        return None, None

    def normalise(self, action, params, cfg):
        if action == "set_volume" and "volume" not in params:
            raise ValueError("set_volume needs volume (percent)")
        if action == "select_source" and not params.get("source"):
            raise ValueError("select_source needs source")
        return params

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        srcs = (cfg or {}).get("sources") or status.get("sources")
        if action == "select_source" and srcs:
            hit = next((s for s in srcs if s.lower() == str(params["source"]).lower()), None)
            if not hit:
                return f"the {self.title}'s sources are: {', '.join(srcs)}"
            params["source"] = hit
        return None

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        if p != "playing" and c == "playing":
            return ["started"]
        if p == "playing" and c in ("paused", "idle", "off", "standby"):
            return ["stopped"]
        return []

    def sim_init(self, cfg):
        return {"state": "idle", "volume": 30, "muted": False, "source": None, "title": None}

    def sim_apply(self, st, action, params, now, speed):
        if action == "play":
            st.update(state="playing", title=st.get("title") or "Morning playlist")
        elif action == "pause":
            st["state"] = "paused"
        elif action in ("stop",):
            st["state"] = "idle"
        elif action == "turn_off":
            st["state"] = "off"
        elif action == "turn_on":
            st["state"] = "idle" if st["state"] == "off" else st["state"]
        elif action == "set_volume":
            st.update(volume=params["volume"], muted=False)
        elif action in ("mute", "unmute"):
            st["muted"] = action == "mute"
        elif action == "select_source":
            st["source"] = params["source"]

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        a = state.get("attributes") or {}
        vol = a.get("volume_level")
        title = a.get("media_title")
        if title and a.get("media_artist"):
            title = f"{title} - {a['media_artist']}"
        return {"state": state["state"], "volume": round(vol * 100) if vol is not None else None,
                "muted": a.get("is_volume_muted"), "source": a.get("source"), "title": title,
                "sources": a.get("source_list")}

    def ha_ops(self, action, params, entity, cfg):
        e = {"entity_id": entity}
        if action in SIMPLE:
            return [{"service": f"media_player.{SIMPLE[action]}", "data": e}]
        if action == "set_volume":
            return [{"service": "media_player.volume_set",
                     "data": {**e, "volume_level": round(params["volume"] / 100, 2)}}]
        if action in ("mute", "unmute"):
            return [{"service": "media_player.volume_mute",
                     "data": {**e, "is_volume_muted": action == "mute"}}]
        return [{"service": "media_player.select_source", "data": {**e, "source": params["source"]}}]

    def mqtt_state_topics(self, prefix):
        return {"state": f"{prefix}/state", "volume": f"{prefix}/volume/state",
                "source": f"{prefix}/source/state"}

    def mqtt_status(self, v):
        s = (v.get("state") or "").lower()
        if not s:
            return {"state": "offline"}
        vol = v.get("volume")
        return {"state": s, "volume": int(float(vol)) if vol not in (None, "") else None,
                "source": v.get("source")}

    def mqtt_ops(self, action, params, prefix):
        if action == "set_volume":
            return [(f"{prefix}/volume/command", str(params["volume"]))]
        if action == "select_source":
            return [(f"{prefix}/source/command", params["source"])]
        return [(f"{prefix}/command", action.upper().replace("TURN_", ""))]

    def describe_status(self, st):
        bits = [st.get("state", "?")]
        if st.get("title") and st.get("state") == "playing":
            bits.append(f"'{st['title']}'")
        if st.get("volume") is not None:
            bits.append(f"volume {st['volume']}%" + (" (muted)" if st.get("muted") else ""))
        if st.get("source"):
            bits.append(f"source {st['source']}")
        return ", ".join(bits)

    # dashboard
    def ui_active(self, st):
        return st.get("state") == "playing"

    def ui_primary(self, st, cfg=None):
        if st.get("state") in ("offline", "unavailable"):
            return None
        a = "pause" if st.get("state") == "playing" else "play"
        return {"action": a, "label": self.actions[a].desc, "params": {}, "tier": 1}

    def ui_state(self, st):
        s = st.get("state")
        if s in ("offline", "unavailable"):
            return "Not responding"
        if s == "playing":
            return "Playing" + (f" {st['title']}" if st.get("title") else "")
        return str(s or "unknown").capitalize() + (", muted" if st.get("muted") else "")
