pragma Singleton
import QtQuick

// Shim de qs.Commons.Color pro painel standalone: paleta fixa (fora do
// omarchy-shell não há tema pra seguir). Mesma API que o PanelContent usa.
QtObject {
  readonly property color foreground: "#c9cdd6"
  readonly property color background: "#11151b"
  readonly property color accent: "#8f96ee"
  readonly property color urgent: "#e06065"
  readonly property QtObject popups: QtObject {
    readonly property color background: "#11151b"
    readonly property color text: "#c9cdd6"
    readonly property color border: "#8f96ee"
  }
}
