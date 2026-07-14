client (bootbot 패키지 — 로봇 제어측)

한글 서예 로봇(M0609+RG2) 파이프라인 중 로봇측(구 calligraphy_robot 패키지에 해당) 구현. SVG를 파싱해 원뿔대(frustum) 곡면 위 좌표/자세로 투영하고, 그 결과를 받아 실제 movel()로 필기를 수행한다. 이 스냅샷에서는 평면(flat-plane) 서예가 아니라 원뿔대 곡면 서예가 이미 라이브 코드로 반영되어 있다.

서버측(구 rokey_bootbot, MongoDB 저장·GUI 웹) 구현은 server_README.md 참고, 전체 그림은 최상위 README.md 참고.

구성 파일

- **client_connector_node.py**: InputJson 구독 → StrokeCmd(6DOF) 발행(rclpy 노드명 `connector_node`)
- **robot_write_drl_node.py**: StrokeCmd 구독 → 실제 movel() 필기 수행 + 붓펜 힘제어
- **svg_to_robot.py**: SVG → 원뿔대 좌표 투영 → InputJson 발행(디렉토리 감시 기반 1회성 트리거 함수 포함)
- **svg_parser.py**: SVG path `d` 속성을 순수 정규식으로 파싱하는 유틸리티(`parse_svg_to_strokes`)
- **trigger_node.py**: `watch_dir`를 3초 간격으로 폴링하며 새 `Canvas*.svg` 감지 시 `svg_to_robot.trigger_parsing_and_publish()` 호출(폴더 감시 책임을 `svg_to_robot.py`에서 분리)
- **config.json**: 원뿔대 워크피스/좌표 변환 설정(`svg_to_robot.py` 전용)
- **calligraphy_config.json**: 로봇 동작 파라미터 + 9점 자세 보간 데이터(`client_connector_node.py`/`robot_write_drl_node.py` 공용)

핵심 변화: 평면이 아니라 원뿔대(frustum) 곡면에 글씨를 씀

- `config.json`에 `workpiece: {type: "frustum", H: 220.0, Rb: 50.0, Rt: 52.5, tilt_x: 0.0}`로 원뿔대 형상이 정의되어 있다(H=높이, Rb=바닥 반지름, Rt=상단 반지름)
- `svg_to_robot.py`(`RobotPathProcessor.process_and_package`)가 SVG 좌표를 먼저 평면으로 정규화한 뒤, 원뿔대 표면 위로 실제 3D 투영(`h`=바닥 기준 높이, `r`=그 높이에서의 반지름, `theta=arc_len/r`)하고, 펜이 곡면 법선을 따라가도록 `rx/ry/rz`까지 직접 계산해서 점마다 다른 `z`와 다른 `rx/ry/rz`를 만들어낸다
- StrokeCmd 인터페이스는 이미 6DOF로 확장되어 있다: `total_count, stroke_id, part_name, action, float64[] x, y, z, rx, ry, rz`. `client_connector_node.py`의 `stroke_to_msg()`가 이 6개 배열을 전부 채워 publish한다
- 자세(`rx/ry/rz`) 결정 우선순위(`stroke_to_msg` 기준):
  1. point 내 `rx/ry/rz` 직접 지정
  2. stroke 내 `rx/ry/rz` 배열(point 개수와 일치)
  3. `calligraphy_config.json`의 `frustum_poses` 9점 보간값(`get_interpolated_pose`, bilinear) — 실제로는 `svg_to_robot.py`가 이미 모든 점에 `rx/ry/rz`를 채워 보내므로, 정상 경로에서는 이 보간이 호출되지 않는 순수 fallback
  4. `robot` 섹션의 기본 자세(`rx=90.0, ry=180.0, rz=90.0`)

파이프라인

```
[trigger_node.py] watch_dir 감시(Canvas*.svg, 3초 간격) → svg_to_robot.trigger_parsing_and_publish() 호출
     │
     ▼
[svg_to_robot.py] SVG 파싱(svgpathtools) → bbox 정규화 → 원뿔대 3D 투영(x,y,z,rx,ry,rz 계산)
     │ publish: /calligraphy/input_json (InputJson)
     ▼
[client_connector_node.py] (rclpy 노드명 'connector_node') 구독 → whole-job JSON 파싱/검증 → 큐 적재
     │ send_interval(0.3초) 간격으로 순차 publish
     │ publish: /calligraphy/stroke_cmd (StrokeCmd, 6DOF, stroke마다 반복)
     ▼
[robot_write_drl_node.py] 구독(stroke_cmd_callback, 큐잉만) → main loop(spin_once+execute_ready_job_once)에서
     스레드 없이 순차 실행 → draw_stroke(): 첫 점 위 이동 → 펜 내리기 → 힘제어 시작 →
     point마다 과압 확인 후 movel(ref=101) → 힘제어 해제 → 펜 올리기 →
     전체 완료 후 movej로 준비 자세 복귀
```

설정 파일 스키마

**config.json** (`svg_to_robot.py` 전용, SVG→로봇좌표 투영):

```json
{
  "robot": {"center_x": 172.00, "center_y": 42.00, "base_z": 98.00, "base_rx": 34.26, "base_ry": -178.63, "base_rz": 39.11},
  "workpiece": {"type": "frustum", "H": 220.0, "Rb": 50.0, "Rt": 52.5, "tilt_x": 0.0},
  "drawing": {"scale": 80.0, "num_points": 20},
  "path": {"watch_dir": "/home/woods/ws_cobot_pjt/ws_dsr/robot_workspace"}
}
```

- `robot`: SVG 정규화 좌표의 평면 투영 기준점 + 곡면 위 자세 계산 기준값(`base_rx/ry/rz`)
- `workpiece.tilt_x`: 경사판 기울기(도) — 이 스냅샷에서는 `0.0`으로 되어 있는데, `final_z`/`final_rx` 계산식에는 여전히 `tilt_x` 항이 남아있어(사실상 무효화) 실기 튜닝 전 상태이거나 워크피스가 평평하게 재배치됐을 가능성 있음, 확인 필요
- `path.watch_dir`: 다른 계정(`woods`) 경로로 하드코딩됨 — 이 환경(`son` 계정) 기준으로는 존재하지 않는 경로, 실제 배포 전 수정 필요

**calligraphy_config.json** (`client_connector_node.py`/`robot_write_drl_node.py` 공용):

```json
{
  "connector": {"send_interval": 0.3},
  "robot": {"lift_up": 0.0, "pen_up_extra": 20.0, "velx": 30, "accx": 20, "velj": 30, "accj": 20, "move_sleep": 0.05},
  "frustum_poses": {
    "y_bottom": -32.53, "y_center": 72.24, "y_top": 184.48,
    "x_left": 120.0, "x_center": 164.5, "x_right": 210.0,
    "b_l": [175.40, -113.99, 85.26], "b_c": [0.24, 178.6, 2.97], "b_r": [2.11, -125.97, 51.34],
    "c_l": [175.46, -130.44, 90.52], "c_c": [34.26, -178.63, 39.11], "c_r": [179.24, 124.84, -91.93],
    "t_l": [174.91, -117.56, 175.66], "t_c": [109.83, 177.94, 111.18], "t_r": [176.59, 120.74, 173.07]
  }
}
```

- `robot.pen_up_extra`: 필기 사이 펜을 들어올리는 높이(mm) — `draw_stroke()`가 첫 점/마지막 점에서 이 값만큼 z를 더해 이동
- `frustum_poses`: 9점 티칭 자세 격자 — 위 "핵심 변화" 절에서 설명한 대로 실제 경로에서는 fallback으로만 존재

**robot_write_drl_node.py 상단 하드코딩 붓 힘제어 상수** (JSON 밖, 코드 내 상수 — 주석: "JSON 정리 전까지는 여기 값만 바꿔서 테스트한다"):

```python
USE_PEN_FORCE_CONTROL = True
TARGET_PEN_FORCE_N = 4.0
MAX_PEN_FORCE_N = 12.0
PEN_FORCE_AXIS_INDEX = 2  # get_tool_force()[2] = Z축 힘만 봄
PEN_COMPLIANCE_STIFFNESS = [5000, 5000, 5000, 400, 400, 400]
```

토픽 / 메시지 스키마

**`/calligraphy/input_json`** (`calligraphy_interfaces/InputJson: int32 total_count, string json_data`)

- `svg_to_robot.py`: `json_data`가 JSON 배열 문자열(래핑 객체 없이 stroke 목록 그대로) — `client_connector_node.py`의 "bare list" 호환 분기(`parse_job_json`)가 이를 처리(리스트 길이를 `total_count`로 간주)

**`/calligraphy/stroke_cmd`** (`calligraphy_interfaces/StrokeCmd`, 6DOF)

- 필드: `total_count, stroke_id, part_name, action, float64[] x, y, z, rx, ry, rz`
- `client_connector_node.py`(`stroke_to_msg`)가 point별 rx/ry/rz 우선순위(위 "핵심 변화" 절)에 따라 6개 배열을 모두 채워 publish
- `robot_write_drl_node.py`(`is_valid_stroke_msg`)는 `z` 길이를 `point_count`와 무조건 일치시키도록 요구(불일치 시 메시지 자체 거부)하고, `rx/ry/rz`는 0 또는 `point_count` 길이만 허용
- ⚠️ **주의(잠재 결함, 이 스냅샷에도 남아있음)**: `msg_to_stroke()`가 `zip(msg.x, msg.y, z_list, rx_list, ry_list, rz_list)`로 점을 구성하기 때문에, `rx/ry/rz`가 빈 배열(길이 0)로 오면 검증은 통과하지만 `zip`이 즉시 종료되어 `points`가 통째로 빈 리스트가 됨(경고 로그만 남고 아무 동작도 안 함). `client_connector_node.py`는 실제로는 항상 4개 필드를 다 채워 보내므로 정상 경로에서는 발현하지 않지만, StrokeCmd를 직접 구성해 publish하는 다른 발신측(서버측 재실행 등)은 반드시 유의해야 함

실행 순서 (요약)

1. `client_connector_node.py` 기동 (`JsonSendNode`, `/calligraphy/input_json` 구독 대기)
2. `robot_write_drl_node.py` 기동 (`DR_init`/`DSR_ROBOT2` 임포트 필요 — 로봇/에뮬레이터 연결 대기)
3. `trigger_node.py` 기동 → `watch_dir` 폴링 시작(또는 `svg_to_robot.py`를 1회 직접 실행해 특정 SVG 처리)
4. `watch_dir`에 `Canvas*.svg`가 생기면 자동으로 파싱 → 곡면 투영 → publish → 로봇 필기 실행

알려진 이슈 (확인 필요)

- `config.json`의 `workpiece.tilt_x`가 `0.0` — 계산식에는 여전히 `tilt_x` 보정 항이 남아있어 사실상 무효화된 상태로 보임, 실기 값(과거 5.85로 관찰된 적 있음)과 다른지 재확인 필요
- `config.json`의 `path.watch_dir`이 다른 계정(`woods`) 경로로 하드코딩 — 실제 배포 계정과 일치하는지 확인 필요
- `svg_to_robot.py`의 `part_name`이 `"original_drawing"`으로 하드코딩 — 어떤 SVG가 감시 디렉토리에 들어와도 항상 동일 `part_name`으로 제출됨, 여러 작업을 구분해 관리하려면 매개변수화 필요
- `svg_to_robot.py`의 `process_and_package()`에 `print(f"DEBUG: ...")` 한 줄이 남아있음 — 정리 대상으로 보이는 디버그 잔재
- `svg_parser.py`는 이 스냅샷 내 어디서도 import되지 않음 — 미사용 모듈로 보이나, 다른 패키지(예: 서버측)에서 참조할 가능성은 배제하지 않음
- `robot_write_drl_node.py`의 `rx/ry/rz` 누락 시 stroke 소실 버그(위 토픽 스키마 절 참고)는 여전히 코드에 남아있음 — 정상 경로에서는 발현하지 않으나 잠재 결함으로 기록

알려진 제약 / TODO

- 서버측 `main_designing.js`의 `FRUSTUM_CENTER_X` 상수(177.00)가 이 `config.json`의 `center_x`(172.00)와 5.0 어긋나 있음 — 두 패키지가 서로 독립 프로세스로 로드되어 자동 동기화되지 않으므로 수동 동기화 필요(server_README.md 참고)
- 붓펜 힘제어 상수(`TARGET_PEN_FORCE_N=4.0`/`MAX_PEN_FORCE_N=12.0`/`PEN_COMPLIANCE_STIFFNESS`)가 JSON이 아니라 코드에 하드코딩된 채로 남아있음 — 파일 상단 주석에 "JSON 정리 전까지" 임시값이라고 명시되어 있어 향후 `calligraphy_config.json`으로의 이관이 남은 작업
- `draw_stroke()`의 과압 감지(`check_pen_pressure_or_raise`)가 `RuntimeError`를 던지면 해당 stroke만이 아니라 `execute_ready_job_once` 전체가 중단됨 — "과압 시 해당 stroke만 건너뛰고 계속" 동작은 아직 구현되어 있지 않음
- `setup.py`/`package.xml`이 이 스냅샷에 없어 실제 ROS2 콘솔 스크립트(entry_point) 이름은 확정할 수 없음 — 작업공간에 병합할 때 확인 필요
