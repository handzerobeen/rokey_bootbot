system_requirement — bootbot 실행 환경 요구사항

이 문서는 스냅샷의 코드가 실제로 전제하는 하드웨어/로봇 사전설정/OS/네트워크/소프트웨어 의존성을 한 곳에 모은 것이다. README.md/client_README.md/server_README.md 각각에 흩어져 있던 요구사항 중 "환경을 준비하는 사람"이 미리 알아야 할 항목만 추려 정리했다. 코드/설정 파일에서 직접 확인한 사실과, 이전 세션 기록(별도 확인 필요 표시)을 구분했다.

하드웨어

- **로봇 암**: 두산 로보틱스 M0609 (6축 협동로봇)
- **그리퍼**: OnRobot RG2
- **엔드이펙터**: 붓(브러시) — 티치펜던트에 Tool 등록 필요(아래 "로봇/그리퍼 사전 설정" 참고)
- **작업물(워크피스)**: 원뿔대(frustum) 형상 실물 — 실측 `H=220mm`, 바닥 반지름 `Rb=50mm`, 상단 반지름 `Rt=52.5mm` (`config.json` 기준값, 실물 치수가 이와 다르면 좌표 투영 자체가 틀어짐)
- (선택, 이 스냅샷 범위 밖) **RealSense 카메라** — 별도 워크스페이스 패키지(`m0609_rg2_bringup`)의 `bringup_camera.launch.py` 변형에서만 사용
- **로봇 컨트롤러 ↔ PC**: 유선 이더넷 전용(무선 불가 — 이전 세션에서 실기로 확인된 제약)

로봇/그리퍼 사전 설정 (코드가 "이미 되어 있다"고 가정하는 것 — 배포 전 티치펜던트에서 별도로 준비 필요)

- **사용자 좌표계 id 101("bootbot") 등록**: `robot_write_drl_node.py`가 `set_ref_coord(101)`로 이 좌표계 기준 `movel()`을 호출하고, `server_connector_node.py`의 `tcp_ref_coord` 파라미터 기본값도 101 — 실제 로봇에 이 좌표계가 티치펜던트로 등록되어 있어야 두 값이 같은 프레임을 가리킨다
- **Tool/TCP 등록**: `"Tool Weight"`(무게) / `"Tool_v1"`(TCP 오프셋) — `robot_write_drl_node.py`의 `set_tool()`/`set_tcp()` 호출이 이 이름으로 등록된 값을 그대로 불러 씀
- **9점 티칭 자세 데이터**(`calligraphy_config.json`의 `frustum_poses`): `get_interpolated_pose()` fallback 계산의 기준값 — 현재 파이프라인에서는 `svg_to_robot.py`가 매 점의 `rx/ry/rz`를 직접 계산해 보내므로 정상 경로에서는 호출되지 않는 fallback이지만, 값 자체는 실제 곡면 워크피스 위 9개 지점을 티칭한 결과여야 의미가 있음
- **붓 힘제어 파라미터**(로봇측 `robot_write_drl_node.py` 상단 하드코딩): `TARGET_PEN_FORCE_N=4.0`, `MAX_PEN_FORCE_N=12.0` — 실제 워크피스 재질/붓 종류가 바뀌면 재튜닝 필요(client_README.md 참고)

OS / 런타임

- **Ubuntu 22.04 LTS(jammy)** — MongoDB 7.0 공식 apt 저장소 설정이 jammy 기준(아래 "데이터베이스" 절 설치 명령 참고)
- **ROS2 Humble** — 이전 세션 기록 기준(이 스냅샷 자체에는 ROS2 배포판을 직접 명시하는 파일 없음, 배포 전 재확인 권장)
- **Python 3.10** — `__pycache__/*.cpython-310.pyc`로 확인됨. `server_connector_node.py` 최상단의 websockets monkeypatch가 Python 3.10에서 `asyncio.Lock/sleep/wait/wait_for`의 `loop=` 인자가 제거된 것을 전제로 하므로, 다른 Python 버전에서는 이 패치 자체가 불필요하거나 오히려 문제를 일으킬 수 있음
- **colcon 워크스페이스**(ament_python 패키지 전제) — 이 스냅샷 자체에는 `setup.py`/`package.xml`이 포함되어 있지 않음, 원본 워크스페이스(`~/ws_cobot_pjt/ws_dsr`) 쪽에서 확인 필요

네트워크

- **로봇 컨트롤러 IP**: `192.168.1.100` (고정)
- **OnRobot RG2 컴퓨트박스 IP**: `192.168.1.1` (고정, Modbus-TCP 502 포트)
- **필수 sysctl 설정**: `net.ipv4.ip_unprivileged_port_start=0` (두산 RT/모션 프로토콜이 저번호 포트를 쓰기 때문 — `/etc/sysctl.d/99-ros2-doosan.conf` 등으로 영속화 권장)
- **ROS2 DDS 통신**(서비스/토픽 전반)은 로컬 루프백 기준으로 동작 — 로봇 유선망(`192.168.1.x`)과는 완전히 별개 레이어이므로, DDS 쪽 문제와 로봇 유선 링크 문제를 혼동하지 않도록 구분해서 진단할 것
- **GUI 접근**: Flask(5000)와 WebSocket(8765) 모두 서버 프로세스가 도는 PC의 IP:포트로 브라우저에서 접근 가능해야 함(방화벽 확인)
- **MANAGER 3D 뷰어**: 최초 페이지 로드 1회에 한해 인터넷 연결 필요(`esm.sh` CDN에서 three.js/urdf-loader를 ES 모듈로 가져옴) — 이후 조인트 값 스트리밍은 이미 열린 로컬 WebSocket만 사용하므로 그 이후로는 인터넷 연결과 무관

데이터베이스

- **MongoDB 7.0** (`mongod` 서비스) — DB명 `handwriting_db`, 컬렉션명 `stroke_plans`
- 기본 접속 URI: `mongodb://localhost:27017` (`server_connector_node.py`의 `mongo_uri` 파라미터, `gui_web`의 `MONGO_URI` 환경변수로 각각 변경 가능 — 둘 다 같은 값을 가리켜야 함)
- 별도 인증/사용자 계정 설정 코드 없음 — 로컬 신뢰 환경(같은 PC 또는 신뢰된 네트워크) 전제

설치 예시(jammy 기준):

```bash
curl -fsSL https://pgp.mongodb.com/server-7.0.asc | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt update && sudo apt install -y mongodb-org
sudo systemctl enable --now mongod
```

파이썬 패키지 의존성

| 패키지 | 설치 경로 | 용도 | 비고 |
|---|---|---|---|
| rclpy | ROS2 배포판(apt) 동봉 | 모든 ROS2 노드 공통 | — |
| pymongo | apt `python3-pymongo` 또는 pip | MongoDB 클라이언트 | `mongo_repository.py`, `gui_web/app.py` |
| flask | apt `python3-flask` 또는 pip | GUI 웹서버 | `gui_web/app.py` |
| websockets | apt `python3-websockets`(9.1 확인됨) | 서버 자체 WebSocket 구현 | `server_connector_node.py` 최상단 monkeypatch와 반드시 함께 사용 — 최신 버전으로 교체 시 패치 코드 자체가 불필요해지므로 함께 제거 여부 검토 필요 |
| svgpathtools | pip `--user` (apt 미제공 확인됨) | SVG 경로 파싱/샘플링 | `svg_to_robot.py`. 이 환경에는 pip 자체가 기본 설치돼 있지 않을 수 있어 `get-pip.py` 부트스트랩이 먼저 필요했던 이력 있음 |
| DSR_ROBOT2 / DR_init | 두산 제공 SDK(`doosan-robot2` 패키지 동봉) | movel/force-control 등 실제 로봇 호출 | `robot_write_drl_node.py`. 로봇/에뮬레이터 없이도 import와 노드 기동까지는 되지만 `movel()` 등 실제 호출 시점에 블로킹/실패 |
| ament_index_python | ROS2 표준 | `get_package_share_directory`로 Flask 템플릿/정적 경로 탐색 | `gui_web/app.py` |

ROS2 패키지 의존성 (이 스냅샷에는 미포함 — 워크스페이스에 별도로 있어야 함)

- **doosan-robot2**(`dsr_controller2`, `dsr_msgs2`, `dsr_common2`, `dsr_description2` 등) — 벤더 제공
- **onrobot-ros2**(RG2 그리퍼 드라이버) — 벤더 제공
- **calligraphy_interfaces**(`InputJson` / `StrokeCmd`(6DOF) / `CurrentPoint` 메시지 정의) — 공용, 이 스냅샷에 `.msg` 파일 자체는 포함되어 있지 않음(사용처 코드로 스키마만 확인됨)
- **m0609_rg2_bringup**(실제/가상 브링업 launch, RViz) — 이 스냅샷 범위 밖, 로봇/에뮬레이터 기동에 필요

브라우저(클라이언트) 요구사항

- WebSocket 지원(표준 기능, 대부분의 최신 브라우저에서 지원)
- `<script type="module">` ES 모듈 지원 — MANAGER 3D 뷰어가 `esm.sh`에서 three.js/urdf-loader를 ESM으로 가져옴
- Canvas 2D 지원 — USER 탭 캔버스 렌더링
- 권장 뷰포트: USER 탭 캔버스가 800×680 고정 크기라 그보다 넉넉한 해상도 권장

포트 사용 현황

| 포트 | 프로토콜 | 용도 | 기본값 변경 방법 |
|---|---|---|---|
| 5000 | HTTP | `gui_web`(Flask) | `GUI_WEB_PORT` 환경변수 |
| 8765 | WebSocket | `server_connector_node` ↔ 브라우저 | `ws_port` 파라미터(`ws_host`와 함께) |
| 27017 | MongoDB | `mongod` | `mongo_uri` 파라미터 / `MONGO_URI` 환경변수(두 값을 같이 바꿔야 함) |

알려진 제약 (환경 준비 시 유의)

- `setup.py`/`package.xml`이 이 스냅샷에 없어 정확한 colcon 패키지명·entry_point 실행 명령은 이 스냅샷만으로 확정할 수 없음 — 실제 워크스페이스 병합 시 확인 필요(server_README.md/client_README.md에도 동일하게 표기됨)
- `/manager` 페이지 인증 로직 미구현 — 네트워크로 접근 가능한 누구나 로봇 정지/재개/비상정지/조인트 이동을 실행할 수 있는 상태. 사내망 밖으로 노출하지 말 것, 필요하면 방화벽/리버스 프록시로 접근을 제한할 것
- 로봇/그리퍼/워크피스 사전 설정(사용자 좌표계 101, Tool/TCP 등록, 9점 티칭)은 이 코드 저장소 안에 없고 티치펜던트에서 별도로 준비되어 있어야 동작함 — 새 로봇/새 환경에 배포할 때 이 문서의 "로봇/그리퍼 사전 설정" 절부터 먼저 확인
- `config.json`의 `path.watch_dir`이 특정 계정(`woods`) 경로로 하드코딩되어 있음 — 배포 환경의 실제 계정/경로로 교체 필요(client_README.md 참고)
