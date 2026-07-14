import json
import os
import glob
import shutil
import time
import math
import rclpy
from calligraphy_interfaces.msg import InputJson 
from svgpathtools import svg2paths
import tempfile

class RobotPathProcessor:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, 'config.json')
        
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
            
        self.center = self.config['robot']
        self.wp = self.config['workpiece'] # 기울기 보정을 위해 추가
        self.scale = self.config['drawing']['scale']
        self.num_points = self.config['drawing']['num_points']
        self.watch_dir = self.config['path']['watch_dir']

    def process_and_package(self, svg_binary_data):
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as tmp:
            tmp.write(svg_binary_data)
            tmp_path = tmp.name
        try:
            paths, _ = svg2paths(tmp_path)
            if not paths: raise ValueError("SVG 데이터 없음")

            all_x, all_y = [], []
            for path in paths:
                for j in range(10):
                    p = path.point(j / 9)
                    all_x.append(p.real); all_y.append(p.imag)
            
            g_min_x, g_max_x = min(all_x), max(all_x)
            g_min_y, g_max_y = min(all_y), max(all_y)
            g_rx = (g_max_x - g_min_x) or 1
            g_ry = (g_max_y - g_min_y) or 1
            
            max_range = max(g_rx, g_ry)
            processed_list = []
            
            # 기울기 보정용 라디안 변환
            tilt_x = math.radians(self.wp['tilt_x'])
            
            for i, path in enumerate(paths):
                points = []
                for j in range(self.num_points):
                    t = j / (self.num_points - 1)
                    p = path.point(t)
                    
                    norm_x = (p.real - g_min_x) / max_range
                    norm_y = (p.imag - g_min_y) / max_range
                    
                    real_x = (norm_x - (g_rx / max_range) * 0.5) * self.scale
                    real_y = (norm_y - (g_ry / max_range) * 0.5) * self.scale
                    
                    flat_x = self.center['center_x'] + real_x
                    flat_y = self.center['center_y'] - real_y
                    
                    # 🌟 기울기 반영 3D 곡면 투영
                    H = self.wp['H']
                    R_b = self.wp['Rb']
                    R_t = self.wp['Rt']
                    
                    base_x = self.center['center_x']
                    base_y = self.center['center_y']
                    base_z = self.center['base_z']

                    h = flat_y - base_y
                    r = R_b + (R_t - R_b) * (h / H)
                    
                    arc_len = flat_x - base_x
                    theta = arc_len / r
                    
                    # 1. Z축 보정: 경사판 기울기(tilt_x) 및 높이별 비례 보정(gap_correction) 반영
                    C = 3.0
                    gap_correction = (1- (h / H)) * C
                    
                    # right_correction = max(0.0, real_x / (self.scale * 0.5)) * 6.0
                    final_x = base_x + r * math.sin(theta)
                    final_y = flat_y
                    final_z = base_z - (r * (1 - math.cos(theta)) * 1.05) + (real_x * math.sin(tilt_x)) - gap_correction  
                        # - right_correction
                    print(f"DEBUG: y={flat_y:.2f}, h={h:.2f}, gap={gap_correction:.4f}, final_z={final_z:.4f}")
                    # 2. RY축 보정: 부호 반전 및 기울기 반영 및 특이점 Damping
                    theta_deg = math.degrees(theta)
                    final_rx = self.center['base_rx'] + (real_x * math.cos(tilt_x) * 0.05)
                    
                    damping = 1.0 - (abs(real_x) / (self.scale * 1.5))
                    final_ry = self.center['base_ry'] + (theta_deg * damping)
                    final_rz = self.center['base_rz']

                    # 각도 정규화
                    if final_ry < -180.0: final_ry += 360.0
                    elif final_ry > 180.0: final_ry -= 360.0
                    
                    points.append({
                        "x": round(final_x, 3), "y": round(final_y, 3), "z": round(final_z, 3),
                        "rx": round(final_rx, 2), "ry": round(final_ry, 2), "rz": round(final_rz, 2)
                    })

                processed_list.append({"header": {"part_name": "original_drawing", "stroke_id": i + 1}, "action": "draw", "points": points})
            return len(paths), json.dumps(processed_list, ensure_ascii=False)
        finally:
            if os.path.exists(tmp_path): os.remove(tmp_path)

def trigger_parsing_and_publish(target_file=None):
    try:
        time.sleep(1.0)
        processor = RobotPathProcessor()
        watch_dir = processor.watch_dir
        done_dir = os.path.join(watch_dir, "Done")
        
        if target_file is None:
            pattern = os.path.join(watch_dir, "Canvas*.svg")
            files = [f for f in glob.glob(pattern) if os.path.isfile(f) and os.path.getsize(f) > 0]
            if not files: return
            target_file = max(files, key=os.path.getmtime)
            
        with open(target_file, 'rb') as f: svg_binary = f.read()
        count, json_string = processor.process_and_package(svg_binary)
        
        node = rclpy.create_node('svg_publisher_temp')
        pub = node.create_publisher(InputJson, '/calligraphy/input_json', 10)
        msg = InputJson()
        msg.total_count = count       
        msg.json_data = json_string   

        start_time = time.time()
        while pub.get_subscription_count() == 0 and (time.time() - start_time) < 2.0: time.sleep(0.1)
        pub.publish(msg)
        node.get_logger().info(f"성공: {target_file} 발행 완료!")
        
        time.sleep(0.5)
        if not os.path.exists(done_dir): os.makedirs(done_dir)
        shutil.move(target_file, os.path.join(done_dir, f"Canvas_{time.strftime('%Y%m%d_%H%M%S')}.svg"))
        node.destroy_node()
    except Exception as e: print(f"[ERROR] 처리 중 오류 발생: {e}")

if __name__ == "__main__":
    rclpy.init()
    trigger_parsing_and_publish()
    rclpy.shutdown()