import QtQuick
import qs.Commons

// Shim de qs.Ui.PanelActionButton (subconjunto usado pelo PanelContent).
Rectangle {
  id: root
  property string iconText: ""
  property string tooltipText: ""
  property color foreground: Color.foreground
  property color hoverColor: foreground
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.icon
  property real size: 22
  property bool bordered: false
  signal clicked()

  implicitWidth: size
  implicitHeight: size
  radius: Style.cornerRadius
  color: mouse.containsMouse && enabled ? Util.alpha(hoverColor, 0.12) : "transparent"
  border.width: bordered ? 1 : 0
  border.color: Util.alpha(foreground, 0.4)

  Text {
    anchors.centerIn: parent
    text: root.iconText
    color: root.enabled ? (mouse.containsMouse ? root.hoverColor : root.foreground) : Qt.darker(root.foreground, 2.0)
    font.family: root.fontFamily
    font.pixelSize: root.fontSize
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
    enabled: root.enabled
    onClicked: root.clicked()
  }

  Rectangle {
    visible: mouse.containsMouse && root.tooltipText !== ""
    anchors.horizontalCenter: parent.horizontalCenter
    anchors.bottom: parent.top
    anchors.bottomMargin: 6
    width: tipText.implicitWidth + 12
    height: tipText.implicitHeight + 8
    radius: Style.cornerRadius > 0 ? 4 : 0
    color: Color.background
    border.width: 1
    border.color: Util.alpha(Color.foreground, 0.4)
    Text {
      id: tipText
      anchors.centerIn: parent
      text: root.tooltipText
      color: Color.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }
}
