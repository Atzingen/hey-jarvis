-- Omarchy 4 (Hyprland Lua config) — append to ~/.config/hypr/bindings.lua

-- Jarvis: toggle the assistant / push-to-talk (talk without saying "hey jarvis")
o.bind("CTRL + SHIFT + J", "Toggle Jarvis", "jarvis toggle-notify")
o.bind("CTRL + SHIFT + H", "Jarvis: push-to-talk", "systemctl --user kill -s SIGUSR1 voice-launcher.service")

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
