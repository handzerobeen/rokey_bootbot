import os

from ament_index_python.packages import get_package_share_directory
from flask import Flask, jsonify, render_template
from pymongo import ASCENDING, MongoClient


def create_app():
    share_dir = get_package_share_directory('bootbot')
    gui_web_dir = os.path.join(share_dir, 'gui_web')

    app = Flask(
        __name__,
        template_folder=os.path.join(gui_web_dir, 'templates'),
        static_folder=os.path.join(gui_web_dir, 'static'),
    )

    ws_host = os.environ.get('WS_HOST', 'localhost')
    ws_port = os.environ.get('WS_PORT', '8765')

    # 작성 기록(part_name 목록) 조회 전용. ROS2 도메인과는 무관하게 MongoDB를
    # 직접 읽기만 하므로, gui_web이 rclpy 없는 순수 Flask라는 원칙과 충돌하지 않는다.
    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017')
    stroke_plans = MongoClient(mongo_uri)['handwriting_db']['stroke_plans']

    # 첫 진입 화면 -- 배경 이미지 + "붓봇 사용하러 가기" 버튼만 있는 정적 랜딩 페이지.
    # WebSocket이 필요 없어 ws_host/ws_port를 넘기지 않는다.
    @app.route('/')
    def landing():
        return render_template('landing.html')

    # USER/DATABASE는 별도 페이지가 아니라 한 페이지 안의 탭 메뉴다. MANAGER만 예외 --
    # 로그인 없이 접근 가능한 USER/DATABASE와 달리 관리자 인증이 필요해서 별도 라우트
    # (/manager)로 분리했다 (tabs_designing.js가 MANAGER 탭 클릭 시 이 라우트로 이동시킨다).
    @app.route('/user')
    def user_page():
        return render_template(
            'user_designing.html',
            ws_host=ws_host,
            ws_port=ws_port,
        )

    # MANAGER 대시보드 -- 접속하면 먼저 인증 게이트가 뜨고, 통과해야 실제 대시보드가
    # 보인다. ID 검증 로직은 아직 없고(추후 구현 예정), 지금은 게이트의 "입장" 버튼을
    # 누르면 바로 통과된다 (manager_designing.html/js 참고).
    @app.route('/manager')
    def manager_page():
        return render_template(
            'manager_designing.html',
            ws_host=ws_host,
            ws_port=ws_port,
        )

    @app.route('/api/history')
    def history():
        names = [doc['_id'] for doc in stroke_plans.find({}, {'_id': 1}).sort('_id', ASCENDING)]
        return jsonify(names)

    @app.route('/api/history/<part_name>')
    def history_detail(part_name):
        # DATABASE 페이지 우측 상세 패널 전용. 좌표 배열(points)까지 그대로 내려주면
        # 상세 화면치고 너무 무거워지므로, stroke마다 point_count로 요약해서 반환한다.
        doc = stroke_plans.find_one({'_id': part_name})
        if not doc:
            return jsonify({'error': f'"{part_name}" 기록 없음'}), 404

        sessions = []
        for session in doc.get('sessions', []):
            strokes = sorted(session.get('strokes', []), key=lambda s: s.get('stroke_id', 0))
            sessions.append({
                'session_id': session.get('session_id'),
                'started_at': session['started_at'].isoformat() if session.get('started_at') else None,
                'stroke_count': len(strokes),
                'strokes': [
                    {
                        'stroke_id': stroke.get('stroke_id'),
                        'action': stroke.get('action'),
                        'point_count': len(stroke.get('points', [])),
                        'updated_at': stroke['updated_at'].isoformat() if stroke.get('updated_at') else None,
                    }
                    for stroke in strokes
                ],
            })
        # sessions는 Mongo에 $push된 순서(오래된 게 먼저)라서, 화면에는 최신 세션이
        # 위로 오도록 반대로 뒤집어서 내려준다. replay가 실제로 쓰는 sessions[-1]
        # 순서(가장 최근)는 이 반전과 무관 -- 여기는 응답 JSON만 뒤집는 것이다.
        sessions.reverse()

        return jsonify({'part_name': doc.get('part_name', part_name), 'sessions': sessions})

    @app.route('/api/history/<part_name>', methods=['DELETE'])
    def delete_history(part_name):
        # DATABASE 페이지 상세 패널의 "삭제"(글씨명 전체) 버튼 전용. part_name 문서
        # 전체(모든 세션/stroke 이력 포함)를 통째로 지운다 -- 복원 로직은 없으므로
        # 프론트엔드가 삭제 전 반드시 확인 팝업을 띄운다.
        result = stroke_plans.delete_one({'_id': part_name})
        if result.deleted_count == 0:
            return jsonify({'error': f'"{part_name}" 기록 없음'}), 404
        return jsonify({'deleted': part_name})

    @app.route('/api/history/<part_name>/sessions/<session_id>', methods=['DELETE'])
    def delete_session(part_name, session_id):
        # DATABASE 페이지 상세 패널의 세션별 "삭제" 버튼 전용. part_name 문서는
        # 그대로 두고 sessions 배열에서 해당 session_id 하나만 $pull로 제거한다.
        result = stroke_plans.update_one(
            {'_id': part_name},
            {'$pull': {'sessions': {'session_id': session_id}}},
        )
        if result.matched_count == 0:
            return jsonify({'error': f'"{part_name}" 기록 없음'}), 404
        if result.modified_count == 0:
            return jsonify({'error': f'세션 "{session_id}" 없음'}), 404

        # 마지막 남은 세션까지 지워서 sessions가 비면, 빈 문서만 덩그러니 남기지
        # 않고 part_name 문서 자체도 같이 정리한다 (목록에 빈 글씨명이 남는 것 방지).
        doc = stroke_plans.find_one({'_id': part_name}, {'sessions': 1})
        part_name_removed = doc is not None and not doc.get('sessions')
        if part_name_removed:
            stroke_plans.delete_one({'_id': part_name})

        return jsonify({
            'deleted_session': session_id,
            'part_name': part_name,
            'part_name_removed': part_name_removed,
        })

    return app


def main():
    app = create_app()
    port = int(os.environ.get('GUI_WEB_PORT', '5000'))
    app.run(host='0.0.0.0', port=port)


if __name__ == '__main__':
    main()
