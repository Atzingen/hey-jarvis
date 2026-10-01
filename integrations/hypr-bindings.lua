-- Omarchy 4 (Hyprland Lua config) — append to ~/.config/hypr/bindings.lua

-- Talk immediately, starting the service if needed. H remains an alias.
o.bind("CTRL + SHIFT + J", "Jarvis: talk now", "jarvis talk")
o.bind("CTRL + SHIFT + H", "Jarvis: push-to-talk", "jarvis talk")
o.bind("CTRL + ALT + SHIFT + J", "Jarvis: toggle wake-word listening", "jarvis wake toggle")

-- Only consume Escape while polishing; otherwise the focused app receives it.
o.bind("ESCAPE", "Jarvis: skip dictation polish", function()
  local runtime = os.getenv("XDG_RUNTIME_DIR")
  if not runtime then return { pass_event = true } end
  local marker = io.open(runtime .. "/jarvis-polishing", "r")
  if not marker then return { pass_event = true } end
  local pid = marker:read("*l")
  marker:close()
  if not pid or not pid:match("^%d+$") then return { pass_event = true } end
  local process = io.open("/proc/" .. pid .. "/stat", "r")
  if not process then return { pass_event = true } end
  process:close()
  local request = io.open(runtime .. "/jarvis-skip-polish", "w")
  if not request then return { pass_event = true } end
  request:close()
  return { pass_event = false }
end)

-- Dictation: Ctrl+Shift+K toggles (press to start, press again to transcribe and
-- paste into the active window). While recording, any other key cancels.
o.bind("CTRL + SHIFT + K", "Dictation (toggle)", function()
  hl.dispatch(hl.dsp.exec_cmd("jarvis dictate toggle"))
  hl.dispatch(hl.dsp.submap("jarvis_dictating"))
end)

-- Dictation push-to-talk: hold Ctrl+Shift+L to talk, release to transcribe.
o.bind("CTRL + SHIFT + L", "Dictation (push-to-talk)", "jarvis dictate start")
o.bind("CTRL + SHIFT + L", "Dictation stop", "jarvis dictate stop", { release = true })

hl.define_submap("jarvis_dictating", function()
  hl.bind("CTRL + SHIFT + K", function()
    hl.dispatch(hl.dsp.exec_cmd("jarvis dictate stop"))
    hl.dispatch(hl.dsp.submap("reset"))
  end, { description = "Dictation: stop and paste" })
  hl.bind("CTRL + SHIFT + L", function()
    hl.dispatch(hl.dsp.exec_cmd("jarvis dictate stop"))
    hl.dispatch(hl.dsp.submap("reset"))
  end, { description = "Dictation: stop and paste" })
  -- Any other key cancels. Must be `release`: with press, the catchall fires on
  -- the CTRL of the Ctrl+Shift+K chord itself (hyprwm/Hyprland#10166).
  hl.bind("catchall", function()
    hl.dispatch(hl.dsp.exec_cmd("jarvis dictate cancel"))
    hl.dispatch(hl.dsp.submap("reset"))
  end, { release = true, description = "Dictation: cancel" })
end)
