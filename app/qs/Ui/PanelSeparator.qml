import QtQuick
import qs.Commons
// Qualificado: desde o Qt 6.12 um `Color` solto resolve para o tipo Color do próprio QtQuick.
import qs.Commons as Commons

Rectangle {
  id: root
  property color foreground: Commons.Color.foreground
  property real strength: 0.12
  width: parent ? parent.width : implicitWidth
  implicitWidth: 100
  implicitHeight: 1
  height: 1
  color: Qt.rgba(foreground.r, foreground.g, foreground.b, strength)
}
