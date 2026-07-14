(function () {
  const statusEl = document.getElementById('status');
  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d');
  const contactForceEl = document.getElementById('contact-force');

  ctx.strokeStyle = '#111';
  ctx.lineWidth = 4;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  let lastPoint = null;
  let lastPartName = null;
  let lastSessionSeq = null;

  // 로봇 base 좌표계 mm 값 -> 캔버스 픽셀 좌표 변환.
  //
  // coord_space === 'robot_mm' 경로는 로봇측(calligraphy_robot, 0710 스테이징 기준)이
  // 평면이 아니라 진짜 3D 원뿔대(frustum) 곡면 위에 글씨를 쓰는 방식으로 바뀌어서, 단순
  // x,y 평면 bbox 매핑으로는 곡면 왜곡을 그대로 화면에 담게 된다. 원뿔대 옆면은 전개
  // 가능한 곡면(원뿔을 펼치면 부채꼴이 되는 것과 같은 원리)이라, 로봇측 svg_to_robot.py가
  // 평면 SVG 좌표를 곡면에 투영하는 공식을 그대로 역으로 계산해서 "펼친" 평면 좌표
  // (arc_len, h)로 되돌린 뒤 그 좌표를 캔버스에 매핑한다.
  //
  // 아래 5개 상수는 calligraphy_robot/config.json(0710 스테이징: robot.center_x/center_y,
  // workpiece.H/Rb/Rt, drawing.scale)과 반드시 맞춰야 한다 -- 두 패키지가 서로 의존하지
  // 않는 별도 구현이라 자동으로 동기화되지 않는다. 로봇측 값이 바뀌면(특히 병합 시점에
  // 최종 확정되면) 여기도 같이 갱신해야 한다.
  const FRUSTUM_CENTER_X = 177.00; // config.json robot.center_x
  const FRUSTUM_CENTER_Y = 42.00;  // config.json robot.center_y
  const FRUSTUM_H = 220.0;         // config.json workpiece.H
  const FRUSTUM_RB = 50.0;         // config.json workpiece.Rb (바닥 반지름)
  const FRUSTUM_RT = 52.5;         // config.json workpiece.Rt (윗면 반지름)
  const DRAWING_SCALE = 80.0;      // config.json drawing.scale

  // 펼친(unwrap) 좌표계에서의 캔버스 매핑 범위. svg_to_robot.py가 SVG를 이 scale 기준으로
  // 중앙 정렬해서 정규화하므로, 펼친 좌표(arc_len, h)도 대략 center 기준 ±scale/2 안에
  // 들어온다고 가정한다. 0710 config.json에는 (예전 평면 config에 있던) num_chars/
  // char_spacing이 더 이상 없어 -- 여러 글자를 옆으로 나란히 쓰는 레이아웃은 로봇측에서도
  // 아직 정의돼 있지 않은 것으로 보여, 글자 하나 기준의 대칭 bbox로 근사한다.
  const UNWRAPPED_ARC_MIN = -DRAWING_SCALE / 2;
  const UNWRAPPED_ARC_MAX = DRAWING_SCALE / 2;
  const UNWRAPPED_H_MIN = -DRAWING_SCALE / 2;
  const UNWRAPPED_H_MAX = DRAWING_SCALE / 2;

  // 로봇 base mm 좌표(x,y) -> 원뿔대 곡면을 펼친 평면 좌표(arc_len, h)로 역변환.
  // 로봇측 svg_to_robot.py 정투영 공식(순방향)의 역계산:
  //   h = flat_y - center_y
  //   r = Rb + (Rt - Rb) * (h / H)
  //   theta = asin((flat_x - center_x) / r)
  //   arc_len = theta * r
  // z/rx/ry/rz는 tilt_x 보정에만 쓰이고 x,y 자체엔 영향을 안 줘서 펼치기 계산엔 필요 없다.
  function unwrapFrustumPoint(x, y) {
    const h = y - FRUSTUM_CENTER_Y;
    const r = FRUSTUM_RB + (FRUSTUM_RT - FRUSTUM_RB) * (h / FRUSTUM_H);
    const sinTheta = Math.max(-1, Math.min(1, (x - FRUSTUM_CENTER_X) / r));
    const theta = Math.asin(sinTheta);
    return { arcLen: theta * r, h };
  }

  function robotMmToCanvasPx(point) {
    const { arcLen, h } = unwrapFrustumPoint(point.x, point.y);

    const rangeArc = UNWRAPPED_ARC_MAX - UNWRAPPED_ARC_MIN;
    const rangeH = UNWRAPPED_H_MAX - UNWRAPPED_H_MIN;
    const scale = Math.min(canvas.width / rangeArc, canvas.height / rangeH);
    const drawWidth = rangeArc * scale;
    const drawHeight = rangeH * scale;
    const offsetX = (canvas.width - drawWidth) / 2;
    const offsetY = (canvas.height - drawHeight) / 2;
    // 기존과 동일하게 세로축만 반전(거울 대칭, 180도 회전 아님) -- 실기로 검증된 방향을
    // h축에도 그대로 유지한다 (X축은 그대로 둠).
    return {
      x: offsetX + (arcLen - UNWRAPPED_ARC_MIN) * scale,
      y: offsetY + (UNWRAPPED_H_MAX - h) * scale,
    };
  }

  function setStatus(connected, text) {
    statusEl.textContent = text;
    statusEl.classList.toggle('status--connected', connected);
    statusEl.classList.toggle('status--disconnected', !connected);
  }

  function clearCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    lastPoint = null;
  }

  // MANAGER 탭의 TCP 좌표 표시와 동일한 방식 -- 로그처럼 쌓이지 않고, 매번 같은
  // 자리의 텍스트를 최신 값으로 덮어쓴다.
  function updateContactForce(forceN) {
    if (typeof forceN !== 'number') return;
    contactForceEl.textContent = `외압: ${forceN.toFixed(2)} N`;
  }

  // 좌표는 기본적으로 캔버스 픽셀 좌표계(0~canvas.width, 0~canvas.height)로
  // 들어온다는 게 Client(SVG 파싱 경로)와 합의된 규격이라는 전제라, 그 경우는
  // 임의로 스케일/이동하지 않고 받은 좌표를 그대로 1:1로 그린다 -- 규격을 벗어나는
  // 좌표가 오면 화면 밖으로 나가거나 잘리는 게 정상이며, 그건 Client 쪽에서 규격을
  // 맞춰야 할 문제다.
  //
  // 다만 header.coord_space === 'robot_mm'인 경우(server_connector_node가 실제
  // 로봇의 get_current_posx를 폴링해서 보내는 경로)는 로봇 base 좌표계의 mm 값이라
  // 캔버스 픽셀 범위와 단위 자체가 다르므로, 이때만 robotMmToCanvasPx로 변환한다.
  function handlePenPoint(payload) {
    const partName = payload.header.part_name;
    const strokeId = payload.header.stroke_id;
    const sessionSeq = payload.header.session_seq;
    const point = payload.header.coord_space === 'robot_mm'
      ? robotMmToCanvasPx(payload.point)
      : payload.point;

    // part_name이 이전과 같아도(예: 같은 글자를 다시 테스트/재작성) stroke_id가 1로
    // 돌아오는 시점이 새 세션의 시작이므로, 그때도 캔버스를 새로 지운다.
    // 다만 실제 로봇 경로(coord_space === 'robot_mm')는 get_current_posx 폴링이라
    // strokeId가 항상 0으로 고정되어 위 조건만으로는 "같은 글씨 재작성"을 감지하지
    // 못한다 -- server_connector_node가 real stroke_id==1마다 올려 보내는
    // session_seq가 있으면 그걸로 새 세션 여부를 판단한다.
    const isNewSession = partName !== lastPartName
      || (strokeId === 1 && payload.action === 'move')
      || (sessionSeq !== undefined && sessionSeq !== lastSessionSeq);

    if (isNewSession) {
      clearCanvas();
      lastPartName = partName;
      lastSessionSeq = sessionSeq;
    }

    if (payload.action === 'draw' && lastPoint) {
      ctx.beginPath();
      ctx.moveTo(lastPoint.x, lastPoint.y);
      ctx.lineTo(point.x, point.y);
      ctx.stroke();
    }

    lastPoint = point;
  }

  // server_connector_node가 직접 여는 WebSocket 서버에 접속 (rosbridge 미사용, 자체 프로토콜:
  // 서버가 받은 pen_point JSON 문자열을 가공 없이 그대로 브로드캐스트한다)
  //
  // 재연결 지원: 원래는 페이지 로드 시 한 번만 소켓을 열고 끝이었는데, 백엔드
  // 노드를 재시작할 때마다(개발 중 흔함) 브라우저 소켓이 조용히 끊긴 채로 남아
  // 새로고침 전까지 아무 데이터도 안 들어오는 문제가 있었다. 그런데 MANAGER 탭의
  // "Connected" 배지는 소켓 상태가 아니라 마지막으로 받은 dashboard 메시지의
  // connected 필드만 보고 있어서, 소켓이 죽어도 마지막 값에 그대로 고정돼 마치
  // 계속 연결된 것처럼 보이는 게 더 큰 문제였다 -- 그래서 소켓을 아예 재연결까지
  // 하도록 고친다. window.__rokeySocket은 재연결마다 새 WebSocket 인스턴스로
  // 교체되므로, 이 인스턴스를 직접 참조(closure)하면 재연결 후 죽은 객체를 계속
  // 참조하게 된다 -- 아래 runBtn 핸들러는 그래서 매번
  // window.__rokeySocket을 다시 읽도록 바꿨다. open/close 시점마다
  // 'rokeysocket:open'/'rokeysocket:close' 커스텀 이벤트를 window에 쏴서,
  // manager_designing.js가 (a) 새 소켓에 message 리스너를 다시 붙이고 (b) 소켓이
  // 죽은 순간 즉시 "연결 끊김"을 반영할 수 있게 한다.
  function connectSocket() {
    const socket = new WebSocket(`ws://${window.WS_HOST}:${window.WS_PORT}`);
    window.__rokeySocket = socket;

    socket.addEventListener('open', () => {
      setStatus(true, '서버 연결됨');
      window.dispatchEvent(new CustomEvent('rokeysocket:open'));
    });
    socket.addEventListener('close', () => {
      setStatus(false, '서버 연결 끊김 (재연결 시도 중...)');
      window.dispatchEvent(new CustomEvent('rokeysocket:close'));
      setTimeout(connectSocket, 2000);
    });
    socket.addEventListener('error', () => setStatus(false, '서버 연결 오류'));

    socket.addEventListener('message', (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (e) {
        console.error('메시지 파싱 실패', e, event.data);
        return;
      }
      // MANAGER 탭의 dashboard/manager_log 메시지는 이 소켓을 공유하는
      // manager_designing.js가 따로 처리한다 -- header가 없는 pen_point가 아닌
      // 형식이니 여기서는 건드리지 않는다 (예전엔 이 구분이 없어서 매 dashboard
      // 메시지마다 "Cannot read properties of undefined (reading 'part_name')"
      // 에러를 던졌었다).
      if (payload.type === 'dashboard' || payload.type === 'manager_log') return;
      if (payload.type === 'contact_force') {
        updateContactForce(payload.force_n);
        return;
      }
      try {
        handlePenPoint(payload);
      } catch (e) {
        console.error('pen_point 파싱 실패', e, event.data);
      }
    });
  }

  connectSocket();

  // charStatusEl: 재실행(replay) 요청 결과 메시지를 보여주는 데 쓴다 (SVG 브라우저
  // 업로드 폼은 백엔드가 더 이상 받지 않아 삭제됨 -- SVG 제출은 calligraphy_robot
  // 쪽에서 직접 처리).
  const charStatusEl = document.getElementById('char-request-status');

  // 작성 기록: /api/history(gui_web이 MongoDB를 직접 조회)로 part_name 목록을 받아와
  // 제목을 누르면 그 아래로 살짝 펼쳐지며 작성 시각 + "실행" 버튼이 함께 나온다.
  // 실행을 누르면 {"replay": part_name}을 WebSocket으로 보내 server_connector_node가
  // 저장된 마지막 세션을 다시 재생하게 한다.
  const historyListEl = document.getElementById('history-list');

  async function loadHistory() {
    let names = [];
    try {
      const res = await fetch('/api/history');
      names = await res.json();
    } catch (e) {
      console.error('작성 기록 조회 실패', e);
      return;
    }

    historyListEl.innerHTML = '';
    for (const name of names) {
      const li = document.createElement('li');

      const titleBtn = document.createElement('button');
      titleBtn.type = 'button';
      titleBtn.className = 'history-title';
      titleBtn.textContent = name;

      const expandEl = document.createElement('div');
      expandEl.className = 'history-expand';
      expandEl.hidden = true;

      const timeEl = document.createElement('span');
      timeEl.className = 'history-time';
      timeEl.textContent = '작성 시각: 불러오는 중...';

      const runBtn = document.createElement('button');
      runBtn.type = 'button';
      runBtn.className = 'history-run';
      runBtn.textContent = '실행';

      expandEl.appendChild(timeEl);
      expandEl.appendChild(runBtn);

      // 펼칠 때마다 다시 조회할 필요는 없으니, 처음 펼칠 때 한 번만 작성 시각을
      // 가져온다 (/api/history/<part_name>은 DATABASE 탭에서 이미 쓰던 API를 그대로
      // 재사용 -- 세션은 최신순으로 오므로 sessions[0]이 곧 마지막 작성 시각).
      let timeLoaded = false;

      titleBtn.addEventListener('click', async () => {
        const opening = expandEl.hidden;
        expandEl.hidden = !opening;
        if (!opening || timeLoaded) return;

        timeLoaded = true;
        try {
          const res = await fetch(`/api/history/${encodeURIComponent(name)}`);
          const data = await res.json();
          const latest = data.sessions && data.sessions[0];
          timeEl.textContent = latest
            ? `작성 시각: ${new Date(latest.started_at).toLocaleString('ko-KR')}`
            : '작성 시각: 정보 없음';
        } catch (e) {
          timeEl.textContent = '작성 시각: 조회 실패';
        }
      });

      runBtn.addEventListener('click', () => {
        if (window.__rokeySocket.readyState !== WebSocket.OPEN) {
          charStatusEl.textContent = '서버에 연결되어 있지 않습니다.';
          return;
        }
        window.__rokeySocket.send(JSON.stringify({ replay: name }));
        charStatusEl.textContent = `"${name}" 재실행 요청 전송`;
      });

      li.appendChild(titleBtn);
      li.appendChild(expandEl);
      historyListEl.appendChild(li);
    }
  }

  loadHistory();
})();
