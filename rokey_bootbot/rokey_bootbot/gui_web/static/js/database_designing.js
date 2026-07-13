(function () {
  const listBodyEl = document.getElementById('db-list-body');
  const detailEl = document.getElementById('db-detail');
  const deleteModal = document.getElementById('db-delete-modal');
  const deleteModalText = document.getElementById('db-delete-modal-text');
  const deleteYesBtn = document.getElementById('db-delete-yes');
  const deleteNoBtn = document.getElementById('db-delete-no');

  let selectedRow = null;
  // { type: 'part_name', name } | { type: 'session', partName, sessionId }
  let pendingDelete = null;

  function formatDate(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('ko-KR');
  }

  async function loadList() {
    let names = [];
    try {
      const res = await fetch('/api/history');
      names = await res.json();
    } catch (e) {
      console.error('작성 기록 목록 조회 실패', e);
      return;
    }

    listBodyEl.innerHTML = '';
    for (const name of names) {
      const tr = document.createElement('tr');
      tr.textContent = name;
      tr.addEventListener('click', () => selectRow(tr, name));
      listBodyEl.appendChild(tr);
    }
  }

  function selectRow(tr, name) {
    if (selectedRow) selectedRow.classList.remove('selected');
    tr.classList.add('selected');
    selectedRow = tr;
    loadDetail(name);
  }

  async function loadDetail(name) {
    detailEl.innerHTML = '<p class="db-empty-hint">불러오는 중...</p>';
    let data;
    try {
      const res = await fetch(`/api/history/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (e) {
      console.error('상세 정보 조회 실패', e);
      detailEl.innerHTML = '<p class="db-empty-hint">상세 정보를 불러오지 못했습니다.</p>';
      return;
    }

    detailEl.innerHTML = '';

    const titleRow = document.createElement('div');
    titleRow.className = 'db-detail-title-row';

    const title = document.createElement('h3');
    title.textContent = `"${data.part_name}" — 세션 ${data.sessions.length}개`;
    titleRow.appendChild(title);

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'db-delete-btn';
    deleteBtn.textContent = '삭제';
    deleteBtn.addEventListener('click', () => openDeletePartNameModal(data.part_name));
    titleRow.appendChild(deleteBtn);

    detailEl.appendChild(titleRow);

    for (const session of data.sessions) {
      const box = document.createElement('div');
      box.className = 'db-session';

      const headingRow = document.createElement('div');
      headingRow.className = 'db-session-heading-row';

      const sessionShortId = session.session_id ? session.session_id.slice(0, 8) : '?';
      const startedAtText = formatDate(session.started_at);

      const heading = document.createElement('h3');
      heading.textContent = `세션 ${sessionShortId}`;
      headingRow.appendChild(heading);

      if (session.session_id) {
        const sessionDeleteBtn = document.createElement('button');
        sessionDeleteBtn.type = 'button';
        sessionDeleteBtn.className = 'db-delete-btn db-delete-btn--sm';
        sessionDeleteBtn.textContent = '삭제';
        sessionDeleteBtn.addEventListener('click', () => openDeleteSessionModal(
          data.part_name, session.session_id, sessionShortId, startedAtText));
        headingRow.appendChild(sessionDeleteBtn);
      }

      box.appendChild(headingRow);

      const meta = document.createElement('div');
      meta.className = 'db-session-meta';
      meta.textContent = `작성 시각: ${startedAtText} · 획 수: ${session.stroke_count}`;
      box.appendChild(meta);

      const table = document.createElement('table');
      table.className = 'db-stroke-table';
      table.innerHTML = `
        <thead>
          <tr><th>stroke_id</th><th>action</th><th>점 개수</th><th>갱신 시각</th></tr>
        </thead>
        <tbody></tbody>
      `;
      const tbody = table.querySelector('tbody');
      for (const stroke of session.strokes) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${stroke.stroke_id}</td>
          <td>${stroke.action}</td>
          <td>${stroke.point_count}</td>
          <td>${formatDate(stroke.updated_at)}</td>
        `;
        tbody.appendChild(tr);
      }
      box.appendChild(table);

      detailEl.appendChild(box);
    }
  }

  function openDeletePartNameModal(name) {
    pendingDelete = { type: 'part_name', name };
    deleteModalText.textContent =
      `정말 "${name}"을(를) 삭제하시겠습니까? 삭제하신 정보는 복원하실 수 없습니다.`;
    deleteModal.hidden = false;
  }

  function openDeleteSessionModal(partName, sessionId, sessionShortId, startedAtText) {
    pendingDelete = { type: 'session', partName, sessionId };
    deleteModalText.textContent =
      `"작성 시각: ${startedAtText}", "세션: ${sessionShortId}"를 삭제하시겠습니까? `
      + '삭제하신 정보는 복원하실 수 없습니다.';
    deleteModal.hidden = false;
  }

  function closeDeleteModal() {
    deleteModal.hidden = true;
    pendingDelete = null;
  }

  deleteNoBtn.addEventListener('click', closeDeleteModal);
  // 오버레이 바깥(모달 박스 밖) 클릭도 취소로 처리 -- No 버튼과 동일한 취소 동작.
  deleteModal.addEventListener('click', (event) => {
    if (event.target === deleteModal) closeDeleteModal();
  });

  deleteYesBtn.addEventListener('click', async () => {
    if (!pendingDelete) return;
    const target = pendingDelete;

    const url = target.type === 'part_name'
      ? `/api/history/${encodeURIComponent(target.name)}`
      : `/api/history/${encodeURIComponent(target.partName)}/sessions/${encodeURIComponent(target.sessionId)}`;

    let body = null;
    try {
      const res = await fetch(url, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      body = await res.json();
    } catch (e) {
      console.error('삭제 실패', e);
      closeDeleteModal();
      detailEl.innerHTML = '<p class="db-empty-hint">삭제에 실패했습니다.</p>';
      return;
    }

    closeDeleteModal();

    // part_name 전체 삭제, 또는 세션 삭제로 인해 마지막 세션까지 없어져 part_name
    // 문서 자체가 같이 정리된 경우 -- 둘 다 목록에서 글씨명이 사라지므로 동일하게 처리.
    const partNameGone = target.type === 'part_name' || body.part_name_removed;
    if (partNameGone) {
      selectedRow = null;
      detailEl.innerHTML = '<p class="db-empty-hint">왼쪽 목록에서 글씨명을 선택하세요.</p>';
      loadList();
    } else {
      // 세션 하나만 지워졌고 글씨명은 남아있으니, 같은 글씨명 상세를 새로고침한다.
      loadDetail(target.partName);
    }
  });

  loadList();
})();
