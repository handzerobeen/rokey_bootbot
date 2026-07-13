import json
import os
import time

import rclpy

import DR_init
from calligraphy_interfaces.msg import StrokeCmd


ROBOT_ID = 'dsr01'
ROBOT_MODEL = 'm0609'
CONFIG_FILE_NAME = 'calligraphy_config.json'

# ============================================================
# 붓펜 힘제어 파라미터
# JSON 정리 전까지는 여기 값만 바꿔서 테스트한다.
# ============================================================
USE_PEN_FORCE_CONTROL = True
TARGET_PEN_FORCE_N = 4.0        # 필기 중 유지하려는 목표 힘[N]
MAX_PEN_FORCE_N = 12.0           # 이 값 이상이면 과압으로 보고 즉시 상승/중단[N]
PEN_FORCE_AXIS_INDEX = 2         # get_tool_force()[2] = Z축 힘
PEN_FORCE_VECTOR = [0, 0, -TARGET_PEN_FORCE_N, 0, 0, 0]
PEN_FORCE_DIR = [0, 0, 1, 0, 0, 0]
PEN_COMPLIANCE_STIFFNESS = [5000, 5000, 5000, 400, 400, 400]


DR_init.__dsr__id = ROBOT_ID
DR_init.__dsr__model = ROBOT_MODEL


class RobotWriteController:
    def __init__(self, node, dsr):
        self.node = node
        self.dsr = dsr

        # 현재 수신 중인 작업
        self.stroke_queue = []
        self.expected_total = None


        # 실행 대기 중인 완성 작업
        self.job_ready = False
        self.pending_job_strokes = None
        self.pending_expected_total = None

        # 실행 상태 플래그
        self.is_executing = False

        self.config = self.load_config()
        robot_config = self.config.get('robot', {})

        # DSR 로봇 함수 연결
        self.movel = dsr['movel']
        self.movej = dsr['movej']
        self.posx = dsr['posx']
        self.posj = dsr['posj']

        # 붓펜 힘제어 함수 연결
        self.task_compliance_ctrl = dsr['task_compliance_ctrl']
        self.set_desired_force = dsr['set_desired_force']
        self.release_compliance_ctrl = dsr['release_compliance_ctrl']
        self.release_force = dsr['release_force']
        self.get_tool_force = dsr['get_tool_force']
        self.force_control_mode = dsr['DR_FC_MOD_ABS']

        # 로봇 속도/가속도 설정은 JSON 설정 파일에서 관리
        self.velx = float(robot_config.get('velx', 50))
        self.accx = float(robot_config.get('accx', 50))
        self.velj = float(robot_config.get('velj', 30))
        self.accj = float(robot_config.get('accj', 30))

        dsr['set_velx'](self.velx)
        dsr['set_accx'](self.accx)
        dsr['set_velj'](self.velj)
        dsr['set_accj'](self.accj)

        # connector_node가 보내는 stroke 명령 수신
        self.stroke_cmd_sub = self.node.create_subscription(
            StrokeCmd,
            '/calligraphy/stroke_cmd',
            self.stroke_cmd_callback,
            10
        )

         # 로봇 초기 설정
        dsr['set_tool']("Tool Weight")
        dsr['set_tcp']("Tool_v1")
        self.ref_coord = 101   # bootbot(id_101)
        dsr['set_ref_coord'](self.ref_coord)
        self.tool_coord = dsr['DR_TOOL']


        # Z값 / 펜 자세 / move 후 대기시간은 JSON 설정 파일에서 관리
        self.lift_up = float(robot_config.get('lift_up', 0.0))
        self.pen_up_extra = float(robot_config.get('pen_up_extra', 20.0))
        
        self.rx = float(robot_config.get('rx', 90.00))
        self.ry = float(robot_config.get('ry', 180.00))
        self.rz = float(robot_config.get('rz', 90.00))
        self.move_sleep = float(robot_config.get('move_sleep', 0.1))

        



        self.node.get_logger().info(
            'robot_write_drl_node started: NO THREAD spin_once mode, '
            f'lift_up={self.lift_up}, '
            f'rpy=({self.rx}, {self.ry}, {self.rz}), '
            f'force_control={USE_PEN_FORCE_CONTROL}, '
            f'target_force={TARGET_PEN_FORCE_N}N, max_force={MAX_PEN_FORCE_N}N'
        )

    # ============================================================
    # 설정 JSON 로드
    # ============================================================
    def load_config(self):
        config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE_NAME)

        default_config = {
            'robot': {
                'rx': 90.0,
                'ry': 180.0,
                'rz': 90.0,
                'velx': 50,
                'accx': 50,
                'velj': 30,
                'accj': 30,
                'move_sleep': 0.1,
            }
        }

        if not os.path.exists(config_path):
            self.node.get_logger().warn(
                f'{CONFIG_FILE_NAME} not found beside robot_write_drl_node. Using defaults.'
            )
            return default_config

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)

            self.node.get_logger().info(f'Loaded config: {config_path}')
            return loaded_config

        except Exception as e:
            self.node.get_logger().error(f'Failed to load {CONFIG_FILE_NAME}: {e}. Using defaults.')
            return default_config

    # ============================================================
    # stroke_cmd 수신 후 queue에 저장만 한다. 로봇 실행은 main loop에서 한다.
    # ============================================================
    def stroke_cmd_callback(self, msg):
        try:
            self.node.get_logger().info(
                f'CALLBACK ENTER: '
                f'is_executing={self.is_executing}, '
                f'job_ready={self.job_ready}, '
                f'expected_total={self.expected_total}, '
                f'queue_len={len(self.stroke_queue)}, '
                f'incoming_total={msg.total_count}, '
                f'incoming_stroke_id={msg.stroke_id}'
            )

            if self.is_executing:
                self.node.get_logger().warn('Currently executing robot motion. New StrokeCmd ignored.')
                return

            if self.job_ready:
                self.node.get_logger().warn('A complete job is already waiting. New StrokeCmd ignored.')
                return

            if not self.is_valid_stroke_msg(msg):
                self.node.get_logger().error(f'Invalid StrokeCmd msg: {msg}')
                return

            incoming_total = int(msg.total_count)

            # 첫 stroke에서 이번 작업의 전체 개수를 확정한다.
            if self.expected_total is None:
                self.expected_total = incoming_total
                self.node.get_logger().info(
                    f'Set expected_total from StrokeCmd.total_count: {self.expected_total}'
                )

            # 같은 작업 안에서 total_count가 바뀌면 위험하므로 작업을 차단한다.
            if incoming_total != self.expected_total:
                self.node.get_logger().error(
                    f'total_count mismatch inside job: '
                    f'incoming_total={incoming_total}, expected_total={self.expected_total}. '
                    f'Job reset.'
                )
                self.reset_receive_state()
                return

            stroke = self.msg_to_stroke(msg)
            self.stroke_queue.append(stroke)

            queued_count = len(self.stroke_queue)
            expected_total = self.expected_total

            self.node.get_logger().info(
                f'RECV /calligraphy/stroke_cmd '
                f'queued_count={queued_count}/{expected_total}, '
                f'stroke_id={stroke["stroke_id"]}, '
                f'part={stroke["part_name"]}, '
                f'action={stroke["action"]}'
            )

            if queued_count < expected_total:
                self.node.get_logger().info(
                    f'Waiting for strokes: queued_count={queued_count}/{expected_total}'
                )
                return

            if queued_count > expected_total:
                self.node.get_logger().error(
                    f'Queue count exceeded expected_total: '
                    f'{queued_count} > {expected_total}. Job reset.'
                )
                self.reset_receive_state()
                return

            # queue 준비 완료. 실행은 콜백 밖 main loop에서 한다.
            self.pending_job_strokes = list(self.stroke_queue)
            self.pending_expected_total = expected_total
            self.job_ready = True
            self.reset_receive_state()

            self.node.get_logger().info(
                f'Queue ready: queued_count={queued_count}/{expected_total}. '
                'Job marked ready. Robot motion will run in main loop.'
            )

        except Exception as e:
            self.node.get_logger().error(f'stroke_cmd_callback failed: {e}')

    # ============================================================
    # main loop에서 호출한다. thread 없이 완성된 작업 하나를 실행한다.
    # ============================================================
    def execute_ready_job_once(self):
        if self.is_executing or not self.job_ready:
            return

        job_strokes = self.pending_job_strokes
        expected_total = self.pending_expected_total

        self.pending_job_strokes = None
        self.pending_expected_total = None
        self.job_ready = False
        self.is_executing = True

        self.node.get_logger().info(
            f'MAIN EXEC START: expected_total={expected_total}, job_len={len(job_strokes)}'
        )

        try:
            self.execute_queue(job_strokes, expected_total)

        except Exception as e:
            self.node.get_logger().error(f'execute_ready_job_once failed: {e}')

        finally:
            self.is_executing = False
            self.reset_receive_state()
            self.node.get_logger().info(
                f'MAIN RESET DONE: '
                f'is_executing={self.is_executing}, '
                f'job_ready={self.job_ready}, '
                f'expected_total={self.expected_total}, '
                f'queue_len={len(self.stroke_queue)}'
            )

    # ============================================================
    # queue에 쌓인 stroke를 queue 순서 그대로 실행
    # ============================================================
    def execute_queue(self, job_strokes, expected_total):
        for index, stroke in enumerate(job_strokes, start=1):
            stroke_id = stroke['stroke_id']
            part_name = stroke['part_name']
            action = stroke['action']
            points = stroke['points']

            self.node.get_logger().info(
                f'EXEC stroke {index}/{expected_total}: '
                f'stroke_id={stroke_id}, part={part_name}, action={action}'
            )

            if action == 'draw':
                self.draw_stroke(stroke_id, points)
            else:
                self.node.get_logger().warn(f'Unsupported action: {action}')

        self.node.get_logger().info('All strokes executed')

        self.movej(
        self.posj(0.0, -25.0, 90.0, 0.0, 90.0, 90.0),
        vel=10,
        acc=10
         )
        
        self.node.get_logger().info('returning to ready pose')

    # ============================================================
    # 한 stroke의 points를 순서대로 movel 실행
    # ============================================================
    def draw_stroke(self, stroke_id, points):
        if len(points) == 0:
            self.node.get_logger().warn(f'Empty points. stroke_id={stroke_id}')
            return

        first_point = points[0]
        first_x, first_y = self.convert_xy(first_point['x'], first_point['y'])
        first_z = float(first_point['z']) + self.lift_up
        first_rx = float(first_point['rx'])
        first_ry = float(first_point['ry'])
        first_rz = float(first_point['rz'])

        last_x, last_y = first_x, first_y
        last_z = first_z
        last_rx, last_ry, last_rz = first_rx, first_ry, first_rz

        # 첫 점 위로 이동
        self.move_to(
            stroke_id,
            first_x,
            first_y,
            first_z + self.pen_up_extra,
            first_rx,
            first_ry,
            first_rz,
            'move_up'
        )

        # 펜 내리기
        self.move_to(
            stroke_id,
            first_x,
            first_y,
            first_z,
            first_rx,
            first_ry,
            first_rz,
            'pen_down'
        )

        time.sleep(0.5)

        force_enabled = False

        try:
            # 필기 중에만 Z방향 힘제어를 켠다.
            if USE_PEN_FORCE_CONTROL:
                self.start_pen_force_control(stroke_id)
                force_enabled = True

            # stroke 내부 point들을 따라 그리기
            for point in points:
                x, y = self.convert_xy(point['x'], point['y'])
                z = float(point['z']) + self.lift_up
                rx = float(point['rx'])
                ry = float(point['ry'])
                rz = float(point['rz'])

                # 이동 전 과압 확인
                self.check_pen_pressure_or_raise(stroke_id, last_x, last_y)

                self.move_to(stroke_id, x, y, z, rx, ry, rz, 'draw')

                last_x, last_y = x, y
                last_z = z
                last_rx, last_ry, last_rz = rx, ry, rz

                # 이동 후 과압 확인
                self.check_pen_pressure_or_raise(stroke_id, last_x, last_y)

        finally:
            # 힘제어가 켜진 상태로 남지 않도록 반드시 해제한다.
            if force_enabled:
                self.stop_pen_force_control(stroke_id)

            # 마지막 위치에서 펜 올리기
            pen_up_z = last_z + self.pen_up_extra
            
            self.move_to(
                stroke_id,
                last_x,
                last_y,
                pen_up_z,
                last_rx,
                last_ry,
                last_rz,
                'pen_up'
            )

    # ============================================================
    # 붓펜 힘제어 시작 / 종료 / 과압 감시
    # ============================================================
    def start_pen_force_control(self, stroke_id):
        self.node.get_logger().info(
            f'START pen force control: stroke_id={stroke_id}, '
            f'target_force={TARGET_PEN_FORCE_N}N, max_force={MAX_PEN_FORCE_N}N'
        )

        self.task_compliance_ctrl(stx=PEN_COMPLIANCE_STIFFNESS)

        self.set_desired_force(
            fd=PEN_FORCE_VECTOR,
            dir=PEN_FORCE_DIR,
            mod=self.force_control_mode
        )

    def stop_pen_force_control(self, stroke_id):
        self.node.get_logger().info(f'STOP pen force control: stroke_id={stroke_id}')

        try:
            self.release_force()
        except Exception as e:
            self.node.get_logger().error(f'release_force failed: {e}')

        try:
            self.release_compliance_ctrl()
        except Exception as e:
            self.node.get_logger().error(f'release_compliance_ctrl failed: {e}')

    def check_pen_pressure_or_raise(self, stroke_id, current_x, current_y):
        if not USE_PEN_FORCE_CONTROL:
            return

        force = self.get_tool_force(self.tool_coord)
        z_force = abs(float(force[PEN_FORCE_AXIS_INDEX]))

        self.node.get_logger().info(
            f'PEN_FORCE stroke_id={stroke_id}, z_force={z_force:.2f}N'
        )

        if z_force < MAX_PEN_FORCE_N:
            return

        self.node.get_logger().error(
            f'Pen over-force detected: stroke_id={stroke_id}, '
            f'z_force={z_force:.2f}N >= max_force={MAX_PEN_FORCE_N:.2f}N. '
            'Lift pen and stop current job.'
        )

        raise RuntimeError(
            f'Pen over-force: {z_force:.2f}N >= {MAX_PEN_FORCE_N:.2f}N'
        )

    # ============================================================
    # StrokeCmd msg를 기존 내부 처리용 stroke dict로 변환
    # ============================================================
    def msg_to_stroke(self, msg):
        point_count = len(msg.x)

        # 새 StrokeCmd 인터페이스:
        # float64[] x, y, z, rx, ry, rz
        # 단, 이전 송신부와의 호환을 위해 z/rx/ry/rz가 비어 있으면 config 기본값을 사용한다.
        z_list = list(msg.z)
        rx_list = list(msg.rx)
        ry_list = list(msg.ry)
        rz_list = list(msg.rz)

        points = []
        for x, y, z, rx, ry, rz in zip(msg.x, msg.y, z_list, rx_list, ry_list, rz_list):
            points.append({
                'x': float(x),
                'y': float(y),
                'z': float(z),
                'rx': float(rx),
                'ry': float(ry),
                'rz': float(rz),
            })

        return {
            'stroke_id': int(msg.stroke_id),
            'part_name': str(msg.part_name),
            'action': str(msg.action),
            'points': points,
        }

    # ============================================================
    # JSON 좌표를 로봇 좌표로 변환
    # 현재는 이미 base 좌표이므로 그대로 사용
    # ============================================================
    def convert_xy(self, json_x, json_y):
        robot_x = float(json_x)
        robot_y = float(json_y)

        return robot_x, robot_y

    # ============================================================
    # 실제 로봇 movel 실행
    # ============================================================
    def move_to(self, stroke_id, x, y, z, rx, ry, rz, action):
        target = self.posx(
            x,
            y,
            z,
            rx,
            ry,
            rz
        )

        self.node.get_logger().info(
            f'BEFORE movel stroke={stroke_id}, action={action}, '
            f'x={x:.2f}, y={y:.2f}, z={z:.2f}, '
            f'rx={rx:.2f}, ry={ry:.2f}, rz={rz:.2f}'
        )

        ret = self.movel(target, radius=0.1, ref=self.ref_coord)

        self.node.get_logger().info(
            f'AFTER movel stroke={stroke_id}, action={action}, ret={ret}'
        )

        time.sleep(self.move_sleep)

    # ============================================================
    # StrokeCmd msg 유효성 검사
    # ============================================================
    def is_valid_stroke_msg(self, msg):
        if msg.total_count <= 0:
            return False

        if msg.stroke_id < 0:
            return False

        if msg.part_name == '':
            return False

        if msg.action == '':
            return False

        point_count = len(msg.x)

        if point_count == 0:
            return False

        if len(msg.y) != point_count:
            return False

        # 새 인터페이스 배열 길이 검사.
        # 비어 있으면 기존 config 기본값으로 fallback 가능하게 허용한다.
        for field_name, values in (
            ('z', msg.z),
            ('rx', msg.rx),
            ('ry', msg.ry),
            ('rz', msg.rz),
        ):
            if len(msg.z) != point_count:
                self.node.get_logger().error(
                    f'Invalid StrokeCmd.z length: '
                    f'len(z)={len(msg.z)}, point_count={point_count}. '
                    'z is required in lift_up mode.'
                )
                return False

            for field_name, values in (
                ('rx', msg.rx),
                ('ry', msg.ry),
                ('rz', msg.rz),
            ):
                if len(values) not in (0, point_count):
                    self.node.get_logger().error(
                        f'Invalid StrokeCmd.{field_name} length: '
                        f'len({field_name})={len(values)}, point_count={point_count}'
                    )
                    return False

        return True

    # ============================================================
    # 다음 작업을 위한 수신 상태 초기화
    # ============================================================
    def reset_receive_state(self):
        self.stroke_queue.clear()
        self.expected_total = None


def main(args=None):
    rclpy.init(args=args)

    node = rclpy.create_node(
        'robot_write_drl_node',
        namespace=ROBOT_ID
    )

    DR_init.__dsr__node = node

    from DSR_ROBOT2 import set_velx, set_accx, set_velj, set_accj
    from DSR_ROBOT2 import movel, movej, posx, posj
    from DSR_ROBOT2 import task_compliance_ctrl, set_desired_force, release_force, release_compliance_ctrl
    from DSR_ROBOT2 import get_tool_force, DR_FC_MOD_REL, DR_FC_MOD_ABS
    from DSR_ROBOT2 import set_tool,set_tcp,set_ref_coord,DR_BASE,DR_TOOL

    dsr = {
        'set_velx': set_velx,
        'set_accx': set_accx,
        'set_velj': set_velj,
        'set_accj': set_accj,
        'movel': movel,
        'movej': movej,
        'posx': posx,
        'posj': posj,
        'task_compliance_ctrl': task_compliance_ctrl,
        'set_desired_force': set_desired_force,
        'release_compliance_ctrl': release_compliance_ctrl,
        'release_force': release_force,
        'get_tool_force': get_tool_force,
        'DR_FC_MOD_REL': DR_FC_MOD_REL,
        'DR_FC_MOD_ABS': DR_FC_MOD_ABS,
        'set_tool': set_tool,
        'set_tcp': set_tcp,
        'set_ref_coord': set_ref_coord,
        'DR_BASE': DR_BASE,
        'DR_TOOL': DR_TOOL,
    }

    controller = RobotWriteController(node, dsr)

    node.get_logger().info('robot_write_drl_node main loop started: spin_once + execute_ready_job_once')

    try:
        while rclpy.ok():
            # 콜백은 여기서만 처리한다.
            rclpy.spin_once(node, timeout_sec=0.1)

            # 완성된 작업이 있으면 같은 메인 루프에서 바로 실행한다.
            controller.execute_ready_job_once()

    except KeyboardInterrupt:
        node.get_logger().info('KeyboardInterrupt')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
