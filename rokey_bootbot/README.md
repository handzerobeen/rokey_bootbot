# rokey_bootbot

M0609 + RG2 로봇 한글 서예 파이프라인 중 **server_connector_node**와 **GUI 웹** 담당 ROS2(ament_python) 패키지.

> 네이밍: 실제 ROS2 노드는 이름 끝에 `_node` 부착. `gui_web`은 rclpy 미사용 순수 Flask 프로세스라 미부착. `mongo_repository.py`는 헬퍼 모듈(`svg_parser.py`는 죽은 코드 정리로 삭제됨, 아래 "노드 구성" 절 참고).

## 노드 구성

**`server_connector_node`(production)**
- `/calligraphy/stroke_cmd`(`calligraphy_interfaces/StrokeCmd`, `calligraphy_robot` 패키지 publish — 범위 밖) 구독
- 역할: (1) MongoDB 저장, (2) GUI 실시간 좌표 broadcast, (3) MANAGER 대시보드(모니터링+제어)
- SVG 제출 경로 비관여
- `{"replay": "<part_name>"}` 수신 시 저장된 마지막 세션을 `/calligraphy/stroke_cmd`로 재발행

**`server_connector_node_designing`**
- production의 실험용 복제본(상속 아님, 완전 별도 `Node` 서브클래스)
- 신기능 우선 검증 후 diff-and-copy로 production 반영
- 현재 production과 사실상 동일
- 두 노드 동시 실행 금지(포트 8765 공유)

**기타**
- 과거 로봇/Mongo 무의존 로컬 테스트 노드(`server_connector_node_test.py`) 삭제됨 — 현재 단독 실행 모드 없음
- GUI 웹 SVG 업로드 폼(`user_designing.html`의 `char-request-form`/`main_designing.js`의 업로드 핸들러)은 완전히 삭제됨 — USER 탭에는 재실행 상태 메시지용 `#char-request-status`만 남아있음. 실제 제출은 여전히 `calligraphy_robot`(범위 밖) 담당, `part_name`도 업로드 파일명과 무관
- `rokey_bootbot/svg_parser.py`: 삭제된 테스트 노드의 SVG 파싱 헬퍼였으나, 전체 죽은 코드 정리(2026-07-10)로 파일 자체가 삭제됨 — 더 이상 이 패키지에 존재하지 않음

## GUI 실시간 붓끝 좌표

**소스**
- 실제 붓끝(TCP) 위치 = 티치펜던트 등록 도구 오프셋 반영 필요
- `/dsr01/aux_control/get_current_posx`(`dsr_msgs2/srv/GetCurrentPosx`) = TCP 위치 소스, `ref=tcp_ref_coord`(기본 **101**, 티치펜던트에 등록된 사용자 좌표계 "bootbot")로 호출 — `robot_write_drl_node`의 `movel(ref=101)`과 반드시 같은 프레임이어야 곡면 펼치기 계산(아래 좌표 규격 절)이 어긋나지 않음
- `/dsr01/aux_control/get_tool_force`(`dsr_msgs2/srv/GetToolForce`) = 접촉력 소스 — **`ref` 필드는 `0=BASE/1=TOOL/2=WORLD`만 지원**(`dsr_controller2.cpp`의 `get_tool_force_cb` 확인, `GetCurrentPosx`처럼 임의의 티치 좌표계를 받지 못함), 그래서 항상 `ref=0`(BASE)로 고정 호출. 값 자체는 Doosan 펌웨어의 모델 기반 외력 추정치(`fActualETT`, 관절 토크 실측-기대 잔차)라 이 노드의 자체 계산이 아님 — 고동역학 구간(가감속)에서 노이즈가 커지는 것도 이 추정 방식 자체의 특성
- `server_connector_node`가 두 서비스 모두 동일 패턴(in-flight 가드)으로 독립 폴링(`posx_poll_period_sec`, 기본 0.1초)

**폴링/broadcast 게이팅**
- 폴링: 노드 기동 시점부터 무조건 실행 — 첫 요청 DDS discovery 지연 회피 목적
- GUI broadcast: `_active_part_name` 설정 시에만 수행
- `_active_part_name`: `/calligraphy/stroke_cmd` 수신 또는 재실행 시 세팅, 자동 해제 없음(완료 신호 부재)

**action 판정 — 힘(Z축) OR 속도, 2026-07-13 실기 튜닝으로 확정**
- 최종 로직: `action = 'draw' if (접촉력 >= pen_contact_force_n) or (이동속도 <= pen_move_speed_max_mm_s) else 'move'`
- 접촉력: `get_tool_force` 응답의 **Z축 힘 성분 절대값**(`abs(tool_force[2])`) — `calligraphy_robot`의 `robot_write_drl_node.py` 과압 감시(`PEN_FORCE_AXIS_INDEX=2`)와 같은 축 기준으로 맞춘 것. 3축 magnitude는 이동 중 x/y축 잔차 노이즈까지 합산돼 실기에서 오탐이 잦아 폐기됨
- 이동속도: `get_current_posx` 연속 응답의 위치차/시간차로 추정(새 서비스 호출 없이 기존 폴링 재사용). 자모 하나가 15개 점으로 촘촘히 쪼개져 있어 실제 필기 중 구간 이동은 느리고, 획 사이 이동은 빠르다는 특성을 이용
- OR로 묶는 이유: 접촉력 센서(모델 기반 외력 추정)가 하드웨어 특성상 순간적으로 못 잡는 경우가 있어(실기 확인), 힘 조건 하나만으로는 실제 필기 중에도 `move`로 잘못 끊긴다. 대신 OR는 힘 노이즈로 인한 이동 중 오탐(false positive)은 억제하지 못한다는 트레이드오프를 감수한 선택(AND였다면 그 반대: 오탐은 줄지만 센서가 잠깐 놓친 진짜 필기를 놓칠 위험)
- **폐기된 시도(재도입 금지)**: min/max 힘 밴드(`3.0~5.0N` 사이만 `draw`)는 획 전환 중 살짝 스치는 진동까지 밴드 안에 잡혀 실기에서 완전히 실패, 원복됨
- z 높이가 아니라 힘/속도로 판단하는 이유: 곡면(원뿔대) 작업물에서는 "쓰는 중" z 자체가 위치마다 달라져 고정 z 임계값이 성립하지 않기 때문
- 현재 파라미터 기본값은 `pen_contact_force_n`(0.7N), `pen_move_speed_max_mm_s`(3.5mm/s) — 둘 다 실기에서 계속 미세 튜닝 중인 값, 최종 확정 아님

**`header.session_seq`**
- 폴링 경로: `stroke_id` 항상 0 → 새 세션 시작 구분 불가
- `_on_stroke_cmd`가 `stroke_id==1`마다 `_active_session_seq` 증가 후 동봉
- 프론트: 값 변경 시 캔버스 초기화

**좌표 규격**
- 기본값: 캔버스 픽셀(`0~800`, `0~680`)
- `header.coord_space === 'robot_mm'`(폴링 경로 전용) 시: `main_designing.js`가 원뿔대(frustum) 곡면 펼치기(unwrap)를 역산한 뒤 letterbox-fit + Y축 반전(`h`축에만, X축 그대로) 적용
- 펼치기 상수: `FRUSTUM_CENTER_X=177.00`, `FRUSTUM_CENTER_Y=42.00`, `FRUSTUM_H=220.0`, `FRUSTUM_RB=50.0`, `FRUSTUM_RT=52.5`, `DRAWING_SCALE=80.0` — `calligraphy_robot/config.json`의 `robot.center_x/center_y`, `workpiece.H/Rb/Rt`, `drawing.scale`과 일치해야 함(두 패키지가 독립 프로세스라 자동 동기화 없음)
- **⚠️ 확인된 불일치 — `calligraphy_robot`의 실제 라이브 `config.json`은 여전히 평면(flat) 스키마**(`robot.center_x=376, center_y=4`, `drawing.scale=50, num_points=7`, `workpiece` 섹션 자체가 없음)**, 위 프러스텀 상수와 완전히 다른 값**. 즉 이 노드/프론트의 곡면-펼치기 좌표 로직은 로봇측이 프러스텀 재설계를 병합하기 전까지는 실제 파이프라인에서 의미 있게 검증되지 않은 상태 — `calligraphy_robot`(범위 밖, 아직 미병합)이 병합되기 전에는 GUI 실시간 좌표 표시가 실제 필기 위치와 맞지 않을 수 있음. rokey_bootbot 쪽에서 고칠 수 있는 문제가 아니라 로봇측 병합을 기다려야 함
- **변환 위치 = 항상 프론트엔드** — 백엔드 저장/전달 좌표는 항상 로봇 raw 물리 좌표

**재실행**
- USER 탭 "작성 기록" → 실행 클릭 → `{"replay": "<part_name>"}` 전송
- 저장된 마지막 세션을 `/calligraphy/stroke_cmd`에 그대로 재발행(`_replay_strokes`, 별도 스레드, stroke 간 0.2초 간격, 전체 stroke 동일 `total_count`)
- 재발행도 `_on_stroke_cmd`에 잡혀 새 세션으로 이력 기록
- 로봇 타 작업 실행 중이면 미반영 가능(실패 감지 불가)

**WebSocket 서버**
- `server_connector_node`: `rosbridge_suite` 미의존, `websockets`로 노드 프로세스 내 자체 서버 직접 실행

## GUI 웹 페이지 구조

Flask(`gui_web/app.py`), 라우트 3개.

- **`/` → `landing.html`**: 배경 이미지 + "붓봇 사용하러 가기" 버튼만 있는 랜딩 페이지
- **`/user` → `user_designing.html`**
  - **USER/DATABASE 탭**(클라이언트 사이드 전환, `tabs_designing.js`)
  - USER: 캔버스 + 접촉력 실시간 표시(`#contact-force`) + 작성 기록(SVG 업로드 폼은 삭제됨, 위 "노드 구성" 절 참고)
  - DATABASE: part_name 목록 + 상세 조회 + 삭제(Yes/No 커스텀 모달)
  - `#user`/`#database` 해시 딥링크 지원
  - `main_designing.js`가 WebSocket 개설, `window.__rokeySocket`으로 전역 공유
- **`/manager` → `manager_designing.html`**: MANAGER 대시보드(아래 절)
  - 별도 라우트 사유: 관리자 인증 필요 설계
  - **단, 실제 인증 로직 미구현** — 게이트 버튼 클릭 시 무조건 통과

## MANAGER 대시보드

로봇 모니터링(TCP/조인트/상태/통신) + 제어(정지/재개/비상정지/안전상태 해제/조인트 이동) + 3D 뷰어. `manager_poll_period_sec`(기본 0.5초) 주기 broadcast, `_active_part_name`과 무관.

| 기능 | 인터페이스 |
|---|---|
| TCP 좌표 | `aux_control/get_current_posx` |
| 조인트 각도 | `/joint_states` 구독 |
| 로봇 상태/모드/속도모드 | `system/get_robot_state` / `get_robot_mode` / `get_robot_speed_mode` |
| 에러/충돌 | `/{robot_ns}/error` 구독(이벤트 로그 스트림, 상태값 아님) |
| 통신 끊김 | `/{robot_ns}/robot_disconnection` 구독 |
| 조인트 이동 | `motion/move_joint`(필수 `sync_type=1` ASYNC — SYNC 시 콜백 그룹 공유로 정지/재개까지 데드락) |
| 정지/재개 | `motion/move_pause` / `motion/move_resume` |
| 비상정지 | `system/servo_off`(EMERGENCY) |
| 안전상태 해제 | `system/set_robot_control` |

**세부 사항**
- Collision: 전용 인터페이스 없음 → `/error`의 특정 에러 코드(`9011`/`7060`/`7067`)로 추정(확정 매핑 아님)
- `/error` 레벨별 분리: WARN → Fault 칸, ERROR → 로그창(중복 노출 방지 목적)
- **정지/재개 ≠ `move_stop`+`set_robot_control` 조합** — 해당 조합은 안전상태 복구용 API(실기 테스트로 "정지 후 재개" 불가 확인). 실사용: `move_pause`/`move_resume`
- wire 프로토콜 `manager_cmd` 문자열(`move_stop`/`reset_safe_stop`)은 옛 이름 유지 — 이름과 실제 동작 불일치 주의
- 정지/재개/비상정지/안전상태 해제: `confirm()` 없이 즉시 실행
- 조인트 이동: 확인 절차 유지(목표 각도 오입력 리스크)

서버 → 브라우저 (0.5초 주기 대시보드 스냅샷):
```json
{
  "type": "dashboard",
  "connected": true,
  "tcp": {"x": 380.5, "y": 3.2, "z": 91.2, "w": 180.0, "p": 0.0, "r": 90.0, "action": "draw"},
  "joints_deg": [0, 0, 90, 0, 90, 0],
  "robot_state": {"code": 1, "label": "STANDBY"},
  "robot_mode": {"code": 1, "label": "AUTONOMOUS"},
  "speed_mode": {"code": 0, "label": "NORMAL"},
  "fault": null,
  "collision": null,
  "comm": {"ws_clients": 1, "poll_period_sec": 0.1, "last_disconnection_at": null}
}
```

서버 → 브라우저 (로그창):
```json
{"type": "manager_log", "text": "정지(move_pause) 성공"}
```

브라우저 → 서버 (`manager_cmd` 값별 의미):
- `move_stop` — 정지(내부: `move_pause`)
- `reset_safe_stop` — 재개(내부: `move_resume`)
- `servo_off_emergency` — 비상정지
- `reset_safety_state` — 안전상태 해제(SAFE_STOP/SAFE_OFF류 전용)
- `move_joint` — 조인트 이동, 예: `{"manager_cmd": "move_joint", "pos": [0,0,90,0,90,0], "vel": 30, "acc": 30}`

**3D 뷰어(프로토타입)**
- 별도 백엔드 없음 — 기존 broadcast `joints_deg` 재사용
- `manager_viewer_designing.js`: Three.js + `urdf-loader`를 `esm.sh` CDN에서 로드(최초 1회만 인터넷 필요, 이후 로컬 WebSocket만 사용)
- 정적 URDF(`gui_web/static/urdf/`, xacro/`package://` 사전 전개 사본) 렌더링

## 토픽 / 메시지 스키마

**`/calligraphy/stroke_cmd`** (`calligraphy_interfaces/StrokeCmd`, **6DOF로 확장됨**)
- DB 저장 기준이자 재실행 발행 대상
- 필드: `total_count`, `stroke_id`, `part_name`, `action`, `float64[] x`, `y`, `z`, `rx`, `ry`, `rz` (2026-07-10, 로봇측 원뿔대 재설계에 맞춰 `z/rx/ry/rz` 추가·확인됨)
- 구독(`_on_stroke_cmd`)/발행(`_stroke_cmd_pub`) 모두 이 노드가 보유 → 재실행이 자기 구독 콜백 재트리거 → 새 세션 이력 자동 기록
- **⚠️ 확인된 위험 — `calligraphy_robot`의 실제 라이브 `connector_node.py`(`stroke_to_msg`)는 여전히 `msg.x`/`msg.y`만 채우고 `z`/`rx`/`ry`/`rz`는 손대지 않는다.** ROS2 메시지에서 미설정 `float64[]` 필드는 빈 배열로 남으므로, 실제 로봇이 지금 이 노드로 보내는 `StrokeCmd`는 `z=rx=ry=rz=[]`인 채로 도착한다. `_on_stroke_cmd`의 `zip(msg.x, msg.y, msg.z, msg.rx, msg.ry, msg.rz)`는 가장 짧은 배열(빈 배열) 기준으로 즉시 끝나므로 **`points`가 통째로 빈 리스트가 되고, 에러 없이 그대로 Mongo에 저장된다** — 즉 `calligraphy_robot`이 프러스텀 설계로 병합되기 전까지, 실제 로봇이 새로 쓰는 모든 stroke는 DB에 좌표 없이(빈 `points`) 저장될 위험이 있음. (재실행 `_replay_strokes`는 `.get(key, 0.0)` 폴백이 있어 이 문제와 무관 — 새 실기 제출 경로에만 해당)

**WebSocket** (`ws://<host>:8765`, 양방향)

브라우저 → 서버:
```json
{"replay": "장동일"}
```
저장된 마지막 세션을 `/calligraphy/stroke_cmd`로 재발행.

```json
{"manager_cmd": "move_stop"}
```
MANAGER 제어 명령(값별 의미는 위 MANAGER 절 참고).

서버 → 브라우저 (붓끝 pen_point):
```json
{"header": {"part_name": "장동일", "stroke_id": 0, "coord_space": "robot_mm", "session_seq": 3}, "action": "move", "point": {"x": 380.5, "y": 3.2}}
```
- `action: "move"` = 선 미연결 이동 / `"draw"` = 이전 점과 선 연결(위 "action 판정" 절의 힘 OR 속도 기준)
- `stroke_id`: 이 경로 항상 `0`(세션 판단은 `session_seq` 사용)
- `coord_space` 존재 시 좌표 변환 대상(위 절 참고)

서버 → 브라우저 (접촉력, 폴링 주기마다 그대로, 스로틀 없음):
```json
{"type": "contact_force", "force_n": 0.42}
```
USER 탭 캔버스 아래 `#contact-force` 표시 전용 — 로그(`get_logger`)와 달리 매번 값을 그대로 덮어써서 보여준다(MANAGER TCP 표시와 동일 방식).

- `{"type": "dashboard"|"manager_log"|"contact_force", ...}` 메시지도 동일 소켓 공유 — `main_designing.js`는 이 세 `type` 모두 자기 처리 대상이 아니면 건너뜀(`contact_force`만 별도 처리), `manager_designing.js`가 `dashboard`/`manager_log`를 처리

**HTTP** (`gui_web`, MongoDB 직접 조회/수정)
- `GET /api/history`: `part_name` 목록(오름차순)
- `GET /api/history/<part_name>`: 세션/stroke 요약(`points`는 무거워 `point_count`로 요약, `sessions`는 응답에서만 최신순 역정렬)
- `DELETE /api/history/<part_name>`: 문서 전체(전 세션) 삭제
- `DELETE /api/history/<part_name>/sessions/<session_id>`: 세션 단건 삭제, 마지막 세션 시 문서도 함께 삭제(`part_name_removed: true`)

## MongoDB 저장 구조

- DB `handwriting_db`, 컬렉션 `stroke_plans`
- **`part_name` 1개 = 문서 1개**(`_id` = `part_name`) → `sessions[]` → `strokes[]`
- `session_id`: 서버 자체 발급 — `stroke_id==1`마다 새 세션 push, 이후 동일 세션 stroke는 그 안에 누적
- 동일 `stroke_id` 재전송 시 덮어쓰기 / 세션 상이(같은 글자 재작성) 시 새 세션 추가, 이력 보존

```json
{
  "_id": "장동일", "part_name": "장동일",
  "sessions": [
    {"session_id": "3f9a1c2e", "started_at": "2026-07-06T05:00:00",
     "strokes": [{"stroke_id": 1, "action": "draw",
       "points": [{"x": 50, "y": 50, "z": 91.0, "rx": 34.3, "ry": -178.6, "rz": 39.1}],
       "updated_at": "..."}]}
  ]
}
```

- `points`는 `z/rx/ry/rz`까지 6DOF 전부 담는 게 설계 의도지만, 위 "토픽/메시지 스키마" 절에서 확인된 대로 **현재는 로봇측이 아직 이 필드들을 안 채워 보내 실제로는 빈 `points`가 저장될 위험이 있음** — 이 문서만으로 최근 저장된 문서가 실제로 좌표를 담고 있는지 판단하지 말고, `mongosh`로 직접 확인할 것(아래 "검증" 절)
- `started_at`/`updated_at`: UTC 아닌 **naive 한국 로컬 시각**(한국 단일 지역 배포 전제)
- 재실행: `sessions` 배열 마지막 세션을 `get_latest_session_strokes()`로 조회, 그대로 재전달

## 설치 / 빌드 / 실행

```bash
# 최초 1회: MongoDB 7.0 + Flask/pymongo/websockets
curl -fsSL https://pgp.mongodb.com/server-7.0.asc | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt update && sudo apt install -y mongodb-org python3-flask python3-pymongo python3-websockets
sudo systemctl enable --now mongod

# 빌드
cd ~/ws_cobot_pjt/ws_dsr && colcon build --symlink-install --packages-select rokey_bootbot && source install/setup.bash
```

- `setup.py`의 `data_files`: 파일 전체 명시적 나열, glob 없음
- `gui_web/templates`/`static` 신규 파일 추가 시 `setup.py`도 함께 수정 필요(누락 시 빌드 에러 없이 신규 파일 미노출 — Flask는 `install/` 경로에서 서빙)

**실행** (launch 파일 없음, 개별 프로세스)
- MongoDB 미기동 시(`systemctl status mongod`) `server_connector_node`가 `ServerSelectionTimeoutError`로 종료

1. `ros2 run rokey_bootbot server_connector_node`(또는 `_designing`, 동시 실행 금지 — 포트 8765 공유)
   - 주요 파라미터: `mongo_uri`, `ws_host`/`ws_port`(기본 `0.0.0.0`/`8765`), `robot_ns`(기본 `dsr01`), `posx_poll_period_sec`(0.1), `tcp_ref_coord`(101), `pen_contact_force_n`(0.7)/`pen_move_speed_max_mm_s`(3.5, 둘 다 실기 튜닝 진행 중), `manager_poll_period_sec`(0.5)
2. `ros2 run rokey_bootbot gui_web`
      → `http://<PC IP>:5000`
   - 환경변수: `GUI_WEB_PORT`, `WS_HOST`/`WS_PORT`, `MONGO_URI`
3. 작업 제출: `calligraphy_robot` 쪽(`client_connector_node`, `robot_write_drl_node`, `svg_parser`
      — 범위 밖, 절차는 `calligraphy_robot/run_command.txt` 참고)
   - `server_connector_node`가 결과 `/calligraphy/stroke_cmd` 자동 감지
4. (선택) USER 탭 "작성 기록" 재실행, `/manager` 모니터링/제어

**검증**
- USER 탭 캔버스 stroke 실시간 렌더링 확인(단, `calligraphy_robot`이 프러스텀 미병합 상태면 좌표가 실제 위치와 안 맞을 수 있음 — 위 "좌표 규격" 절 참고)
- USER 탭 `#contact-force` 표시가 실기 필압에 반응하는지 확인
- `mongosh handwriting_db --eval "printjson(db.stroke_plans.findOne({_id:'<part_name>'}))"`로 저장 구조 확인 — 특히 `strokes[].points`가 실제로 `x/y/z/rx/ry/rz`를 담고 있는지(빈 배열이면 위 "토픽/메시지 스키마" 절의 알려진 위험이 발현된 것)
- 재실행 후 `sessions` 배열 증가 확인
- `/manager` 값 채움 및 제어 버튼 실동작 확인

## 알려진 이슈 (코드 수정 시 주의)

- **`python3-websockets`(apt, 9.1) ↔ Python 3.10 비호환**
  - `loop=` 인자 관련 `TypeError` 방지용 monkeypatch, `server_connector_node.py`/`server_connector_node_designing.py` 최상단 위치
  - "미사용 코드"로 오판 후 삭제 시 클라이언트 접속 즉시 종료
  - 두 파일 독립 중복 보유 — 수정 시 양쪽 확인 필수
- **`self._ws_clients` 네이밍**: `rclpy.Node` 내부적으로 `self._clients` 사용 — 동일 이름 사용 시 executor 종료(실제 발생 이력)
- **재실행은 별도 스레드 필수**: WebSocket 콜백 내 직접 실행 시 stroke 간 `sleep`이 asyncio 이벤트 루프 차단 → 다른 broadcast/메시지 처리 정지
- **`stroke_id==1` 메시지 유실 시 세션 자가복구**: `MongoStrokeRepository.upsert_stroke()`는 세션이 아직 없는 상태로 `$push`가 실패(`matched_count==0`)하면 그 자리에서 `start_session`을 다시 호출해 세션을 만들고 재시도. "중복 코드"로 보고 제거 시 세션 시작 메시지 유실 순간 해당 세션의 나머지 stroke 전부 조용히 소실
- **`hidden` + `display:` CSS 동시 사용 시 `hidden` 무시 가능**: 명시도 동일 → 작성자 규칙 우선 적용 → JS 에러 없이 "패널 노출 지속"으로만 발현. 해결: `.foo[hidden] { display: none; }` 동일 파일 추가
- **`ros2 run ... &`을 `kill`해도 실제 자식 프로세스 잔존 가능**: 포트 8765 점유 지속 → 신규 인스턴스 WebSocket 서버 bind 실패(무소음). wrapper 아닌 실제 자식 PID 종료 필요
- **`get_tool_force`의 `ref`는 `0=BASE/1=TOOL/2=WORLD`만 지원**: `GetCurrentPosx`처럼 임의의 티치 좌표계(예: 101)를 넘기면 조용히 `default:` 분기로 빠져 BASE로 처리됨(WARN 로그만 남음, 에러 아님) — 이 필드에 `tcp_ref_coord` 같은 사용자 좌표계 값을 넘기려는 시도는 겉보기와 달리 아무 효과가 없다는 점 유의
- **min/max 힘 밴드 방식은 이미 시도·폐기됨**: 위 "action 판정" 절 참고 — 획 전환 중 미세한 진동이 밴드 안에 들어와 실기에서 완전히 실패했던 접근이므로, 같은 아이디어를 다시 시도하기 전에 이 이력부터 확인할 것

## 알려진 제약 / TODO

- `/manager` 관리자 인증 미구현(게이트 버튼 무조건 통과)
- **`calligraphy_robot`이 아직 프러스텀 재설계를 병합하지 않음** — 이 노드가 이미 가정하는 6DOF `StrokeCmd`/곡면 펼치기 좌표/`tcp_ref_coord=101` 프레임이 로봇측 실제 라이브 코드(`config.json` 평면 스키마, `connector_node.py`의 x/y만 채우는 `stroke_to_msg`)와 어긋나 있음 — 위 "좌표 규격", "토픽/메시지 스키마" 절의 두 확인된 위험이 전부 이 병합 지연에서 비롯됨. `rokey_bootbot` 쪽에서 단독으로 해소할 수 없고 로봇측 병합이 선행되어야 함
- `pen_contact_force_n`(0.7N)/`pen_move_speed_max_mm_s`(3.5mm/s): 실기 튜닝 진행 중, 최종값 아님
- `_broadcast`는 `client.send()`를 `asyncio.gather(..., return_exceptions=True)`로 감싸 개별 클라이언트 전송 실패를 무시만 함 — 핑퐁/좀비 연결 정리 등 정교한 연결 관리 없음
- `session_id`: 프로세스 메모리 전용 저장 — 재시작 시 진행 세션 단절
- 재실행의 `/calligraphy/stroke_cmd` publish 하위 우회(로봇 movel 직접 호출) 방안: 설계 합의만, 미구현
- `stroke_id==1` 중복 전송 시 빈 세션 추가 가능(데이터 유실 없음)
- 재실행: 항상 마지막 세션만 재생 — 과거 특정 세션 선택 재생 미지원
- MANAGER 3D 뷰어 `joints_deg`가 이유 없이 `[0,0,0,0,0,0]` 고정된 채 미동작 사례 1건 관찰(원인 미조사)
