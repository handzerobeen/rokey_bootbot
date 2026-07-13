import asyncio
import asyncio.mixins
import json
import threading
import time
from datetime import datetime

# apt로 설치되는 python3-websockets(9.1)는 Python 3.10에서 asyncio.Lock/sleep/wait/wait_for의
# loop= 인자가 완전히 제거된 것과 호환되지 않는 알려진 버그가 있다 (내부적으로 loop=None을
# 넘기는 코드가 여러 곳에 있는데, 3.10부터는 loop 키워드 자체를 넘기는 것만으로 TypeError 발생).
# pip이 없어 최신 websockets로 올릴 수 없는 환경이라, 이 프로세스 안에서만 적용되는
# 최소 호환성 패치로 우회한다.
asyncio.mixins._LoopBoundMixin.__init__ = lambda self, *, loop=None: None

_orig_sleep = asyncio.sleep
_orig_wait = asyncio.wait
_orig_wait_for = asyncio.wait_for


def _compat_sleep(delay, result=None, *, loop=None):
    return _orig_sleep(delay, result)


async def _compat_wait(fs, *, loop=None, timeout=None, return_when=asyncio.ALL_COMPLETED):
    return await _orig_wait(fs, timeout=timeout, return_when=return_when)


async def _compat_wait_for(fut, timeout, *, loop=None):
    return await _orig_wait_for(fut, timeout)


asyncio.sleep = _compat_sleep
asyncio.wait = _compat_wait
asyncio.wait_for = _compat_wait_for

import math

import rclpy
import websockets
from calligraphy_interfaces.msg import StrokeCmd
from dsr_msgs2.msg import RobotDisconnection, RobotError
from dsr_msgs2.srv import (
    GetCurrentPosx,
    GetRobotMode,
    GetRobotSpeedMode,
    GetRobotState,
    GetToolForce,
    MoveJoint,
    MovePause,
    MoveResume,
    ServoOff,
    SetRobotControl,
)
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import JointState

from rokey_bootbot.mongo_repository import MongoStrokeRepository

# GetRobotState.srv 응답의 robot_state 코드 -> 사람이 읽을 문자열.
# dsr_msgs2 GetRobotState.srv 주석 그대로 옮김 (실기 dsr_controller2.cpp에
# "system/get_robot_state" 서비스로 실제 등록되어 있는 것까지 소스에서 확인함).
ROBOT_STATE_LABELS = {
    0: 'INITIALIZING',
    1: 'STANDBY',
    2: 'MOVING',
    3: 'SAFE_OFF',
    4: 'TEACHING',
    5: 'SAFE_STOP',
    6: 'EMERGENCY_STOP',
    7: 'HOMMING',
    8: 'RECOVERY',
    9: 'SAFE_STOP2',
    10: 'SAFE_OFF2',
    11: 'RESERVED1',
    12: 'RESERVED2',
    13: 'RESERVED3',
    14: 'RESERVED4',
    15: 'NOT_READY',
}

# SetRobotMode/GetRobotMode.srv 주석 그대로.
ROBOT_MODE_LABELS = {
    0: 'MANUAL',
    1: 'AUTONOMOUS',
    2: 'MEASURE',
}

# GetRobotSpeedMode.srv 주석 그대로 (system/get_robot_speed_mode, 실기 등록 확인됨).
SPEED_MODE_LABELS = {
    0: 'NORMAL',
    1: 'REDUCED',
}

# RobotError.msg 주석 그대로 ("/{robot_ns}/error" 토픽, 실기 publish 확인됨).
ERROR_LEVEL_LABELS = {1: 'INFO', 2: 'WARN', 3: 'ERROR'}
ERROR_GROUP_LABELS = {
    1: 'SYSTEM', 2: 'MOTION', 3: 'TP', 4: 'INVERTER', 5: 'SAFETY_CONTROLLER',
}

# Collision 전용 getter/토픽은 없다(ChangeCollisionSensitivity는 setter뿐) -- 대신
# doosan-robot2/dsr_common2/include/DRFC.h에서 이름 자체가 충돌을 가리키는 에러 코드를
# 확인해서, 이미 구독 중인 /error 토픽(RobotError.code)에 이 코드가 뜨면 충돌 이벤트로
# 간주한다. 확정된 매핑이 아니라 소스에 근거한 최선의 추정치임을 참고할 것:
#   9011 RC_ERROR_SAFETY_COLLISION
#   7060 OPERATION_SAFETY_FUNCTION_COLLISION_VIOLATION
#   7067 OPERATION_SAFETY_FUNCTION_SELF_BODY_COLLISION
COLLISION_ERROR_CODES = {9011, 7060, 7067}

# SAFE_STOP/SAFE_OFF류 안전 상태에서 빠져나오려면 robot_state별로 다른
# system/set_robot_control 코드를 쏴야 한다 (dsr_controller2.cpp의
# OnMonitoringStateCB가 내부적으로 쓰는 것과 동일한 매핑, DRFC.h의 ROBOT_CONTROL enum
# 값 기준: CONTROL_RESET_SAFET_STOP=2, CONTROL_SERVO_ON(=CONTROL_RESET_SAFET_OFF)=3,
# CONTROL_RECOVERY_SAFE_STOP=4, CONTROL_RECOVERY_SAFE_OFF=5). dsr_controller2 자체의
# 자동 복구는 SAFE_OFF에 한해 딱 한 번만(init_state 플래그) 동작하고 그 뒤로는 다시
# 빠지면 복구를 안 해주므로, 이 노드가 수동으로 호출할 수 있는 경로가 필요하다.
SAFETY_RECOVERY_CONTROL_CODE = {
    5: 2,   # STATE_SAFE_STOP -> CONTROL_RESET_SAFET_STOP
    3: 3,   # STATE_SAFE_OFF -> CONTROL_SERVO_ON
    9: 4,   # STATE_SAFE_STOP2 -> CONTROL_RECOVERY_SAFE_STOP
    10: 5,  # STATE_SAFE_OFF2 -> CONTROL_RECOVERY_SAFE_OFF
}

# 로봇 담당자 쪽 인터페이스(calligraphy_robot 패키지의 connector_node/robot_write_drl_node,
# calligraphy_interfaces) 그대로 사용. 이쪽은 확정된 값이라 바뀌지 않는다고 확인받음.
# SVG -> InputJson 제출은 이제 calligraphy_robot 쪽(svg_parser)이 직접 하므로,
# 이 노드는 /calligraphy/input_json을 더 이상 publish하지 않고 결과인 stroke_cmd만 구독한다.
STROKE_CMD_TOPIC = '/calligraphy/stroke_cmd'   # connector_node -> robot_write_drl_node (동일 내용을 우리도 구독)


class ServerConnectorNode(Node):
    """MongoDB 저장 + GUI 웹과의 WebSocket 브리지를 담당하는 실제(production) 노드.

    MANAGER 탭 대시보드(조인트 각도, TCP 전체 pose, robot_state/robot_mode/speed_mode
    모니터링 + 정지/비상정지/재개/안전상태 해제/조인트 이동 제어, /error·
    /robot_disconnection 기반 fault/collision/통신상태 표시)는 원래
    server_connector_node_designing.py에서 먼저 검증한 뒤 이 클래스로 옮겨온 것이다
    (MANAGER 대시보드 1차 실기 검증 2026-07-08, speed_mode/fault/collision/
    reset_safety_state 및 재실행(replay)이 client_connector_node를 거치지 않고
    STROKE_CMD_TOPIC에 직접 publish하도록 바뀐 버전까지 실기/에뮬레이터 검증 완료 후
    2026-07-09 반영). 검증에 쓰인 designing 파일은 참고용으로 당분간 남겨둔다.

    SVG 업로드는 더 이상 브라우저/GUI 웹에서 받지 않는다 -- calligraphy_robot 쪽의
    svg_parser가 SVG를 직접 파싱해서 /calligraphy/input_json으로 connector_node에
    제출하는 구조로 바뀌었기 때문이다. 이 노드는 그 결과인 StrokeCmd(STROKE_CMD_TOPIC)를
    구독해서 DB에 저장하는 역할만 한다.

    GUI 웹 WebSocket으로는 {"replay": "<part_name>"} 요청만 받는다. 이때는 MongoDB에
    저장된 그 part_name의 마지막 세션 stroke들을, client_connector_node를 다시 거치지
    않고 robot_write_drl_node가 직접 구독하는 STROKE_CMD_TOPIC(/calligraphy/stroke_cmd)에
    바로 재발행한다 -- 저장된 좌표는 이미 connector_node의 좌우반전이 반영된 이후 값이라
    그대로 다시 쏘면 된다. 이 재발행은 _on_stroke_cmd에도 그대로 잡혀서 재실행 자체가
    새 세션으로 이력에 남는다.

    GUI에 실시간으로 그려줄 붓끝 좌표는 로봇 사용자 코드(robot_write_drl_node)가
    아니라 로봇 컨트롤러 자체에서 직접 받는다: `/{robot_ns}/aux_control/get_current_posx`
    서비스(dsr_msgs2/srv/GetCurrentPosx)를 주기적으로 폴링한다. 이 서비스는 티치펜던트에
    등록된 TCP(도구 오프셋)가 이미 반영된 좌표를 돌려주므로, URDF/TF만으로는 알 수 없는
    실제 펜촉 위치를 정확하게 얻을 수 있다 (자세한 근거는 README 참고).

    로컬 전용 테스트 노드(server_connector_node_test.py)는 실기/로봇 파이프라인
    의존성 없이 이 클래스와 완전히 독립된 별도 구현으로 운영되다가, 2026-07-09
    실기 검증이 끝나 더 이상 필요 없어져 삭제됐다."""

    def __init__(self, node_name='server_connector_node'):
        super().__init__(node_name)

        self.declare_parameter('mongo_uri', 'mongodb://localhost:27017')
        self.declare_parameter('ws_host', '0.0.0.0')
        self.declare_parameter('ws_port', 8765)

        mongo_uri = self.get_parameter('mongo_uri').get_parameter_value().string_value
        ws_host = self.get_parameter('ws_host').get_parameter_value().string_value
        ws_port = self.get_parameter('ws_port').get_parameter_value().integer_value

        self._repository = MongoStrokeRepository(mongo_uri)
        self._session_ids = {}
        self._active_part_name = None
        # get_current_posx 폴링은 실제 stroke_id를 모르기 때문에(항상 0으로 고정,
        # 아래 _on_current_posx_response 참고) 프론트엔드가 이 값만으로는 "같은
        # part_name을 다시 쓰기 시작했다"는 걸 구분할 수 없다. 그래서 진짜 stroke_id를
        # 아는 _on_stroke_cmd 쪽에서 새 세션(stroke_id==1)마다 이 카운터를 올려서
        # pen_point 브로드캐스트 header에 함께 실어 보낸다 (좌표값 자체는 안 건드림 --
        # 좌표 변환은 여전히 GUI 프론트엔드 책임이라는 원칙과 무관한, 세션 식별용 값).
        self._active_session_seq = 0

        # 로봇에게 실제로 전달되는 것과 동일한 StrokeCmd를 그대로 구독해 DB 저장
        # 기준으로 삼는다 (connector_node의 좌우반전 등 변환이 반영된 이후 값).
        self._stroke_cmd_sub = self.create_subscription(
            StrokeCmd, STROKE_CMD_TOPIC, self._on_stroke_cmd, 10)
        # 재실행(replay)은 DB에 저장된(=이미 좌우반전이 반영된) stroke를 connector_node를
        # 다시 거치지 않고 robot_write_drl_node가 직접 구독하는 이 토픽으로 바로 쏜다
        # (사용자 요청: "client_connector_node를 거치지 않아"). 아래에서 이 노드가
        # 구독도 하고 있는 같은 토픽에 publish하므로, 재실행도 _on_stroke_cmd를 통해
        # 자동으로 새 세션 이력에 남는다 (README.md에도 설명된 ROS2에서 정상적인
        # 자기-재구독 패턴).
        self._stroke_cmd_pub = self.create_publisher(StrokeCmd, STROKE_CMD_TOPIC, 10)

        self._setup_realtime_feed()
        self._setup_manager_feed()

        self._ws_clients = set()
        self._loop = asyncio.new_event_loop()
        self._ws_thread = threading.Thread(
            target=self._run_ws_server, args=(ws_host, ws_port), daemon=True)
        self._ws_thread.start()

        self.get_logger().info(f'{node_name} ready. (ws://{ws_host}:{ws_port})')

    # --- 실시간 GUI 좌표 소스 (오버라이드 지점) ---

    def _setup_realtime_feed(self):
        """실제 로봇 컨트롤러로부터 TCP(붓끝) 좌표를 얻기 위한 서비스 클라이언트 +
        폴링 타이머를 만든다."""
        self.declare_parameter('robot_ns', 'dsr01')
        self.declare_parameter('posx_poll_period_sec', 0.1)
        # 로봇이 실제로 필기하는 좌표계는 티치펜던트에 등록된 사용자 좌표계(ref=101,
        # "bootbot")다 -- robot_write_drl_node의 movel(ref=101)과 calligraphy_robot/
        # config.json의 center_x/center_y도 전부 이 좌표계 기준이다. get_current_posx를
        # 기본값(DR_BASE, ref=0)으로 폴링하면 원점/축 방향이 달라서, 에러 없이 조용히
        # 다른 좌표계 값을 돌려받게 되고, 곡면 펼치기 계산(main_designing.js)이 조용히
        # 틀린 화면 위치를 만들어낸다.
        self.declare_parameter('tcp_ref_coord', 101)
        # draw/move 판정을 z 높이 임계값이 아니라 실제 접촉력(get_tool_force)으로 한다.
        # 곡면(원뿔대) 작업물에서는 "쓰는 중" z 자체가 위치마다 달라져 고정 z 임계값이
        # 성립하지 않는다(로봇측 calligraphy_robot의 프러스텀 설계 확인됨) -- 접촉력은
        # 표면 형상과 무관하게 "펜이 실제로 눌리고 있는가"를 직접 알려준다. 목표
        # 필압(로봇측 확인: 5N)과 과압 상한(12N) 사이, 노이즈 바닥보다는 확실히 위인
        # 값으로 잡되 정확한 임계값은 실기에서 튜닝 필요.
        self.declare_parameter('pen_contact_force_n', 0.7)

        # 접촉력 센서(get_tool_force) 자체가 하드웨어 특성상 그때그때 랜덤하게 튀거나
        # 못 잡는 경우가 있어(실기에서 확인됨), 힘 조건 하나만으로는 실제로 필기 중인데도
        # move로 잘못 끊기는 경우가 생긴다. 속도를 보조 신호로 같이 쓴다: 자모 하나가
        # 약 15개 점으로 촘촘히 쪼개져 있어 실제 필기 중에는 각 구간 이동거리가 짧아
        # 목표 속도(velx)까지 못 올라가는 반면, 획과 획 사이 이동은 더 먼 거리를 움직여
        # 속도가 더 높게 유지된다(로봇측 확인) -- "필기 속도로 움직이는 중"이면 힘 센서
        # 값이 순간적으로 안 잡히더라도 draw로 인정한다(OR 결합, AND 아님 -- 힘 센서를
        # 못 믿는 게 이유이므로 힘 조건이 실패해도 속도만으로 draw를 살릴 수 있어야 함).
        # calligraphy_robot의 velx 기본값(30mm/s)의 절반 정도를 첫 추정값으로 잡되,
        # 정확한 값은 실기 로그(아래 기동 로그 참고)로 실측 후 튜닝 필요.
        self.declare_parameter('pen_move_speed_max_mm_s', 3.5)

        robot_ns = self.get_parameter('robot_ns').get_parameter_value().string_value
        # _setup_manager_feed()가 같은 robot_ns로 dsr_msgs2 서비스 클라이언트를 더
        # 만들어야 해서 인스턴스 속성으로 남겨둔다 (declare_parameter는 이름당 한 번만
        # 가능해서 거기서 'robot_ns'를 다시 선언할 수 없음).
        self._robot_ns = robot_ns
        posx_poll_period_sec = self.get_parameter(
            'posx_poll_period_sec').get_parameter_value().double_value
        self._tcp_ref_coord = self.get_parameter(
            'tcp_ref_coord').get_parameter_value().integer_value
        self._pen_contact_force_n = self.get_parameter(
            'pen_contact_force_n').get_parameter_value().double_value
        self._pen_move_speed_max_mm_s = self.get_parameter(
            'pen_move_speed_max_mm_s').get_parameter_value().double_value
        # 실제로 적용된 값들을 기동 시 한 번 찍어서, 파라미터 오버라이드가 제대로
        # 먹었는지(혹은 타입 불일치로 조용히 0.0이 되는 옛 버그가 재발했는지)를 코드를
        # 다시 안 봐도 로그만으로 바로 확인할 수 있게 한다.
        self.get_logger().info(
            f'pen_contact_force_n = {self._pen_contact_force_n} N, '
            f'pen_move_speed_max_mm_s = {self._pen_move_speed_max_mm_s} mm/s')

        # 속도 게이팅용: 직전 get_current_posx 응답의 위치/시각을 들고 있다가, 다음
        # 응답이 오면 그 사이 이동거리/경과시간으로 순간속도를 추정한다. 첫 응답은
        # 비교 대상이 없으므로 속도 0(=필기 속도로 간주)으로 시작한다.
        self._prev_tcp_pos = None
        self._prev_tcp_time = None
        self._latest_tcp_speed_mm_s = 0.0

        # 작업 유무와 무관하게 노드 기동 시점부터 항상 폴링한다. _active_part_name으로
        # 게이팅하면 서비스 클라이언트가 실제 작업이 들어오는 순간에야 처음 discovery를
        # 시작하게 되어, 정작 응답이 빨리 필요한 첫 작업에서 discovery 지연(수 초~십수 초)을
        # 그대로 뒤집어쓴다. 항상 폴링해두면 그 워밍업 비용은 아무도 안 볼 때 미리 끝난다.
        self._posx_request_in_flight = False
        self._get_current_posx_client = self.create_client(
            GetCurrentPosx, f'/{robot_ns}/aux_control/get_current_posx')
        self._posx_poll_timer = self.create_timer(
            posx_poll_period_sec, self._poll_current_posx)

        # get_current_posx와 동일한 패턴/주기로 get_tool_force도 독립 폴링한다.
        # Z축 힘만 본다 -- calligraphy_robot 쪽 robot_write_drl_node.py의 과압 감시도
        # PEN_FORCE_AXIS_INDEX=2(Z축)만 보고 있어 같은 기준으로 맞췄다(3축 magnitude는
        # 이동 중 x/y축 잔차 노이즈까지 합산돼 실기에서 오탐이 잦았음).
        self._force_request_in_flight = False
        self._latest_contact_force = 0.0
        self._get_tool_force_client = self.create_client(
            GetToolForce, f'/{robot_ns}/aux_control/get_tool_force')
        self._force_poll_timer = self.create_timer(
            posx_poll_period_sec, self._poll_tool_force)

    def _poll_current_posx(self):
        # 서비스 응답이 폴링 주기(posx_poll_period_sec)보다 느릴 수 있으므로, 이전 요청이
        # 아직 안 끝났으면 새 요청을 쌓지 않고 건너뛴다 (안 그러면 응답이 느려질수록
        # 처리 안 된 요청이 무한히 쌓인다).
        if self._posx_request_in_flight:
            return
        if not self._get_current_posx_client.service_is_ready():
            self.get_logger().warn(
                'get_current_posx 서비스가 아직 준비되지 않음 (로봇 컨트롤러 연결 확인 필요)')
            return

        self._posx_request_in_flight = True
        req = GetCurrentPosx.Request()
        req.ref = self._tcp_ref_coord  # 로봇이 실제로 필기하는 사용자 좌표계(기본 101)
        future = self._get_current_posx_client.call_async(req)
        future.add_done_callback(self._on_current_posx_response)

    def _poll_tool_force(self):
        # get_current_posx와 동일한 in-flight 가드 패턴. 두 서비스는 독립적으로 폴링되므로
        # _on_current_posx_response가 참조하는 _latest_contact_force는 항상 "가장 최근에
        # 도착한" 힘 값이고, 위치 응답과 완벽히 동시 시점은 아니다 -- 두 폴링 주기가 짧아
        # (기본 0.1s) 실사용에는 문제없는 수준의 지연으로 판단.
        if self._force_request_in_flight:
            return
        if not self._get_tool_force_client.service_is_ready():
            return

        self._force_request_in_flight = True
        req = GetToolForce.Request()
        req.ref = 0  # DR_BASE -- calligraphy_robot의 robot_write_drl_node도 별도 ref 지정 없이 호출
        future = self._get_tool_force_client.call_async(req)
        future.add_done_callback(self._on_tool_force_response)

    def _on_tool_force_response(self, future):
        self._force_request_in_flight = False
        try:
            response = future.result()
        except Exception as e:
            self.get_logger().error(f'get_tool_force 호출 실패: {e}')
            return

        if not response.success:
            return

        # 3축 magnitude 대신 Z축 힘만 본다 -- calligraphy_robot 쪽
        # robot_write_drl_node.py의 과압 감시(check_pen_pressure_or_raise)도
        # PEN_FORCE_AXIS_INDEX=2(Z축)만 보고 있어, 같은 기준으로 맞춘다. 3축
        # magnitude는 이동 중 발생하는 x/y축 잔차 노이즈까지 그대로 합산해버려
        # 실기에서 오히려 오탐이 잦았다(이동 중 살짝 스치는 노이즈가 자꾸 잡힘).
        fz = response.tool_force[2]
        self._latest_contact_force = float(fz)

        # 수신(폴링) 자체는 posx_poll_period_sec 주기(기본 0.1s)로 계속하되, 화면 로그
        # 출력만 1초에 한 번으로 제한한다. throttle_duration_sec은 이 info() 호출 자체가
        # 몇 번 불리든(0.1s마다) rclpy가 노드 클럭 기준으로 실제 출력만 걸러준다.
        self.get_logger().info(
            f'[접촉력] {self._latest_contact_force:.2f}N', throttle_duration_sec=1.0)

        # USER 탭 캔버스 아래 실시간 표시용. 로그와 달리 쌓이는 게 아니라 프론트가
        # 매번 같은 자리의 값만 덮어써서 보여주므로(MANAGER 탭 TCP 표시와 동일한 방식),
        # 여기는 스로틀 없이 수신할 때마다(0.1s) 그대로 보낸다.
        self._broadcast_threadsafe(
            json.dumps({'type': 'contact_force', 'force_n': self._latest_contact_force},
                       ensure_ascii=False))

    def _on_current_posx_response(self, future):
        self._posx_request_in_flight = False
        try:
            response = future.result()
        except Exception as e:
            self.get_logger().error(f'get_current_posx 호출 실패: {e}')
            return

        if not response.success or not response.task_pos_info:
            return

        # task_pos_info[0].data = [x, y, z, w, p, r, solution_space] (mm/deg, TCP 기준)
        x, y, z, w, p, r = response.task_pos_info[0].data[0:6]

        # 속도 게이팅: 직전 응답과의 위치차/시간차로 순간속도(mm/s)를 추정한다. 첫
        # 응답(비교 대상 없음)은 속도 0으로 둬 필기 속도로 간주한다.
        now = self.get_clock().now()
        if self._prev_tcp_pos is not None:
            dt = (now - self._prev_tcp_time).nanoseconds / 1e9
            if dt > 0:
                px, py, pz = self._prev_tcp_pos
                dist = math.sqrt((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2)
                self._latest_tcp_speed_mm_s = dist / dt
        self._prev_tcp_pos = (x, y, z)
        self._prev_tcp_time = now

        # 로봇 쪽에서 stroke_id/action을 따로 알려주지 않으므로, 접촉력(get_tool_force)이
        # 임계값 이상이거나(붓이 실제로 눌린 상태) 이동속도가 필기 속도 이하이면(자모 하나가
        # 15개 점으로 촘촘히 쪼개져 있어 실제 필기 중 구간 이동은 느림, 획 사이 이동은
        # 빠름) draw로 판정한다. OR로 묶는 이유: 접촉력 센서가 하드웨어 특성상 그때그때
        # 랜덤하게 못 잡는 경우가 있어(실기 확인됨), 힘 조건 하나만으로는 실제 필기 중에도
        # move로 잘못 끊긴다 -- 속도가 필기 속도라면 힘 센서가 순간 놓쳐도 draw를 살린다.
        # z 높이가 아니라 힘/속도로 판단하는 이유: 곡면(원뿔대) 작업물에서는 "쓰는 중" z
        # 자체가 위치마다 달라져 고정 z 임계값이 성립하지 않기 때문.
        action = (
            'draw'
            if (self._latest_contact_force >= self._pen_contact_force_n
                or self._latest_tcp_speed_mm_s <= self._pen_move_speed_max_mm_s)
            else 'move'
        )

        # MANAGER 대시보드용으로 전체 6축 pose를 캐시해둔다 (작업 중이 아니어도 항상
        # 갱신 -- 대시보드는 붓글씨 작업 여부와 무관하게 로봇 상태를 보여주는 용도라
        # USER 탭의 pen_point 게이팅과는 다르다). 실제 broadcast는 _poll_manager_status의
        # 타이머에서 주기적으로 한다.
        self._latest_tcp_pose = {
            'x': x, 'y': y, 'z': z, 'w': w, 'p': p, 'r': r, 'action': action,
        }

        # 아래 pen_point는 USER 탭(캔버스 시각화) 전용이라, 활성 작업이 없으면
        # (_active_part_name이 None) 그릴 대상이 없으니 브로드캐스트하지 않는다 --
        # get_current_posx 폴링 자체는 항상 하되(위 _setup_realtime_feed 설명),
        # 캔버스에 뿌리는 건 작업 중일 때만 한다.
        if self._active_part_name is None:
            return

        # coord_space='robot_mm'로 표시해서, GUI(main.js)가 이 점은 로봇 base 좌표계
        # mm 값이라는 걸 알고 캔버스 픽셀 좌표로 변환하게 한다 (여기서는 값 자체를
        # 건드리지 않는다 -- 화면 표시용 변환은 뷰 계층인 GUI 쪽 책임이고, 이 노드가
        # 브로드캐스트/저장하는 좌표는 항상 로봇의 실제 물리 좌표를 그대로 유지해야
        # DB에 남는 기록이나 다른 소비자에게도 왜곡 없이 전달된다).
        # session_seq도 함께 실어 보낸다 -- stroke_id가 항상 0이라 프론트엔드가 "같은
        # part_name을 다시 쓰기 시작했다"를 구분 못 하는 문제를 이걸로 해결한다.
        self._broadcast_pen_point(
            {
                'part_name': self._active_part_name,
                'stroke_id': 0,
                'coord_space': 'robot_mm',
                'session_seq': self._active_session_seq,
            },
            action, {'x': x, 'y': y})

    def _broadcast_pen_point(self, header, action, point):
        payload = json.dumps(
            {'header': header, 'action': action, 'point': point}, ensure_ascii=False)
        self._broadcast_threadsafe(payload)

    # --- MANAGER 탭 대시보드 (모니터링 + 제어) ---
    #
    # dsr_controller2.cpp(실제 드라이버) 소스를 직접 읽어서 실제로 등록되는 서비스만
    # 사용한다 (예전에 참고했던 예제의 /robot/start 등 커스텀 토픽은 이 드라이버에
    # 없다 -- 그 예제는 별도의 자체 래퍼 노드를 가정한 것으로 보임):
    #   - "motion/move_joint"        -> dsr_msgs2/srv/MoveJoint
    #   - "motion/move_pause"        -> dsr_msgs2/srv/MovePause
    #   - "motion/move_resume"       -> dsr_msgs2/srv/MoveResume
    #   - "system/servo_off"         -> dsr_msgs2/srv/ServoOff
    #   - "system/get_robot_state"   -> dsr_msgs2/srv/GetRobotState
    #   - "system/get_robot_mode"    -> dsr_msgs2/srv/GetRobotMode
    # 반대로 /dsr01/state(RobotState 토픽)는 dsr_controller2.cpp에 publisher 등록
    # 코드 자체가 없어 실기에도 안 뜬 것으로 확인되어 사용하지 않는다.
    #
    # move_stop(DR_QSTOP)+set_robot_control 조합은 안전 상태 머신(SAFE_STOP/SAFE_OFF)
    # 복구용이라 "정지/재개"가 원했던 "동작 일시정지 후 이어서 진행"과는 다른 기능이었다
    # (실기 테스트에서 셋 다 눌러도 robot_state가 계속 STANDBY로 안 바뀌는 것으로 확인됨).
    # 모션 일시정지/재개 전용 서비스인 move_pause/move_resume으로 교체한다.

    def _setup_manager_feed(self):
        """MANAGER 탭 대시보드용 서비스 클라이언트 + /joint_states 구독 + 주기적
        상태 브로드캐스트 타이머를 만든다."""
        self.declare_parameter('manager_poll_period_sec', 0.5)
        manager_poll_period_sec = self.get_parameter(
            'manager_poll_period_sec').get_parameter_value().double_value

        self._latest_tcp_pose = None
        self._latest_joint_deg = None
        self._latest_robot_state_code = None
        self._latest_robot_mode_code = None
        self._latest_speed_mode_code = None
        self._robot_state_request_in_flight = False
        self._robot_mode_request_in_flight = False
        self._speed_mode_request_in_flight = False
        # /error, /robot_disconnection은 폴링이 아니라 이벤트 토픽이라 응답을 기다릴
        # 필요 없이 콜백에서 바로 캐시한다 (아래 _on_robot_error/_on_robot_disconnection).
        self._latest_fault = None
        self._latest_collision = None
        self._last_disconnection_at = None

        # /joint_states는 dsr_controller2가 기동되면 표준적으로 발행하는 토픽
        # (이전 세션에 실기로 확인됨). 콜백마다 바로 WS로 쏘면 컨트롤러 발행 주기
        # (수십~100Hz)만큼 과도하게 나가므로, 최신 값만 캐시해두고 실제 전송은
        # 아래 manager_poll_period_sec 주기 타이머에서 한다.
        self._joint_states_sub = self.create_subscription(
            JointState, '/joint_states', self._on_joint_states, 10)

        ns = self._robot_ns
        self._move_joint_client = self.create_client(
            MoveJoint, f'/{ns}/motion/move_joint')
        self._move_pause_client = self.create_client(
            MovePause, f'/{ns}/motion/move_pause')
        self._move_resume_client = self.create_client(
            MoveResume, f'/{ns}/motion/move_resume')
        self._servo_off_client = self.create_client(
            ServoOff, f'/{ns}/system/servo_off')
        self._get_robot_state_client = self.create_client(
            GetRobotState, f'/{ns}/system/get_robot_state')
        self._get_robot_mode_client = self.create_client(
            GetRobotMode, f'/{ns}/system/get_robot_mode')
        # Speed limit/Fault/통신상태(연결 끊김) 칸을 "확인 필요" 정적 placeholder로
        # 두지 않고 실제 값으로 채우기 위해 추가 -- dsr_controller2.cpp 소스에서
        # 실제로 create_service/create_publisher 되는 것까지 확인했다. Collision은
        # 대응하는 전용 getter/토픽이 없어(ChangeCollisionSensitivity는 setter뿐)
        # 여전히 placeholder로 남겨둔다.
        self._get_robot_speed_mode_client = self.create_client(
            GetRobotSpeedMode, f'/{ns}/system/get_robot_speed_mode')
        # SAFE_STOP/SAFE_OFF류 안전 상태에서 수동으로 빠져나오기 위한 클라이언트
        # (SAFETY_RECOVERY_CONTROL_CODE 참고) -- move_pause/move_resume(정지/재개)과는
        # 완전히 다른 용도라 별도 client/버튼으로 분리한다.
        self._set_robot_control_client = self.create_client(
            SetRobotControl, f'/{ns}/system/set_robot_control')
        self._error_sub = self.create_subscription(
            RobotError, f'/{ns}/error', self._on_robot_error, 10)
        self._disconnection_sub = self.create_subscription(
            RobotDisconnection, f'/{ns}/robot_disconnection', self._on_robot_disconnection, 10)

        self._manager_poll_timer = self.create_timer(
            manager_poll_period_sec, self._poll_manager_status)

    def _on_joint_states(self, msg):
        if len(msg.position) < 6:
            return
        # rad -> deg 변환 (M0609은 6축). msg.position 순서가 곧 joint_names 순서라고
        # 가정한다 (dsr_controller2가 발행하는 표준 /joint_states 그대로).
        self._latest_joint_deg = [math.degrees(p) for p in msg.position[:6]]

    def _poll_manager_status(self):
        """robot_state/robot_mode를 폴링하고, 그 결과와 무관하게(응답이 아직 없어도
        캐시된 값으로) 매 tick마다 대시보드 스냅샷을 브로드캐스트한다. get_current_posx
        폴링(_poll_current_posx, 0.1s)과는 독립된 별도 타이머라 주기가 다르다."""
        connected = self._get_current_posx_client.service_is_ready()

        if not self._robot_state_request_in_flight and self._get_robot_state_client.service_is_ready():
            self._robot_state_request_in_flight = True
            future = self._get_robot_state_client.call_async(GetRobotState.Request())
            future.add_done_callback(self._on_robot_state_response)

        if not self._robot_mode_request_in_flight and self._get_robot_mode_client.service_is_ready():
            self._robot_mode_request_in_flight = True
            future = self._get_robot_mode_client.call_async(GetRobotMode.Request())
            future.add_done_callback(self._on_robot_mode_response)

        if not self._speed_mode_request_in_flight and self._get_robot_speed_mode_client.service_is_ready():
            self._speed_mode_request_in_flight = True
            future = self._get_robot_speed_mode_client.call_async(GetRobotSpeedMode.Request())
            future.add_done_callback(self._on_robot_speed_mode_response)

        self._broadcast_dashboard(connected)

    def _on_robot_state_response(self, future):
        self._robot_state_request_in_flight = False
        try:
            response = future.result()
        except Exception as e:
            self.get_logger().warn(f'get_robot_state 호출 실패: {e}')
            return
        if response.success:
            self._latest_robot_state_code = response.robot_state

    def _on_robot_mode_response(self, future):
        self._robot_mode_request_in_flight = False
        try:
            response = future.result()
        except Exception as e:
            self.get_logger().warn(f'get_robot_mode 호출 실패: {e}')
            return
        if response.success:
            self._latest_robot_mode_code = response.robot_mode

    def _on_robot_speed_mode_response(self, future):
        self._speed_mode_request_in_flight = False
        try:
            response = future.result()
        except Exception as e:
            self.get_logger().warn(f'get_robot_speed_mode 호출 실패: {e}')
            return
        if response.success:
            self._latest_speed_mode_code = response.speed_mode

    def _on_robot_error(self, msg):
        """"/{robot_ns}/error" (RobotError)는 폴링 대상이 아니라 실기가 직접 publish하는
        이벤트 로그다. "지금 fault가 있는지"를 보장하는 상태 토픽이 아니라 "이런 로그가
        찍혔다"는 스트림이라서, 최신 한 건만 캐시해 MANAGER 대시보드의 Fault 칸에
        "최근 로그가 언제/무엇이었는지"로 보여준다 (완전히 해소됐는지 여부까지는 이
        토픽만으로 알 수 없음 -- 그래서 프론트에서도 "현재 fault 상태"가 아니라
        "최근 에러 로그"로 표현한다).

        레벨별로 보여주는 곳을 분리한다(사용자 요청, Fault 칸과 로그창에 같은 내용이
        중복으로 쏟아지는 게 과하다는 피드백): Fault 대시보드 칸은 WARN만 반영하고,
        MANAGER 로그창은 ERROR만 흘려보낸다. INFO는 어느 쪽에도 안 보낸다 -- "문제"라고
        부를 레벨이 아니라서. 실기 재생 테스트로 확인된 사실: MOTION #3509
        (RC_ERROR_MTN_SINGULARITY, 특이점 경고, WARN)가 붓글씨 연속 동작 중 초당 수십
        건씩 찍혀서, 이걸 전부 로그창에도 내보내면 7줄 로그창이 스팸으로만 가득 찼다."""
        fault = {
            'level': msg.level,
            'level_label': ERROR_LEVEL_LABELS.get(msg.level),
            'group': msg.group,
            'group_label': ERROR_GROUP_LABELS.get(msg.group),
            'code': msg.code,
            'msg1': msg.msg1,
            'msg2': msg.msg2,
            'msg3': msg.msg3,
            'at': datetime.now().isoformat(),
        }

        if msg.level == 2:  # WARN -> Fault 대시보드 칸
            self._latest_fault = fault

        if msg.level == 3:  # ERROR -> MANAGER 로그창
            self._manager_log(
                f'[에러 로그] {ERROR_LEVEL_LABELS.get(msg.level, msg.level)} '
                f'{ERROR_GROUP_LABELS.get(msg.group, msg.group)} #{msg.code}: {msg.msg1}')

        # Collision 칸은 위 레벨 분기와 무관하게 항상 확인한다 -- 실제 충돌 에러가
        # WARN이 아니라 ERROR로 찍힐 수도 있으므로, self._latest_fault(WARN 전용)가
        # 아니라 이 콜백에서 방금 만든 fault를 그대로 쓴다. "지금 충돌 중"이 아니라
        # "가장 최근 충돌성 에러 로그"를 보여준다 (뒤에 무관한 에러가 또 찍혀도 이
        # 값은 덮어쓰지 않는다).
        if msg.code in COLLISION_ERROR_CODES:
            self._latest_collision = fault
            self._manager_log(f'[충돌 감지] #{msg.code}: {msg.msg1}')

    def _on_robot_disconnection(self, msg):
        """"/{robot_ns}/robot_disconnection" (RobotDisconnection)은 필드가 없는 순수
        이벤트 메시지 -- 발행됐다는 사실 자체가 신호라 수신 시각만 기록한다. 통신
        상태 카드의 "Poll rate"는 고정 설정값이라 실제 통신 품질을 보여주지 못했는데,
        이 값은 실기가 실제로 끊겼다고 알려준 마지막 시점이라 훨씬 신뢰할 수 있는
        신호다."""
        self._last_disconnection_at = datetime.now().isoformat()
        self._manager_log('로봇 연결 끊김 이벤트 수신 (robot_disconnection)')

    def _broadcast_dashboard(self, connected):
        payload = {
            'type': 'dashboard',
            'connected': connected,
            'tcp': self._latest_tcp_pose,
            'joints_deg': self._latest_joint_deg,
            'robot_state': {
                'code': self._latest_robot_state_code,
                'label': ROBOT_STATE_LABELS.get(self._latest_robot_state_code),
            },
            'robot_mode': {
                'code': self._latest_robot_mode_code,
                'label': ROBOT_MODE_LABELS.get(self._latest_robot_mode_code),
            },
            'speed_mode': {
                'code': self._latest_speed_mode_code,
                'label': SPEED_MODE_LABELS.get(self._latest_speed_mode_code),
            },
            'fault': self._latest_fault,
            'collision': self._latest_collision,
            'comm': {
                'ws_clients': len(self._ws_clients),
                'poll_period_sec': self.get_parameter(
                    'posx_poll_period_sec').get_parameter_value().double_value,
                'last_disconnection_at': self._last_disconnection_at,
            },
        }
        self._broadcast_threadsafe(json.dumps(payload, ensure_ascii=False))

    def _manager_log(self, text):
        """MANAGER 탭 로그 패널로 보낼 메시지. get_logger()에도 같이 남긴다."""
        self.get_logger().info(f'[MANAGER] {text}')
        self._broadcast_threadsafe(
            json.dumps({'type': 'manager_log', 'text': text}, ensure_ascii=False))

    def _on_manager_command(self, data):
        """MANAGER 탭에서 온 제어 명령 -- 실제 로봇을 멈추거나 움직일 수 있으므로
        각 명령은 실기에서 확인된 dsr_msgs2 서비스로만 연결한다 (사용자 확정 매핑:
        정지=move_pause, 비상정지=servo_off(EMERGENCY), 재개=move_resume).
        wire 프로토콜상의 manager_cmd 문자열(move_stop/reset_safe_stop)은 프론트와
        맞추기 위해 그대로 두고, 내부에서 호출하는 서비스만 바꿨다 -- move_stop+
        set_robot_control 조합은 안전 상태 머신(SAFE_STOP/SAFE_OFF) 복구용이라
        "정지 후 이어서 진행"이라는 실제 요구사항과는 다른 기능이었다(실기 테스트에서
        셋 다 눌러도 robot_state가 계속 STANDBY로 안 바뀌는 것으로 확인됨)."""
        cmd = data.get('manager_cmd')
        if cmd == 'move_stop':
            self._call_move_pause()
        elif cmd == 'servo_off_emergency':
            self._call_servo_off_emergency()
        elif cmd == 'reset_safe_stop':
            self._call_move_resume()
        elif cmd == 'reset_safety_state':
            self._call_reset_safety_state()
        elif cmd == 'move_joint':
            self._call_move_joint(data)
        else:
            self.get_logger().error(f'알 수 없는 MANAGER 명령: {cmd}')

    def _call_move_pause(self):
        if not self._move_pause_client.service_is_ready():
            self._manager_log('정지 실패: move_pause 서비스 준비 안 됨')
            return
        future = self._move_pause_client.call_async(MovePause.Request())
        future.add_done_callback(lambda f: self._on_manager_service_response('정지(move_pause)', f))

    def _call_move_resume(self):
        if not self._move_resume_client.service_is_ready():
            self._manager_log('재개 실패: move_resume 서비스 준비 안 됨')
            return
        future = self._move_resume_client.call_async(MoveResume.Request())
        future.add_done_callback(lambda f: self._on_manager_service_response('재개(move_resume)', f))

    def _call_reset_safety_state(self):
        """SAFE_STOP/SAFE_OFF류 안전 상태에서 수동으로 빠져나온다. dsr_controller2의
        자체 자동 복구(OnMonitoringStateCB)는 SAFE_OFF에 한해 프로세스당 딱 한 번만
        동작하고 그 뒤로 다시 SAFE_OFF에 빠지면 복구를 안 해주기 때문에, 이 버튼이
        없으면 CLI로 직접 set_robot_control을 호출하는 것 말고는 복구할 방법이 없다
        (실제로 겪은 문제). 현재 robot_state에 맞는 robot_control 코드를
        SAFETY_RECOVERY_CONTROL_CODE에서 찾아 보내고, 안전 상태가 아니면 아무것도
        하지 않는다 -- 정지/재개(move_pause/move_resume)와는 완전히 다른 용도라
        섞지 않는다."""
        state_code = self._latest_robot_state_code
        control_code = SAFETY_RECOVERY_CONTROL_CODE.get(state_code)
        if control_code is None:
            self._manager_log(
                f'안전상태 해제 무시: 현재 상태({ROBOT_STATE_LABELS.get(state_code, state_code)})는 '
                '복구 대상 안전 상태가 아님')
            return
        if not self._set_robot_control_client.service_is_ready():
            self._manager_log('안전상태 해제 실패: set_robot_control 서비스 준비 안 됨')
            return
        req = SetRobotControl.Request()
        req.robot_control = control_code
        future = self._set_robot_control_client.call_async(req)
        future.add_done_callback(
            lambda f: self._on_manager_service_response('안전상태 해제(set_robot_control)', f))

    def _call_servo_off_emergency(self):
        if not self._servo_off_client.service_is_ready():
            self._manager_log('비상정지 실패: servo_off 서비스 준비 안 됨')
            return
        req = ServoOff.Request()
        req.stop_type = 3  # STOP_TYPE_EMERGENCY(=STOP_TYPE_HOLD)
        future = self._servo_off_client.call_async(req)
        future.add_done_callback(lambda f: self._on_manager_service_response('비상정지(servo_off)', f))

    def _call_move_joint(self, data):
        """sync_type을 반드시 ASYNC(1)로 보내야 한다. SYNC(0, 기본값)로 두면
        dsr_controller2.cpp의 movej_cb가 Drfl->movej()(모션 완료까지 블로킹)를 호출하는데,
        move_joint/move_pause/move_resume이 전부 같은 기본 콜백 그룹을 공유하고 있어서
        (move_stop만 별도 cb_group_) 모션 도중엔 정지 후 재개/다음 조인트 이동 요청이
        그 블로킹이 풀릴 때까지 아예 처리되지 못하고 큐에 쌓인다."""
        pos = data.get('pos')
        if not isinstance(pos, list) or len(pos) != 6:
            self._manager_log('조인트 이동 요청 무시: pos는 6개 값이어야 함')
            return
        if not self._move_joint_client.service_is_ready():
            self._manager_log('조인트 이동 실패: move_joint 서비스 준비 안 됨')
            return

        req = MoveJoint.Request()
        req.pos = [float(v) for v in pos]
        req.vel = float(data.get('vel', 30.0))
        req.acc = float(data.get('acc', 30.0))
        req.mode = 0  # MOVE_MODE_ABSOLUTE
        req.sync_type = 1  # ASYNC: Drfl->amovej() 사용, 콜백이 즉시 반환됨
        future = self._move_joint_client.call_async(req)
        future.add_done_callback(
            lambda f: self._on_manager_service_response('조인트 이동 접수(move_joint, async)', f))

    def _on_manager_service_response(self, label, future):
        try:
            response = future.result()
        except Exception as e:
            self._manager_log(f'{label} 호출 실패: {e}')
            return
        self._manager_log(f'{label} {"성공" if response.success else "실패"}')

    # --- WebSocket 서버 (asyncio, 별도 스레드에서 이벤트 루프 실행) ---

    def _run_ws_server(self, host, port):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(websockets.serve(self._on_client_connected, host, port))
        self._loop.run_forever()

    async def _on_client_connected(self, websocket, path=None):
        self._ws_clients.add(websocket)
        self.get_logger().info(f'GUI 클라이언트 연결됨 (현재 {len(self._ws_clients)}개)')
        try:
            async for message in websocket:
                self._on_ws_message(message)
        finally:
            self._ws_clients.discard(websocket)
            self.get_logger().info(f'GUI 클라이언트 연결 종료 (현재 {len(self._ws_clients)}개)')

    async def _broadcast(self, data):
        if not self._ws_clients:
            return
        await asyncio.gather(
            *(client.send(data) for client in self._ws_clients),
            return_exceptions=True,
        )

    def _broadcast_threadsafe(self, data):
        asyncio.run_coroutine_threadsafe(self._broadcast(data), self._loop)

    def _on_ws_message(self, raw_message):
        """브라우저가 WebSocket으로 보낸 메시지를 종류에 따라 분기한다.
        websockets 콜백(ws 스레드)에서 직접 호출되지만, rclpy Publisher.publish()는
        스레드 세이프하므로 문제없다."""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError as e:
            self.get_logger().error(f'WebSocket 메시지 파싱 실패: {e}')
            return

        if 'replay' in data:
            self._on_replay_request(data['replay'])
        elif 'manager_cmd' in data:
            self._on_manager_command(data)
        else:
            self.get_logger().error(f'알 수 없는 WebSocket 메시지 형식: {raw_message[:200]}')

    # --- 데이터 수신 ---

    def _on_stroke_cmd(self, msg):
        """로봇에게 실제로 전달되는 것과 동일한 StrokeCmd를 그대로 구독해 DB에 저장한다.
        connector_node의 좌우반전 등 변환이 반영된 이후의 최종 값이라 이걸 저장
        기준으로 삼는다. SVG 파싱 자체는 calligraphy_robot 쪽(svg_parser)의 몫이라
        이 노드는 파싱 결과를 전혀 만들지 않는다.

        이 메시지가 곧 "로봇이 지금 이 part_name을 작업 중이다"라는 유일하게 신뢰할 수
        있는 신호이므로, 매번 _active_part_name을 갱신해서 get_current_posx 폴링(GUI
        실시간 시각화)이 항상 실제 로봇 작업과 함께 동작하도록 한다. 브라우저 업로드
        여부와는 무관하게, 로봇에게 실제로 stroke_cmd가 전달되는 모든 경로(svg_parser
        직접 제출, 재실행 등)에서 동일하게 작동한다.

        stroke_id==1은 새 작성 세션의 시작 신호(Mongo 세션 분리 기준과 동일)이므로,
        이때 _active_session_seq를 올려서 GUI가 "같은 part_name이라도 새 세션이
        시작됐다"를 구분할 수 있게 한다 (_on_current_posx_response 참고)."""
        if msg.stroke_id == 1:
            self._active_session_seq += 1
        self._active_part_name = msg.part_name

        # calligraphy_interfaces 0710 갱신판: StrokeCmd에 z/rx/ry/rz(float64[])가 추가됨
        # (곡면 필기라 점마다 자세가 다름). client_connector_node가 항상 이 네 필드를
        # x/y와 같은 길이로 채워 보내므로(9점 보간 fallback 포함) zip 그대로 써도 된다.
        points = [
            {'x': float(x), 'y': float(y), 'z': float(z), 'rx': float(rx), 'ry': float(ry), 'rz': float(rz)}
            for x, y, z, rx, ry, rz in zip(msg.x, msg.y, msg.z, msg.rx, msg.ry, msg.rz)
        ]
        self._store_stroke_data(msg.part_name, msg.stroke_id, msg.action, points)

    def _on_replay_request(self, part_name):
        """{"replay": "<part_name>"}: 저장된 마지막 세션의 stroke들을 다시 실행한다.
        실제로 무엇을 하는지는 _replay_strokes()에 위임한다."""
        strokes = self._repository.get_latest_session_strokes(part_name)
        if not strokes:
            self.get_logger().warn(f'재실행 요청 실패: "{part_name}"에 대한 저장된 기록이 없음')
            return

        threading.Thread(
            target=self._replay_strokes, args=(part_name, strokes), daemon=True).start()

    def _replay_strokes(self, part_name, strokes):
        """DB에 저장된 stroke를 connector_node를 거치지 않고 robot_write_drl_node가
        직접 구독하는 STROKE_CMD_TOPIC(/calligraphy/stroke_cmd)으로 바로 publish한다.
        저장된 좌표는 이미 connector_node의 좌우반전이 반영된 이후 값이라 이대로 바로
        쏘면 다시 반전될 걱정이 없다.

        robot_write_drl_node.stroke_cmd_callback()은 첫 StrokeCmd로 total_count를
        확정하고, queued_count가 그 값과 같아질 때까지 큐에 모았다가 한 번에 실행한다
        (총 개수가 달라지면 작업 자체를 차단/리셋함) -- 그래서 모든 stroke에
        len(strokes)를 total_count로 동일하게 실어 보내야 한다. 또한 로봇이 이미 다른
        작업을 실행 중이면(is_executing=True) 새 StrokeCmd를 조용히 무시하므로, 재실행은
        로봇이 유휴 상태일 때만 실제로 먹힌다(로그에는 남지만 이 노드가 그 실패를
        알 방법은 없음 -- "no send_done"과 같은 이유).

        publish 사이에 잠깐씩 텀을 두는 이유 -- QoS 큐(depth 10)를 한 번에 넘겨서 뒤쪽
        stroke가 유실되는 걸 막기 위함. 이 메서드는 별도 스레드에서 돌기 때문에 sleep으로
        멈춰도 WebSocket 이벤트 루프(다른 클라이언트 broadcast 등)는 막히지 않는다.

        _active_part_name을 여기서 세팅해서 _poll_current_posx가 폴링을 시작하게 한다.
        일부러 이 메서드가 끝나도 다시 None으로 되돌리지 않는다 -- publish 자체는 몇 초
        안에 끝나지만 로봇이 실제로 그 stroke들을 다 그리는 데는 훨씬 오래 걸리고,
        robot_write_drl_node가 "작업 완료" 신호를 따로 주지 않기 때문이다. 그래서 다음
        작업이 시작될 때 새 part_name으로 덮어쓰는 것 말고는, 로봇이 다 그린 뒤에도
        폴링이 계속되는 걸 감수하는 쪽을 택했다. _on_stroke_cmd도 매번 _active_part_name을
        갱신하므로, 여기서 세팅한 값은 곧 이 publish 자체가 실제로 stroke_cmd로
        수신되면서 동일한 값으로 다시 덮어써진다. 재실행도 "새 세션"으로 취급해 GUI
        캔버스를 새로 지워야 하므로(같은 part_name을 다시 재생하는 경우가 흔함)
        session_seq도 함께 올린다."""
        self._active_session_seq += 1
        self._active_part_name = part_name
        total_count = len(strokes)
        self.get_logger().info(f'"{part_name}" 재실행 시작 ({total_count}개 stroke)')
        for stroke in strokes:
            msg = StrokeCmd()
            msg.total_count = total_count
            msg.stroke_id = int(stroke['stroke_id'])
            msg.part_name = part_name
            msg.action = stroke['action']
            msg.x = [float(p['x']) for p in stroke['points']]
            msg.y = [float(p['y']) for p in stroke['points']]
            # 6DOF 확장 이전(z/rx/ry/rz 없이 저장된) 옛 이력을 재실행할 수도 있어 .get()
            # 기본값 0.0으로 채운다 -- robot_write_drl_node의 is_valid_stroke_msg가 z 길이를
            # point_count와 무조건 같아야 한다고 요구해서, 아예 안 채우면 메시지 자체가
            # 거부된다(옛 이력이라 원래 자세를 알 방법이 없는 건 감수).
            msg.z = [float(p.get('z', 0.0)) for p in stroke['points']]
            msg.rx = [float(p.get('rx', 0.0)) for p in stroke['points']]
            msg.ry = [float(p.get('ry', 0.0)) for p in stroke['points']]
            msg.rz = [float(p.get('rz', 0.0)) for p in stroke['points']]
            self._stroke_cmd_pub.publish(msg)
            time.sleep(0.2)
        self.get_logger().info(
            f'"{part_name}" stroke_cmd 재발행 완료 ({total_count}개, 로봇 실제 작성은 '
            f'계속 진행 중일 수 있음).')

    # --- Mongo 저장 (실제/테스트 공통 사용) ---

    def _store_stroke_data(self, part_name, stroke_id, action, points):
        # stroke_id==1은 새 작성 세션의 시작 신호. 새 session_id를 발급해서
        # part_name 문서의 sessions 배열에 새 세션을 추가하고, 이후 같은 세션의
        # stroke들과 묶는다 -- 다음에 같은 글자를 다시 써도 이전 세션을 덮어쓰지
        # 않고 이력으로 남는다.
        if stroke_id == 1:
            session_id = self._repository.new_session_id()
            self._session_ids[part_name] = session_id
            self._repository.start_session(part_name, session_id)
        else:
            session_id = self._session_ids.setdefault(
                part_name, self._repository.new_session_id())

        self._repository.upsert_stroke(
            part_name=part_name,
            session_id=session_id,
            stroke_id=stroke_id,
            action=action,
            points=points,
        )
        self.get_logger().info(
            f'저장 완료: {part_name} stroke_id={stroke_id} (session={session_id[:8]})')

    def destroy_node(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._ws_thread.join(timeout=2.0)
        self._repository.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ServerConnectorNode()

    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        node.get_logger().info('server_connector_node 종료')
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
