import uuid
from datetime import datetime

from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure

_OLD_FLAT_UNIQUE_KEY = [
    ('header.part_name', ASCENDING),
    ('header.session_id', ASCENDING),
    ('header.stroke_id', ASCENDING),
]


class MongoStrokeRepository:
    """part_name(글씨명) 하나당 문서 하나로 묶고, 그 안에 작성 세션(session)별로
    stroke 배열을 중첩 저장하는 저장소.

    문서 구조:
        {
          "_id": "장동일",
          "part_name": "장동일",
          "sessions": [
            {"session_id": "...", "started_at": ..., "strokes": [
                {"stroke_id": 1, "action": "draw", "points": [...], "updated_at": ...},
                ...
            ]},
            ...
          ]
        }

    같은 part_name은 항상 같은 문서로 묶이고, 다시 쓸 때마다 새 session이
    sessions 배열에 추가되어 과거 작성 이력이 전부 보존된다. 같은 세션 안에서
    동일 stroke_id가 다시 들어오면(네트워크 재전송 등) 기존 stroke 항목을
    덮어써 중복을 막는다."""

    def __init__(self, mongo_uri, db_name='handwriting_db', collection_name='stroke_plans'):
        self._client = MongoClient(mongo_uri)
        self._collection = self._client[db_name][collection_name]
        try:
            self._collection.drop_index(_OLD_FLAT_UNIQUE_KEY)
        except OperationFailure:
            pass

    def new_session_id(self):
        return uuid.uuid4().hex

    def start_session(self, part_name, session_id):
        self._collection.update_one(
            {'_id': part_name},
            {
                '$setOnInsert': {'part_name': part_name},
                '$push': {
                    'sessions': {
                        'session_id': session_id,
                        'started_at': datetime.now(),
                        'strokes': [],
                    },
                },
            },
            upsert=True,
        )

    def upsert_stroke(self, part_name, session_id, stroke_id, action, points):
        # datetime.now(timezone.utc)를 쓰면 UTC로 저장되어 mongosh로 확인할 때
        # 실제 한국 시각(KST, 이 서버의 OS 타임존)보다 9시간 느리게 보인다. 이 프로젝트는
        # 로봇/서버가 항상 한국 한 지역에서만 돌아가므로(다른 타임존 배포 계획 없음), UTC
        # 변환 없이 tzinfo 없는 로컬(=KST) 시각을 그대로 저장한다.
        now = datetime.now()
        stroke_fields = {'action': action, 'points': points, 'updated_at': now}

        result = self._collection.update_one(
            {'_id': part_name},
            {'$set': {
                'sessions.$[sess].strokes.$[stroke].action': stroke_fields['action'],
                'sessions.$[sess].strokes.$[stroke].points': stroke_fields['points'],
                'sessions.$[sess].strokes.$[stroke].updated_at': stroke_fields['updated_at'],
            }},
            array_filters=[
                {'sess.session_id': session_id},
                {'stroke.stroke_id': stroke_id},
            ],
        )
        if result.modified_count > 0:
            return

        push_result = self._collection.update_one(
            {'_id': part_name, 'sessions.session_id': session_id},
            {'$push': {'sessions.$.strokes': {'stroke_id': stroke_id, **stroke_fields}}},
        )
        if push_result.matched_count == 0:
            # stroke_id==1 메시지가 유실되는 등으로 세션이 아직 만들어지지 않은 경우 -->
            # 세션을 지금 새로 만들고 한 번 더 시도한다 (데이터 유실 방지).
            self.start_session(part_name, session_id)
            self._collection.update_one(
                {'_id': part_name, 'sessions.session_id': session_id},
                {'$push': {'sessions.$.strokes': {'stroke_id': stroke_id, **stroke_fields}}},
            )

    def get_latest_session_strokes(self, part_name):
        """part_name 문서의 가장 최근(마지막) 세션의 stroke들을 stroke_id 순으로
        반환한다. 문서/세션이 없으면 None."""
        doc = self._collection.find_one({'_id': part_name})
        if not doc or not doc.get('sessions'):
            return None
        return sorted(doc['sessions'][-1]['strokes'], key=lambda s: s['stroke_id'])

    def close(self):
        self._client.close()
