import QtQuick
import qs.Commons

// Shim de qs.Ui.ToggleSwitch (subconjunto usado pelo PanelContent).
Item {
  id: root
  property bool checked: false
  property color foreground: Color.foreground
  property color accent: Color.accent
  signal toggled()

  readonly property int trackHeight: 22
  readonly property int trackWidth: Math.round(trackHeight * 1.9)
  readonly property int knobSize: Math.round(trackHeight * 0.72)
  readonly property int knobInset: Math.round((trackHeight - knobSize) / 2)

  implicitWidth: trackWidth
  implicitHeight: trackHeight

  Rectangle {
    id: track
    anchors.fill: parent
    radius: height / 2
    color: root.checked ? Util.alpha(root.accent, 0.35) : Util.alpha(root.foreground, 0.10)
    border.width: 1
    border.color: root.checked ? root.accent : Util.alpha(root.foreground, 0.4)

    Rectangle {
      width: root.knobSize; height: root.knobSize; radius: root.knobSize / 2
      anchors.verticalCenter: parent.verticalCenter
      x: root.checked ? track.width - width - root.knobInset : root.knobInset
      color: root.checked ? root.accent : Qt.darker(root.foreground, 1.25)
      Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }
  }

  MouseArea {
    anchors.fill: parent
    cursorShape: Qt.PointingHandCursor
    onClicked: root.toggled()
  }
}
