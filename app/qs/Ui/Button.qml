import QtQuick
import qs.Commons

// Shim de qs.Ui.Button (subconjunto usado pelo PanelContent).
Rectangle {
  id: root
  property string text: ""
  property string iconText: ""
  property bool bordered: false
  property bool selected: false
  property color foreground: Color.foreground
  property color accent: Color.accent
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.body
  property real iconSize: Style.font.icon
  signal clicked()

  radius: Style.cornerRadius
  implicitWidth: row.implicitWidth + 22
  implicitHeight: row.implicitHeight + 13
  color: mouse.pressed ? Util.alpha(accent, 0.25)
    : mouse.containsMouse ? Util.alpha(accent, 0.12)
    : selected ? Util.alpha(accent, 0.18) : "transparent"
  border.width: bordered ? 1 : 0
  border.color: Util.alpha(foreground, 0.4)

  Row {
    id: row
    anchors.centerIn: parent
    spacing: 6
    Text {
      visible: root.iconText !== ""
      anchors.verticalCenter: parent.verticalCenter
      text: root.iconText
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: root.iconSize
    }
    Text {
      visible: root.text !== ""
      anchors.verticalCenter: parent.verticalCenter
      text: root.text
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: root.fontSize
    }
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: root.clicked()
  }
}
