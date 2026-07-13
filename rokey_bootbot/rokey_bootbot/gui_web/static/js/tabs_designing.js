(function () {
  // USER/DATABASE는 별도 페이지가 아니라 한 페이지 안의 탭이다. 탭 버튼을 누르면
  // 페이지 이동(location.href 변경) 없이 [data-panel]을 hidden 속성으로 켜고 끄기만
  // 한다 -- WebSocket 연결(main_designing.js) 등 페이지 상태는 탭을 전환해도 그대로
  // 유지된다.
  //
  // MANAGER만 예외: 로그인 없이 접근 가능한 USER/DATABASE와 달리 관리자 인증이
  // 필요해서(사용자 요청) 이 페이지의 탭이 아니라 별도 라우트(/manager)로 옮겼다.
  // 탭 목록에는 그대로 보이되, 클릭하면 패널 전환 대신 페이지 이동을 한다.
  const tabButtons = document.querySelectorAll('.top-nav .tab-btn');
  const panels = document.querySelectorAll('[data-panel]');

  function activate(tabName) {
    tabButtons.forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.panel !== tabName;
    });
  }

  tabButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.tab === 'manager') {
        location.href = '/manager';
        return;
      }
      activate(btn.dataset.tab);
    });
  });

  // #user/#database/#manager로 특정 탭을 바로 열 수 있게 한다 (딥링크/스크린샷 검증용).
  // 해시가 없거나 알 수 없는 값이면 HTML에 이미 표시된 기본 탭(active 클래스)을 그대로 둔다.
  const initialTab = location.hash.replace('#', '');
  if (initialTab && document.querySelector(`[data-panel="${initialTab}"]`)) {
    activate(initialTab);
  }
})();
