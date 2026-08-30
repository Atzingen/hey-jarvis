-- Omarchy 4 (Hyprland Lua config) — append to ~/.config/hypr/bindings.lua
o.bind("CTRL + SHIFT + J", "Toggle Jarvis", "jarvis toggle-notify")
o.bind("CTRL + SHIFT + H", "Jarvis: push-to-talk", "systemctl --user kill -s SIGUSR1 voice-launcher.service")

-- Hyprland classic config (Omarchy ≤ 3) — append to ~/.config/hypr/bindings.conf
-- bindd = CTRL SHIFT, J, Toggle Jarvis, exec, jarvis toggle-notify
-- bindd = CTRL SHIFT, H, Jarvis push-to-talk, exec, systemctl --user kill -s SIGUSR1 voice-launcher.service
