import re
import xml.etree.ElementTree as ET

# 절대좌표 M/L/H/V/Q 명령만 지원한다 (이 패키지에서 실제로 오가는 서예 SVG가
# 이 명령들만 쓰기 때문). Q(2차 베지어)는 직선으로 뭉개지 않고 여러 점으로
# 샘플링해서 곡선 형태를 남긴다. C/S/T/A 등 그 외 명령은 끝점만 취한다.

_PATH_TOKEN_RE = re.compile(r'[MLHVCSQTAZ]|-?\d*\.?\d+(?:[eE][+-]?\d+)?', re.IGNORECASE)
_ARITY = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0}


def _quad_bezier_points(p0, p1, p2, segments=6):
    points = []
    for i in range(1, segments + 1):
        t = i / segments
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        points.append((x, y))
    return points


def _parse_path_d(d):
    """SVG path의 d 속성을 (x, y) 튜플 리스트로 변환한다."""
    tokens = _PATH_TOKEN_RE.findall(d)
    points = []
    current = (0.0, 0.0)
    cmd = None
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok.upper() in _ARITY:
            cmd = tok.upper()
            idx += 1
            continue
        if cmd is None:
            idx += 1
            continue

        arity = _ARITY[cmd]
        if arity == 0:  # Z (닫기), 좌표 없음
            idx += 1
            continue

        raw = tokens[idx:idx + arity]
        if len(raw) < arity:
            break
        nums = [float(n) for n in raw]
        idx += arity

        if cmd == 'M':
            current = (nums[0], nums[1])
            points.append(current)
            cmd = 'L'  # SVG 규격: M 뒤에 좌표쌍이 더 오면 암묵적으로 L
        elif cmd == 'L':
            current = (nums[0], nums[1])
            points.append(current)
        elif cmd == 'H':
            current = (nums[0], current[1])
            points.append(current)
        elif cmd == 'V':
            current = (current[0], nums[0])
            points.append(current)
        elif cmd == 'Q':
            control, end = (nums[0], nums[1]), (nums[2], nums[3])
            points.extend(_quad_bezier_points(current, control, end))
            current = end
        else:  # C/S/T/A -- 정확한 곡선 계산은 생략하고 끝점만 사용
            current = (nums[-2], nums[-1])
            points.append(current)

    return points


def parse_svg_to_strokes(svg_content, part_name):
    """SVG 문자열의 <path> 엘리먼트들을 stroke_plan과 동일한 스키마의 stroke 배열로
    변환한다: [{"header":{"part_name","stroke_id"},"action":"draw","points":[{"x","y"},...]}]
    <path> 하나 = stroke 하나, 문서 순서대로 stroke_id를 1부터 매긴다.

    ET.ParseError를 그대로 전파하니, 호출부에서 잡아서 로깅해야 한다."""
    root = ET.fromstring(svg_content)
    strokes = []
    stroke_id = 0
    for elem in root.iter():
        tag = elem.tag.split('}')[-1]  # 네임스페이스 접두어 제거
        if tag != 'path':
            continue
        d = elem.get('d')
        if not d:
            continue
        points = _parse_path_d(d)
        if len(points) < 2:
            continue

        stroke_id += 1
        strokes.append({
            'header': {'part_name': part_name, 'stroke_id': stroke_id},
            'action': 'draw',
            'points': [{'x': x, 'y': y} for x, y in points],
        })

    return strokes
