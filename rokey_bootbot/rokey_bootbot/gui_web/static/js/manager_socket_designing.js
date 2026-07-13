(function () {
  // /manager는 독립 페이지라 main_designing.js(캔버스/작성기록 등 USER 탭 전용 DOM에
  // 강하게 결합됨)를 그대로 불러올 수 없다. 이 파일은 main_designing.js의
  // connectSocket() 중 window.__rokeySocket 연결/재연결/이벤트 디스패치 부분만 떼어낸
  // 것 -- manager_designing.js는 어느 페이지에서 로드되든 window.__rokeySocket과
  // rokeysocket:open/close 이벤트만 있으면 그대로 동작한다.
  function connectSocket() {
    const socket = new WebSocket(`ws://${window.WS_HOST}:${window.WS_PORT}`);
    window.__rokeySocket = socket;

    socket.addEventListener('open', () => {
      window.dispatchEvent(new CustomEvent('rokeysocket:open'));
    });
    socket.addEventListener('close', () => {
      window.dispatchEvent(new CustomEvent('rokeysocket:close'));
      setTimeout(connectSocket, 2000);
    });
  }

  connectSocket();
})();
