"""
[포트폴리오용 실패 기록] gui_web UI 좌표 표시 실패 당시 코드 스냅샷
====================================================================

이 파일은 실행되는 코드가 아니라, "로봇의 실제 좌표계를 GUI 캔버스에 반영하지 않고
임의(원본 mm 값 그대로) 좌표를 사용해서 글씨가 화면에 뜨지 않았던" 시점의 관련 코드를
그대로 보존해둔 기록이다. 당시 실제로는 이 로직이 두 파일에 나뉘어 있었다:

  1. rokey_bootbot/server_connector_node.py (백엔드) -- pen_z_down 파라미터
  2. rokey_bootbot/gui_web/static/js/main.js (프론트엔드) -- 캔버스 렌더링

--------------------------------------------------------------------
문제 1: server_connector_node.py의 pen_z_down 기본값이 실제 로봇 설정과 불일치
--------------------------------------------------------------------
실제 로봇(calligraphy_robot/calligraphy_config.json)의 z_down은 91.0(mm)인데,
server_connector_node.py는 아래처럼 130.0을 기본값으로 하드코딩하고 있었다:

    self.declare_parameter('pen_z_down', 130.0)
    self.declare_parameter('pen_z_draw_tolerance', 3.0)

    ...

    def _on_current_posx_response(self, future):
        ...
        x, y, z = response.task_pos_info[0].data[0:3]

        # z가 130.0 근처(±3.0)여야만 'draw'로 판정하는데, 실제 로봇의 펜 다운 위치는
        # z≈91.0이라 이 조건에 절대 걸리지 않았다. 그 결과 action이 항상 'move'로만
        # 나와서, 프론트엔드(main.js)가 "action==='draw'일 때만 선을 그림" 조건에
        # 걸려 캔버스에 선이 단 하나도 그려지지 않았다.
        action = 'draw' if abs(z - self._pen_z_down) <= self._pen_z_draw_tolerance else 'move'

        self._broadcast_pen_point(
            {'part_name': self._active_part_name, 'stroke_id': 0}, action, {'x': x, 'y': y})

--------------------------------------------------------------------
문제 2: main.js가 로봇 mm 좌표를 캔버스 픽셀 좌표로 변환하지 않고 1:1로 그림
--------------------------------------------------------------------
로봇의 실제 작업 영역은 x: 351~431mm, y: -21~29mm (약 80mm x 50mm)인데, 캔버스는
800x680 픽셀이다. 아래 코드처럼 좌표를 그대로(단위 변환 없이) 그리면, 실제 글씨
영역이 캔버스 왼쪽 위 구석에 폭 80px, 높이 50px짜리 아주 작은 점 뭉치로만 찍혀서
사실상 아무것도 안 보이는 것처럼 보였다:

    // 좌표는 캔버스 픽셀 좌표계(0~canvas.width, 0~canvas.height)로 들어온다는 게
    // Client 쪽과 합의된 규격이라는 전제. 여기서 임의로 스케일/이동하지 않고
    // 받은 좌표를 그대로 1:1로 그린다 -- 규격을 벗어나는 좌표가 오면 화면 밖으로
    // 나가거나 잘리는 게 정상이며, 그건 Client 쪽에서 규격을 맞춰야 할 문제다.
    function handlePenPoint(payload) {
      const partName = payload.header.part_name;
      const strokeId = payload.header.stroke_id;
      const point = payload.point;   // <- 로봇 mm 값이 캔버스 픽셀로 착각되어 그대로 사용됨

      const isNewSession = partName !== lastPartName
        || (strokeId === 1 && payload.action === 'move');

      if (isNewSession) {
        clearCanvas();
        lastPartName = partName;
      }

      if (payload.action === 'draw' && lastPoint) {
        ctx.beginPath();
        ctx.moveTo(lastPoint.x, lastPoint.y);
        ctx.lineTo(point.x, point.y);
        ctx.stroke();
      }

      lastPoint = point;
    }

--------------------------------------------------------------------
당시 이 상태가 "실패"로 보였던 이유 (증상 요약)
--------------------------------------------------------------------
- 문제 1 때문에 action이 전부 'move'로만 옴 -> 문제 2와 무관하게 이 시점에서 이미
  선이 그려질 조건 자체가 성립하지 않았음.
- 설령 action이 'draw'로 잡혔더라도, 문제 2 때문에 실제로는 800x680 캔버스에서
  거의 안 보이는 크기(약 80x50px)로만 찍혔을 것.
- 즉 두 버그가 겹쳐서 "GUI에 글씨가 전혀 뜨지 않는다"는 증상으로 나타났다.

--------------------------------------------------------------------
실제로 적용된 수정 (현재 코드, 참고용)
--------------------------------------------------------------------
- server_connector_node.py: pen_z_down 기본값을 91.0으로 수정.
- server_connector_node.py: _broadcast_pen_point 호출 시
  header에 'coord_space': 'robot_mm'을 추가해서, 이 점이 로봇 base 좌표계 mm
  값이라는 걸 프론트엔드가 알 수 있게 함 (값 자체는 변형하지 않음 -- DB 저장/다른
  소비자에게는 항상 원본 물리 좌표를 그대로 전달해야 하므로, 좌표 변환은 서버가
  아니라 뷰 계층인 GUI 쪽 책임으로 분리함).
- main.js: coord_space === 'robot_mm'일 때만 robotMmToCanvasPx()로 mm -> 캔버스
  픽셀 변환(종횡비 유지, letterbox 중앙 정렬)을 적용하도록 수정. SVG 업로드/테스트
  경로(이미 캔버스 픽셀과 1:1로 맞게 설계됨)는 이 플래그가 없어 기존 그대로 동작.
"""
