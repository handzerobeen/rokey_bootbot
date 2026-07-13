import time
import os
import glob
import rclpy
from bootbot.svg_to_robot import trigger_parsing_and_publish

def main():
    rclpy.init()
    
    # 감시할 경로
    watch_dir = "/home/woods/ws_cobot_pjt/ws_dsr/robot_workspace"
    print(f"[POLLING] 감시 시작: {watch_dir}")
    
    try:
        while True:
            # 1. 1초 간격으로 폴더 확인 (CPU 부하를 줄임)
            time.sleep(3.0)
            
            # 2. 'Canvas'를 포함한 모든 .svg 파일 찾기
            pattern = os.path.join(watch_dir, "Canvas*.svg")
            files = [f for f in glob.glob(pattern) if os.path.isfile(f) and os.path.getsize(f) > 0]
            
            # 3. 파일이 발견되면 처리
            if files:
                print(f"[POLLING] 새 파일 감지, 처리 시작...")
                
                # 중복 호출 방지를 위해 잠시 대기 (rclone이 파일을 완전히 옮길 시간)
                time.sleep(1.0)
                
                # 처리 로직 호출
                trigger_parsing_and_publish()
                
                # 4. 처리 후 로컬 폴더에 Canvas.svg가 남아있다면 명시적 삭제 (루프 방지)
                for f in files:
                    if os.path.exists(f):
                        # 이미 svg_to_robot.py 내부에서 Done으로 옮겨지지만, 
                        # 안전하게 한번 더 체크하여 삭제
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                print("[POLLING] 작업 완료, 대기 중...")

    except KeyboardInterrupt:
        print("[POLLING] 감시 중단")
    finally:
        rclpy.shutdown()

if __name__ == "__main__":
    main()