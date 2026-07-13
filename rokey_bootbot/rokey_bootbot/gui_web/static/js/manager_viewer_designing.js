import * as THREE from 'https://esm.sh/three@0.160.0';
import { OrbitControls } from 'https://esm.sh/three@0.160.0/examples/jsm/controls/OrbitControls.js';
import URDFLoader from 'https://esm.sh/urdf-loader@0.12.4?deps=three@0.160.0';

// MANAGER 탭 3D 뷰어 프로토타입.
// m0609_rg2_bringup의 m0609_with_rg2.urdf.xacro를 미리 xacro로 펼치고 collision을
// 제거한 뒤 mesh 경로를 정적 파일로 바꿔둔 사본을 /static/urdf/m0609_rg2.urdf로 서빙한다
// (브라우저는 xacro도 package://도 못 읽으므로). manager_designing.js가 dashboard
// 메시지에서 뽑은 joints_deg(6축, deg)를 window.__mgrViewerSetJointsDeg로 그대로
// 넘겨받아 robot.setJointValue()에 반영한다.
//
// three/urdf-loader는 이 환경에 npm/번들러가 없어서 esm.sh CDN에서 ESM으로 가져온다.
// ?deps=three@... 로 urdf-loader 내부의 three 참조를 이 파일의 THREE와 동일 인스턴스로
// 고정한다 (안 하면 별도 THREE 모듈 인스턴스가 되어 Object3D 상속 체계가 어긋난다).
// 최초 페이지 로드시 이 CDN 스크립트를 받아오는 데에만 인터넷이 필요하고, 이후 관절값
// 스트리밍은 이미 떠 있는 자체 WebSocket(레이어 B)을 그대로 쓰므로 로봇의 유선망(레이어 A)
// 상태와는 무관하다.

(function () {
  const container = document.getElementById('mgr-3d-viewer');
  if (!container) return;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x11151c);

  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10);
  camera.position.set(0.9, 0.7, 0.9);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0.25, 0);
  controls.update();

  scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.2));
  const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
  dirLight.position.set(1, 2, 1);
  scene.add(dirLight);

  scene.add(new THREE.GridHelper(1.4, 14, 0x2a3140, 0x1a1f28));

  // dsr_controller2가 발행하는 /joint_states의 position 순서를 그대로 따른다는
  // server_connector_node.py의 가정과 동일하게, URDF 선언 순서(joint_1..joint_6)를 쓴다.
  const JOINT_NAMES = ['joint_1', 'joint_2', 'joint_3', 'joint_4', 'joint_5', 'joint_6'];

  let robot = null;
  const loader = new URDFLoader();
  loader.load(
    '/static/urdf/m0609_rg2.urdf',
    (result) => {
      robot = result;
      // URDF는 Z-up, three.js는 Y-up이라 X축으로 -90도 회전해 맞춘다.
      robot.rotation.x = -Math.PI / 2;
      scene.add(robot);
    },
    undefined,
    (err) => console.error('[mgr-3d-viewer] URDF 로드 실패:', err),
  );

  function resize() {
    const w = container.clientWidth || 1;
    const h = container.clientHeight || 1;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }
  window.addEventListener('resize', resize);
  // window의 resize 이벤트만으로는 부족하다 -- MANAGER 탭이 기본 숨김 상태(USER 탭이
  // 먼저 열려있는 것이 기본값)로 페이지가 로드되면 이 시점엔 container가
  // display:none이라 clientWidth/clientHeight가 0이고, 그래서 renderer가 1x1
  // 픽셀로 초기화된다. tabs_designing.js가 hidden 속성만 토글해서 MANAGER 탭을
  // 보여줘도 그건 실제 브라우저 resize 이벤트가 아니라 이 1x1 크기가 그대로
  // 남는다 -- 사용자가 실제로 창 크기를 바꿔야만(우연히 resize 이벤트가 발생해야만)
  // 정상 크기로 재계산됐던 게 바로 이 버그였다. ResizeObserver는 원인이 뭐든(창
  // 크기 변경이든 hidden 토글로 인한 표시든) container 자체의 실측 크기가 바뀌는
  // 순간을 직접 잡아내므로, 이 케이스를 근본적으로 해결한다.
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  resize();

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  window.__mgrViewerSetJointsDeg = function (positionsDeg) {
    if (!robot || !Array.isArray(positionsDeg)) return;
    JOINT_NAMES.forEach((name, i) => {
      const deg = positionsDeg[i];
      if (typeof deg === 'number') {
        robot.setJointValue(name, THREE.MathUtils.degToRad(deg));
      }
    });
  };
})();
