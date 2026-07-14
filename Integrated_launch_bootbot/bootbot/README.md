# rokey_bootbot

M0609 + RG2 로봇 한글 서예 파이프라인 중 **server_connector_node**와 **GUI 웹** 담당 ROS2(ament_python) 패키지.

> 네이밍: 실제 ROS2 노드는 이름 끝에 `_node` 부착. `gui_web`은 rclpy 미사용 순수 Flask 프로세스라 미부착. `mongo_repository.py`/`svg_parser.py`는 헬퍼 모듈.

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
- GUI 웹 SVG 업로드 폼(`user_designing.html`/`main_designing.js`의 `char-request-form`) 존재하나 **백엔드 미처리(죽은 경로)** — `_on_ws_message`가 `replay`/`manager_cmd` 외 메시지는 에러 로그만 남기고 무시. 실제 제출은 `calligraphy_robot`(범위 밖) 담당, `part_name`도 업로드 파일명과 무관
- `rokey_bootbot/svg_parser.py`: 삭제된 테스트 노드의 SVG 파싱 헬퍼, 현재 미사용

## GUI 실시간 붓끝 좌표

**소스**
- 실제 붓끝(TCP) 위치 = 티치펜던트 등록 도구 오프셋 반영 필요
- `/dsr01/aux_control/get_current_posx`(`dsr_msgs2/srv/GetCurrentPosx`) = 유일한 정확한 실시간 소스
- `server_connector_node`가 주기 폴링(`posx_poll_period_sec`, 기본 0.1초)

**폴링/broadcast 게이팅**
- 폴링: 노드 기동 시점부터 무조건 실행 — 첫 요청 DDS discovery 지연 회피 목적
- GUI broadcast: `_active_part_name` 설정 시에만 수행
- `_active_part_name`: `/calligraphy/stroke_cmd` 수신 또는 재실행 시 세팅, 자동 해제 없음(완료 신호 부재)

**action 판정**
- 응답 `z`값과 `pen_z_down`(기본 **91.0**, `calligraphy_robot/calligraphy_config.json`의 `robot.z_down`과 일치 필수 — 자동 동기화 없음) 간 차이를 `pen_z_draw_tolerance`(기본 3.0)와 비교
- 이내 → `draw`, 초과 → `move`

**`header.session_seq`**
- 폴링 경로: `stroke_id` 항상 0 → 새 세션 시작 구분 불가
- `_on_stroke_cmd`가 `stroke_id==1`마다 `_active_session_seq` 증가 후 동봉
- 프론트: 값 변경 시 캔버스 초기화

**좌표 규격**
- 기본값: 캔버스 픽셀(`0~800`, `0~680`)
- `header.coord_space === 'robot_mm'`(폴링 경로 전용) 시: `main_designing.js`의 `robotMmToCanvasPx()`가 letterbox-fit + Y축 반전 적용(로봇 mm 좌표계-화면 Y축 방향 반대, 180° 회전과 구분 필요)
- `ROBOT_X_MIN/MAX=351/431`, `Y_MIN/MAX=-21/29`: 로봇 실제 작업 반경과 일치 필요
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
  - USER: 캔버스 + 업로드 폼 + 작성 기록
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

**`/calligraphy/stroke_cmd`** (`calligraphy_interfaces/StrokeCmd`)
- DB 저장 기준이자 재실행 발행 대상
- 필드: `total_count`, `stroke_id`, `part_name`, `action`, `float64[] x`, `float64[] y`
- 구독(`_on_stroke_cmd`)/발행(`_stroke_cmd_pub`) 모두 이 노드가 보유 → 재실행이 자기 구독 콜백 재트리거 → 새 세션 이력 자동 기록

**`/handwriting/stroke_plan`** (`std_msgs/String`, 사실상 미사용)
- 과거 재실행 경로 흔적(`_on_stroke_plan`, `_stroke_plan_pub`) 잔존
- `/calligraphy/stroke_cmd` 직접 발행 방식 전환 이후 publish 없음
- 사용자 확인 없이 미삭제, 현행 유지

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

```json
{"name": "장동일", "svg": "<svg>...</svg>"}
```
죽은 경로 — 미처리.

서버 → 브라우저 (붓끝 pen_point):
```json
{"header": {"part_name": "장동일", "stroke_id": 0, "coord_space": "robot_mm", "session_seq": 3}, "action": "move", "point": {"x": 380.5, "y": 3.2}}
```
- `action: "move"` = 선 미연결 이동 / `"draw"` = 이전 점과 선 연결
- `stroke_id`: 이 경로 항상 `0`(세션 판단은 `session_seq` 사용)
- `coord_space` 존재 시 좌표 변환 대상(위 절 참고)
- `{"type": "dashboard"|"manager_log", ...}` 메시지도 동일 소켓 공유 — `main_designing.js`는 `type` 필드 존재 시 건너뜀, `manager_designing.js`가 처리

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
     "strokes": [{"stroke_id": 1, "action": "draw", "points": [{"x": 50, "y": 50}], "updated_at": "..."}]}
  ]
}
```

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
   - 주요 파라미터: `mongo_uri`, `ws_host`/`ws_port`(기본 `0.0.0.0`/`8765`), `robot_ns`(기본 `dsr01`), `posx_poll_period_sec`(0.1), `pen_z_down`(91.0)/`pen_z_draw_tolerance`(3.0), `manager_poll_period_sec`(0.5)
2. `ros2 run rokey_bootbot gui_web`
      → `http://<PC IP>:5000`
   - 환경변수: `GUI_WEB_PORT`, `WS_HOST`/`WS_PORT`, `MONGO_URI`
3. 작업 제출: `calligraphy_robot` 쪽(`client_connector_node`, `robot_write_drl_node`, `svg_parser`
      — 범위 밖, 절차는 `calligraphy_robot/run_command.txt` 참고)
   - `server_connector_node`가 결과 `/calligraphy/stroke_cmd` 자동 감지
4. (선택) USER 탭 "작성 기록" 재실행, `/manager` 모니터링/제어

**검증**
- USER 탭 캔버스 stroke 실시간 렌더링 확인
- `mongosh handwriting_db --eval "printjson(db.stroke_plans.findOne({_id:'<part_name>'}))"`로 저장 구조 확인
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

## 알려진 제약 / TODO

- `/manager` 관리자 인증 미구현(게이트 버튼 무조건 통과)
- USER 탭 SVG 업로드 폼: 백엔드 미처리 죽은 경로 잔존(정리 미완)
- `rokey_bootbot/svg_parser.py`: 미사용 모듈 잔존(정리 미완)
- `_broadcast`는 `client.send()`를 `asyncio.gather(..., return_exceptions=True)`로 감싸 개별 클라이언트 전송 실패를 무시만 함 — 핑퐁/좀비 연결 정리 등 정교한 연결 관리 없음
- `session_id`: 프로세스 메모리 전용 저장 — 재시작 시 진행 세션 단절
- 재실행의 `/calligraphy/stroke_cmd` publish 하위 우회(로봇 movel 직접 호출) 방안: 설계 합의만, 미구현
- `stroke_id==1` 중복 전송 시 빈 세션 추가 가능(데이터 유실 없음)
- 재실행: 항상 마지막 세션만 재생 — 과거 특정 세션 선택 재생 미지원
- MANAGER 3D 뷰어 `joints_deg`가 이유 없이 `[0,0,0,0,0,0]` 고정된 채 미동작 사례 1건 관찰(원인 미조사)
