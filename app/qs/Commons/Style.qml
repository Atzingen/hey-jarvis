pragma Singleton
import QtQuick

// Shim de qs.Commons.Style: tokens de fonte/espaçamento com os defaults do
// omarchy-shell (base 12px, monospace), sem escala dinâmica.
QtObject {
  readonly property int cornerRadius: 8
  function space(px) { return px }
  readonly property QtObject font: QtObject {
    readonly property string family: "monospace"
    readonly property int caption: 10
    readonly property int bodySmall: 11
    readonly property int body: 12
    readonly property int subtitle: 13
    readonly property int title: 14
    readonly property int heading: 16
    readonly property int display: 24
    readonly property int displayLarge: 28
    readonly property int icon: 14
    readonly property int iconLarge: 18
  }
}
