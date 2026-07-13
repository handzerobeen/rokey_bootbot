import copy
import json
import os
import threading
import time
from collections import deque
import rclpy
from rclpy.node import Node
from calligraphy_interfaces.msg import InputJson, StrokeCmd

CONFIG_FILE_NAME = 'calligraphy_config.json'

class JsonSendNode(Node):
    def __init__(self):
        super().__init__('connector_node')
        self.config = self.load_config()
        connector_config = self.config.get('connector', {})
        self.send_interval = float(connector_config.get('send_interval', 0.3))

        self.input_json_sub = self.create_subscription(InputJson, '/calligraphy/input_json', self.input_json_callback, 10)
        self.stroke_cmd_pub = self.create_publisher(StrokeCmd, '/calligraphy/stroke_cmd', 10)
        
        self.stroke_queue = deque()
        self.expected_total = None
        self.lock = threading.Lock()
        self.is_sending = False
        self.sender_thread = None

    # ... (기존 load_config, input_json_callback, handle_whole_job, parse_job_json, extract_total_count, looks_like_single_stroke, get_stroke_id 함수 유지) ...

    def load_config(self):
        config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE_NAME)

        default_config = {
            'connector': {
                'send_interval': 0.3,
            }
        }

        if not os.path.exists(config_path):
            self.get_logger().warn(
                f'{CONFIG_FILE_NAME} not found beside connector_node. Using defaults.'
            )
            return default_config

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)

            self.get_logger().info(f'Loaded config: {config_path}')
            return loaded_config

        except Exception as e:
            self.get_logger().error(f'Failed to load {CONFIG_FILE_NAME}: {e}. Using defaults.')
            return default_config

    # ============================================================
    # 외부 서버 JSON 수신
    # ============================================================
    def input_json_callback(self, msg):
        with self.lock:
            if self.is_sending:
                self.get_logger().warn('Currently sending. New input_json ignored.')
                return

        try:
            # 역직렬화: JSON 문자열 -> Python dict/list
            data = json.loads(msg.json_data)

            # 예: "{\"header\":{...}}" 처럼 JSON 문자열이 한 번 더 감싸져 들어온 경우 처리
            if isinstance(data, str):
                data = json.loads(data)

            self.handle_whole_job(data)

        except json.JSONDecodeError as e:
            self.get_logger().error(f'JSON deserialize failed: {e}')
        except Exception as e:
            self.get_logger().error(f'input_json_callback failed: {e}')

    # ============================================================
    # 통째 JSON 작업 처리
    # ============================================================
    def handle_whole_job(self, data):
        expected_total, strokes = self.parse_job_json(data)

        if expected_total is None:
            self.get_logger().error('total_count not found. Job rejected.')
            return

        if expected_total <= 0:
            self.get_logger().error(f'Invalid total_count={expected_total}. Job rejected.')
            return

        if not isinstance(strokes, list):
            self.get_logger().error('strokes field is not a list. Job rejected.')
            return

        temp_queue = deque()
        expected_total = int(expected_total)

        self.get_logger().info(
            f'RECV whole job JSON: total_count={expected_total}, json_strokes={len(strokes)}'
        )

        # JSON 배열 위에서부터 순서대로 queue에 넣는다.
        # stroke_id는 header 안에 보존하지만 정렬 기준으로 사용하지 않는다.
        for stroke in strokes:
            if not self.is_valid_stroke(stroke):
                self.get_logger().error(f'Invalid stroke found. Job rejected: {stroke}')
                return

            temp_queue.append(copy.deepcopy(stroke))

            self.get_logger().info(
                f'QUEUE stroke: count={len(temp_queue)}/{expected_total}, '
                f'stroke_id={self.get_stroke_id(stroke)}, '
                f'part={stroke["header"]["part_name"]}, '
                f'action={stroke["action"]}'
            )

            if len(temp_queue) > expected_total:
                self.get_logger().error(
                    f'Queue count exceeded total_count: {len(temp_queue)} > {expected_total}. Job rejected.'
                )
                return

        if len(temp_queue) != expected_total:
            self.get_logger().error(
                f'total_count mismatch: total_count={expected_total}, '
                f'queued_count={len(temp_queue)}. Job rejected.'
            )
            return

        with self.lock:
            if self.is_sending:
                self.get_logger().warn('Currently sending. New validated job ignored.')
                return

            self.reset_job_state_locked()
            self.expected_total = expected_total
            self.stroke_queue = temp_queue
            self.is_sending = True

            self.sender_thread = threading.Thread(
                target=self.publish_in_queue_order,
                daemon=True
            )
            self.sender_thread.start()

        self.get_logger().info(
            f'total_count matched: {len(temp_queue)}/{expected_total}. '
            'Publishing thread started.'
        )

    # ============================================================
    # JSON 형식 파싱
    # ============================================================
    def parse_job_json(self, data):
        # 새 표준 형식: {"total_count": N, "strokes": [...]}
        if isinstance(data, dict):
            expected_total = self.extract_total_count(data)
            strokes = data.get('strokes')

            # 호환용: {"total_count": N, "stroke_list": [...]}
            if strokes is None:
                strokes = data.get('stroke_list')

            # 호환용: stroke 하나만 dict로 들어온 경우
            if expected_total is None and self.looks_like_single_stroke(data):
                return 1, [data]

            return expected_total, strokes

        # 호환용: list만 온 경우에는 total_count를 list 길이로 간주
        if isinstance(data, list):
            self.get_logger().warn(
                'Received bare stroke list without total_count. '
                'Using len(list) as total_count for compatibility.'
            )
            return len(data), data

        return None, None

    # ============================================================
    # total_count 추출
    # ============================================================
    def extract_total_count(self, data):
        for key in ('total_count', 'total_strokes', 'stroke_count', 'total'):
            if key in data:
                try:
                    return int(data[key])
                except Exception:
                    self.get_logger().error(f'Invalid {key}: {data[key]}')
                    return None

        return None

    # ============================================================
    # stroke 하나짜리 dict인지 간단 판별
    # ============================================================
    def looks_like_single_stroke(self, data):
        return (
            isinstance(data, dict)
            and 'header' in data
            and isinstance(data['header'], dict)
            and 'part_name' in data['header']
            and 'stroke_id' in data['header']
            and 'action' in data
            and 'points' in data
        )

    # ============================================================
    # stroke_id 추출
    # ============================================================
    def get_stroke_id(self, stroke):
        return int(stroke['header']['stroke_id'])
    
    # ============================================================
    # queue 순서 그대로 publish
    # ============================================================
    def publish_in_queue_order(self):
        try:
            with self.lock:
                expected_total = self.expected_total
                output_strokes = [copy.deepcopy(stroke) for stroke in self.stroke_queue]

            # queue 순서 그대로 로봇 노드로 publish
            for index, stroke in enumerate(output_strokes, start=1):
                msg = self.stroke_to_msg(stroke, expected_total)
                self.stroke_cmd_pub.publish(msg)

                self.get_logger().info(
                    f'PUB /calligraphy/stroke_cmd '
                    f'count={index}/{expected_total}, '
                    f'total_count={msg.total_count}, '
                    f'stroke_id={self.get_stroke_id(stroke)}, '
                    f'part={stroke["header"]["part_name"]}, '
                    f'action={stroke["action"]}'
                )

                time.sleep(self.send_interval)

            self.get_logger().info(
                f'All StrokeCmd published. total_count={expected_total}'
            )

        except Exception as e:
            self.get_logger().error(f'publish_in_queue_order failed: {e}')

        finally:
            with self.lock:
                self.reset_job_state_locked()
                self.is_sending = False
                self.sender_thread = None


    # ============================================================
    # 🌟 수정된 부분: stroke dict를 StrokeCmd msg로 변환할 때 rx, ry, rz 추가
    # ============================================================
    def stroke_to_msg(self, stroke, expected_total):
        msg = StrokeCmd()
        msg.total_count = int(expected_total)
        msg.stroke_id = self.get_stroke_id(stroke)
        msg.part_name = str(stroke['header']['part_name'])
        msg.action = str(stroke['action'])

        robot_cfg = self.config.get('robot', {})
        default_z = float(robot_cfg.get('z_down', 195.0))

        points = stroke['points']

        msg.x = []
        msg.y = []
        msg.z = []
        msg.rx = []
        msg.ry = []
        msg.rz = []

        # stroke 단위 배열로 들어오는 호환 형식도 지원
        stroke_z = stroke.get('z', [])

        for index, point in enumerate(points):
            x = float(point['x'])
            y = float(point['y'])

            z = self.pick_pose_value(point, 'z', stroke_z, index, default_z)

            # 각 point의 로봇 자세값은 필수
            missing_pose_keys = [
                key for key in ('rx', 'ry', 'rz')
                if key not in point
            ]

            if missing_pose_keys:
                raise ValueError(
                    f'stroke_id={msg.stroke_id}, point_index={index}: '
                    f'필수 자세값 누락={missing_pose_keys}'
                )

            rx = float(point['rx'])
            ry = float(point['ry'])
            rz = float(point['rz'])

            msg.x.append(x)
            msg.y.append(y)
            msg.z.append(float(z))
            msg.rx.append(float(rx))
            msg.ry.append(float(ry))
            msg.rz.append(float(rz))

        return msg

    def pick_pose_value(self, point, key, stroke_values, index, default_value):
        if key in point:
            return float(point[key])

        if isinstance(stroke_values, list) and len(stroke_values) > index:
            return float(stroke_values[index])

        return float(default_value)

    # ... (나머지 is_valid_stroke, reset_job_state_locked 함수 동일) ...

# ... (main 함수 동일) ...

# ============================================================
    # stroke JSON 유효성 검사
    # ============================================================
    def is_valid_stroke(self, stroke):
        if not isinstance(stroke, dict):
            return False

        if 'header' not in stroke or not isinstance(stroke['header'], dict):
            return False

        if 'part_name' not in stroke['header']:
            return False

        if 'stroke_id' not in stroke['header']:
            return False

        try:
            int(stroke['header']['stroke_id'])
        except Exception:
            return False

        if 'action' not in stroke:
            return False

        if 'points' not in stroke or not isinstance(stroke['points'], list):
            return False

        if len(stroke['points']) == 0:
            return False

        point_count = len(stroke['points'])

        # stroke 단위 배열 형식이 들어오는 경우 길이 검사
        for key in ('z', 'rx', 'ry', 'rz'):
            if key in stroke:
                if not isinstance(stroke[key], list):
                    return False
                if len(stroke[key]) != point_count:
                    self.get_logger().error(
                        f'Invalid stroke.{key} length: '
                        f'len({key})={len(stroke[key])}, point_count={point_count}'
                    )
                    return False

        for point in stroke['points']:
            if not isinstance(point, dict):
                return False

            if 'x' not in point or 'y' not in point:
                return False

            try:
                float(point['x'])
                float(point['y'])

                # point 안에 z/rx/ry/rz가 있으면 숫자 변환 가능해야 한다.
                for key in ('z', 'rx', 'ry', 'rz'):
                    if key in point:
                        float(point[key])

            except Exception:
                return False

        return True

    # ============================================================
    # 다음 작업을 위한 상태 초기화
    # lock을 이미 잡은 상태에서만 호출
    # ============================================================
    def reset_job_state_locked(self):
        self.stroke_queue.clear()
        self.expected_total = None


def main(args=None):
    rclpy.init(args=args)

    node = JsonSendNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('KeyboardInterrupt')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
