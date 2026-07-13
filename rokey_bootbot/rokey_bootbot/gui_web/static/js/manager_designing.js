(function () {
  // MANAGER 탭: 로봇 모니터링(조인트/TCP/상태/통신) + 제어(정지/비상정지/재개/조인트 이동).
  // main_designing.js가 연 WebSocket(window.__rokeySocket)을 그대로 공유해서 쓴다 --
  // server_connector_node_designing이 보내는 {"type":"dashboard"|"manager_log", ...}
  // 메시지만 골라서 처리하고, USER 탭의 pen_point 메시지는 무시한다.
  //
  // 정지/재개/비상정지/안전상태 해제는 사용자 요청으로 confirm() 확인 절차 없이 클릭
  // 즉시 실행된다 (이 웹페이지에 인증이 없다는 제약은 여전하지만, 네 버튼은 빠른 대응이
  // 우선이라는 판단). 조인트 이동은 실제 목표 각도를 입력하는 실수 위험이 있어
  // confirm()을 유지.

  const els = {
    connection: document.getElementById('mgr-connection'),
    tcpX: document.getElementById('mgr-tcp-x'),
    tcpY: document.getElementById('mgr-tcp-y'),
    tcpZ: document.getElementById('mgr-tcp-z'),
    tcpW: document.getElementById('mgr-tcp-w'),
    tcpP: document.getElementById('mgr-tcp-p'),
    tcpR: document.getElementById('mgr-tcp-r'),
    action: document.getElementById('mgr-action'),
    state: document.getElementById('mgr-state'),
    mode: document.getElementById('mgr-mode'),
    speedLimit: document.getElementById('mgr-speed-limit'),
    fault: document.getElementById('mgr-fault'),
    commWs: document.getElementById('mgr-comm-ws'),
    commRate: document.getElementById('mgr-comm-rate'),
    commDisconnect: document.getElementById('mgr-comm-disconnect'),
    collision: document.getElementById('mgr-collision'),
    log: document.getElementById('mgr-log'),
    btnStop: document.getElementById('mgr-btn-stop'),
    btnEstop: document.getElementById('mgr-btn-estop'),
    btnReset: document.getElementById('mgr-btn-reset'),
    btnSafetyReset: document.getElementById('mgr-btn-safety-reset'),
    moveForm: document.getElementById('mgr-move-form'),
  };

  const jointEls = [1, 2, 3, 4, 5, 6].map((i) => ({
    slider: document.getElementById(`mgr-joint-${i}`),
    val: document.getElementById(`mgr-joint-${i}-val`),
  }));

  // robot_state 라벨 -> 심각도 스타일 (색상은 CSS 클래스로만 표현, 값 자체는 서버가
  // 준 그대로 사용 -- 심각도 분류는 순수 표시용이라 여기(뷰 계층)에 둔다).
  const STATE_SEVERITY = {
    STANDBY: 'ok', MOVING: 'ok', HOMMING: 'ok', RECOVERY: 'ok',
    TEACHING: 'caution', INITIALIZING: 'caution',
    SAFE_STOP: 'warn', SAFE_OFF: 'warn', SAFE_STOP2: 'warn', SAFE_OFF2: 'warn',
    EMERGENCY_STOP: 'danger',
    NOT_READY: 'unknown',
  };

  const SPEED_MODE_SEVERITY = { NORMAL: 'ok', REDUCED: 'caution' };
  const FAULT_LEVEL_SEVERITY = { INFO: 'info', WARN: 'caution', ERROR: 'danger' };

  function appendLog(text) {
    if (!els.log) return;
    const time = new Date().toLocaleTimeString('ko-KR');
    els.log.textContent += `[${time}] ${text}\n`;
    els.log.scrollTop = els.log.scrollHeight;
  }

  function setPill(el, text, variant) {
    if (!el) return;
    el.textContent = text;
    el.className = el.className.replace(/\bmgr-pill--\S+/g, '').trim();
    el.classList.add('mgr-pill', `mgr-pill--${variant}`);
  }

  function fmt(n) {
    return typeof n === 'number' ? n.toFixed(2) : '-';
  }

  function updateConnection(connected) {
    setPill(els.connection, connected ? 'Connected' : 'Disconnected', connected ? 'ok' : 'danger');
  }

  function updateTcp(tcp) {
    if (!tcp) return;
    els.tcpX.textContent = fmt(tcp.x);
    els.tcpY.textContent = fmt(tcp.y);
    els.tcpZ.textContent = fmt(tcp.z);
    els.tcpW.textContent = fmt(tcp.w);
    els.tcpP.textContent = fmt(tcp.p);
    els.tcpR.textContent = fmt(tcp.r);
    // action 배지는 원본 참고 코드의 draw/move 하드코딩 색(mgr-pill--action-*)을 그대로 씀.
    setPill(els.action, tcp.action, `action-${tcp.action === 'draw' ? 'draw' : 'move'}`);
  }

  function updateJoints(positionsDeg) {
    if (!Array.isArray(positionsDeg)) return;
    positionsDeg.forEach((deg, i) => {
      const target = jointEls[i];
      if (!target) return;
      const clamped = Math.max(-180, Math.min(180, deg));
      target.slider.value = clamped;
      target.val.textContent = `${Math.round(deg)}°`;
    });
    // manager_viewer_designing.js가 있으면(3D 뷰어 프로토타입) 같은 값을 그대로 전달.
    if (typeof window.__mgrViewerSetJointsDeg === 'function') {
      window.__mgrViewerSetJointsDeg(positionsDeg);
    }
  }

  function updateRobotState(robotState) {
    if (!robotState || robotState.label == null) {
      setPill(els.state, '확인 안 됨', 'unknown');
      return;
    }
    setPill(els.state, robotState.label, STATE_SEVERITY[robotState.label] || 'unknown');
  }

  function updateRobotMode(robotMode) {
    const label = (robotMode && robotMode.label) ? robotMode.label : '확인 안 됨';
    setPill(els.mode, `Mode: ${label}`, robotMode && robotMode.label ? 'info' : 'unknown');
  }

  function updateSpeedMode(speedMode) {
    if (!speedMode || speedMode.label == null) {
      setPill(els.speedLimit, '확인 필요', 'unknown');
      return;
    }
    setPill(els.speedLimit, speedMode.label, SPEED_MODE_SEVERITY[speedMode.label] || 'unknown');
  }

  function updateFault(fault) {
    // /error 토픽은 "지금 fault가 있다/없다"를 알려주는 상태 토픽이 아니라 이벤트
    // 로그 스트림이라, 여기서도 "현재 fault"가 아니라 "최근 에러 로그"로 표현한다
    // (백엔드 _on_robot_error 주석 참고).
    if (!fault) {
      setPill(els.fault, '없음', 'ok');
      return;
    }
    const time = fault.at ? new Date(fault.at).toLocaleTimeString('ko-KR') : '';
    const text = `[${fault.level_label || fault.level}] ${fault.group_label || fault.group}`
      + ` #${fault.code}${fault.msg1 ? ': ' + fault.msg1 : ''}${time ? ' (' + time + ')' : ''}`;
    setPill(els.fault, text, FAULT_LEVEL_SEVERITY[fault.level_label] || 'unknown');
  }

  function updateCollision(collision) {
    // 백엔드가 /error 로그 중 충돌 관련 에러 코드(DRFC.h의 RC_ERROR_SAFETY_COLLISION 등)만
    // 골라 별도로 캐시해서 보내주는 값 -- Fault와 마찬가지로 "지금 충돌 중"이 아니라
    // "가장 최근에 감지된 충돌성 에러 로그"를 보여준다.
    if (!collision) {
      setPill(els.collision, '정상', 'ok');
      return;
    }
    const time = collision.at ? new Date(collision.at).toLocaleTimeString('ko-KR') : '';
    const text = `충돌 감지 #${collision.code}${collision.msg1 ? ': ' + collision.msg1 : ''}`
      + `${time ? ' (' + time + ')' : ''}`;
    setPill(els.collision, text, 'danger');
  }

  function updateComm(comm) {
    if (!comm) return;
    els.commWs.textContent = comm.ws_clients;
    els.commRate.textContent = comm.poll_period_sec ? `${(1 / comm.poll_period_sec).toFixed(1)} Hz` : '-';
    els.commDisconnect.textContent = comm.last_disconnection_at
      ? new Date(comm.last_disconnection_at).toLocaleString('ko-KR')
      : '없음';
  }

  function handleMessage(event) {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch (e) {
      return;
    }

    if (payload.type === 'manager_log') {
      appendLog(payload.text);
      return;
    }
    if (payload.type !== 'dashboard') {
      return; // USER 탭의 pen_point 등 -- MANAGER와 무관, 무시.
    }

    updateConnection(!!payload.connected);
    updateTcp(payload.tcp);
    updateJoints(payload.joints_deg);
    updateRobotState(payload.robot_state);
    updateRobotMode(payload.robot_mode);
    updateSpeedMode(payload.speed_mode);
    updateFault(payload.fault);
    updateCollision(payload.collision);
    updateComm(payload.comm);
  }

  function sendCommand(cmd) {
    const socket = window.__rokeySocket;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      appendLog('명령 전송 실패: 서버에 연결되어 있지 않음');
      return;
    }
    socket.send(JSON.stringify(cmd));
    appendLog(`명령 전송: ${cmd.manager_cmd}`);
  }

  if (els.btnStop) {
    els.btnStop.addEventListener('click', () => {
      sendCommand({ manager_cmd: 'move_stop' });
    });
  }

  if (els.btnReset) {
    els.btnReset.addEventListener('click', () => {
      sendCommand({ manager_cmd: 'reset_safe_stop' });
    });
  }

  if (els.btnEstop) {
    els.btnEstop.addEventListener('click', () => {
      sendCommand({ manager_cmd: 'servo_off_emergency' });
    });
  }

  if (els.btnSafetyReset) {
    els.btnSafetyReset.addEventListener('click', () => {
      // 정지/재개(move_pause/resume)와는 무관한, SAFE_STOP/SAFE_OFF류에서 빠져나오는
      // 별도 명령 -- 백엔드 _call_reset_safety_state 참고.
      sendCommand({ manager_cmd: 'reset_safety_state' });
    });
  }

  if (els.moveForm) {
    els.moveForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const pos = [1, 2, 3, 4, 5, 6].map((i) => {
        const input = document.getElementById(`mgr-move-j${i}`);
        return parseFloat(input.value);
      });
      if (pos.some((v) => Number.isNaN(v))) {
        appendLog('조인트 이동 취소: 6축 값을 모두 입력하세요.');
        return;
      }
      if (!confirm(`입력한 목표 각도로 조인트를 이동하시겠습니까?\n[${pos.join(', ')}]`)) return;

      const velInput = document.getElementById('mgr-move-vel');
      const accInput = document.getElementById('mgr-move-acc');
      sendCommand({
        manager_cmd: 'move_joint',
        pos,
        vel: parseFloat(velInput.value) || 30,
        acc: parseFloat(accInput.value) || 30,
      });
    });
  }

  function attach() {
    if (window.__rokeySocket) {
      window.__rokeySocket.addEventListener('message', handleMessage);
    } else {
      // main_designing.js가 소켓을 아직 안 만들었으면 잠깐 뒤 재시도.
      setTimeout(attach, 100);
    }
  }

  attach();

  // main_designing.js가 재연결할 때마다 window.__rokeySocket이 새 WebSocket
  // 인스턴스로 교체된다 -- 그 새 인스턴스에도 message 리스너를 다시 붙여야
  // dashboard/manager_log를 계속 받는다. 그리고 소켓이 끊긴 그 순간 바로
  // "연결 끊김"을 반영한다 -- 예전에는 dashboard 메시지의 connected 필드만 보고
  // 있어서, 소켓이 죽어도 마지막 값(true)에 고정된 채 안 바뀌는 문제가 있었다
  // (3D 뷰어/조인트는 안 움직이는데 Connected는 계속 true로 보이는 모순).
  window.addEventListener('rokeysocket:open', attach);
  window.addEventListener('rokeysocket:close', () => {
    updateConnection(false);
    setPill(els.state, '서버 연결 끊김', 'unknown');
    setPill(els.mode, 'Mode: 확인 안 됨', 'unknown');
  });
})();
